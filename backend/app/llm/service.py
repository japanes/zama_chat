"""complete(role, messages, schema?) → LLMResult.

The caller names a role, not a model: the model, reasoning effort, timeout and
fallback come from `model_roles` (llm/registry.py). Every attempt is written to
`llm_calls` with tokens, cost and the Gateway generation id.

Not called gateway.py on purpose — not to confuse it with the Vercel AI Gateway.
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.db.models import LlmCall, LlmRole
from app.errors import AppError
from app.llm.openai_client import ChatBackend, LLMRequestError, LLMTransportError
from app.llm.pricing import cost_from_prices, cost_from_response
from app.llm.registry import ModelSpec, RoleConfig, get_model, resolve_role
from app.logging import get_logger

log = get_logger(__name__)


class LLMBadOutput(Exception):
    """The model answered, but not with valid JSON for the schema."""


@dataclass
class LLMResult:
    text: str
    data: dict[str, Any] | None
    model: str
    role: str
    tokens_in: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0
    cost: Decimal = Decimal(0)
    latency_ms: int = 0
    generation_id: str | None = None
    call_id: uuid.UUID | None = None
    fallback_used: bool = False
    attempts: int = 1
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Overrides:
    """Per-call overrides (sandbox, `make eval`) — DB settings stay untouched."""

    model: str | None = None
    reasoning_effort: str | None = None


class LLMService:
    def __init__(
        self,
        sm: async_sessionmaker[AsyncSession],
        backend: ChatBackend,
        clock: Clock,
        *,
        attempts_per_model: int = 3,  # 1 call + 2 retries
        retry_delay_s: float = 1.0,
    ) -> None:
        self.sm = sm
        self.backend = backend
        self.clock = clock
        self.attempts_per_model = attempts_per_model
        self.retry_delay_s = retry_delay_s

    async def role_config(
        self, role: LlmRole | str, tenant_id: uuid.UUID | None, overrides: Overrides | None = None
    ) -> RoleConfig:
        async with self.sm() as session:
            config = await resolve_role(session, role, tenant_id)
            if overrides and overrides.model:
                model = await get_model(session, overrides.model)
                config = RoleConfig(
                    role=config.role,
                    model=ModelSpec.from_row(model),
                    reasoning_effort=config.reasoning_effort,
                    timeout_s=config.timeout_s,
                    fallback=None,
                    tenant_override=config.tenant_override,
                )
        if overrides and overrides.reasoning_effort is not None:
            config = RoleConfig(
                role=config.role,
                model=config.model,
                reasoning_effort=overrides.reasoning_effort or None,
                timeout_s=config.timeout_s,
                fallback=config.fallback,
                tenant_override=config.tenant_override,
            )
        return config

    async def complete(
        self,
        role: LlmRole | str,
        messages: list[dict[str, Any]],
        *,
        tenant_id: uuid.UUID | None,
        turn_id: uuid.UUID | None = None,
        schema: dict[str, Any] | None = None,
        schema_name: str = "result",
        strict: bool = True,
        prompt_key: str | None = None,
        prompt_version: int | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        overrides: Overrides | None = None,
    ) -> LLMResult:
        config = await self.role_config(role, tenant_id, overrides)
        models: list[tuple[ModelSpec, bool]] = [(config.model, False)]
        if config.fallback is not None:
            models.append((config.fallback, True))

        last_error = ""
        attempt_no = 0
        for spec, is_fallback in models:
            if schema is not None and not spec.supports_json_schema:
                last_error = f"{spec.name}: json schema not supported"
                continue
            request = self._build_request(
                spec,
                messages,
                effort=config.reasoning_effort,
                schema=schema,
                schema_name=schema_name,
                strict=strict,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            for i in range(self.attempts_per_model):
                attempt_no += 1
                started = time.monotonic()
                raw: dict[str, Any] | None = None
                try:
                    raw = await self.backend.chat(request, timeout=config.timeout_s)
                    result = self._parse(raw, spec, config.role, schema is not None)
                except (LLMTransportError, LLMBadOutput) as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    await self._record(
                        tenant_id,
                        turn_id,
                        config.role,
                        spec,
                        prompt_key,
                        prompt_version,
                        request,
                        raw,
                        None,
                        started,
                        attempt_no,
                        last_error,
                    )
                    log.warning(
                        "llm.attempt_failed",
                        role=config.role,
                        model=spec.name,
                        attempt=attempt_no,
                        error=last_error,
                    )
                    if i + 1 < self.attempts_per_model:
                        await asyncio.sleep(self.retry_delay_s * (i + 1))
                    continue
                except LLMRequestError as exc:
                    last_error = f"{type(exc).__name__}: {exc}"
                    await self._record(
                        tenant_id,
                        turn_id,
                        config.role,
                        spec,
                        prompt_key,
                        prompt_version,
                        request,
                        None,
                        None,
                        started,
                        attempt_no,
                        last_error,
                    )
                    log.error(
                        "llm.request_rejected", role=config.role, model=spec.name, error=last_error
                    )
                    break  # same request to the same model will fail again → fallback
                result.fallback_used = is_fallback
                result.attempts = attempt_no
                result.call_id = await self._record(
                    tenant_id,
                    turn_id,
                    config.role,
                    spec,
                    prompt_key,
                    prompt_version,
                    request,
                    raw,
                    result,
                    started,
                    attempt_no,
                    None,
                )
                return result

        raise AppError(
            "llm.unavailable", {"role": config.role, "error": last_error[:300]}, status=503
        )

    async def embed(
        self, texts: list[str], *, tenant_id: uuid.UUID | None, timeout: float | None = None
    ) -> tuple[list[list[float]], LLMResult]:
        config = await self.role_config(LlmRole.EMBED, tenant_id)
        started = time.monotonic()
        raw = await self.backend.embed(
            config.model.name, texts, timeout=timeout or config.timeout_s
        )
        vectors = [item["embedding"] for item in sorted(raw["data"], key=lambda d: d["index"])]
        usage = raw.get("usage") or {}
        tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
        cost = cost_from_response(raw) or cost_from_prices(
            price_input=config.model.price_input,
            price_cached_input=None,
            price_output=Decimal(0),
            tokens_in=tokens,
            tokens_cached=0,
            tokens_out=0,
        )
        result = LLMResult(
            text="",
            data=None,
            model=config.model.name,
            role=config.role,
            tokens_in=tokens,
            cost=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return vectors, result

    # --- helpers -------------------------------------------------------------------

    @staticmethod
    def _build_request(
        spec: ModelSpec,
        messages: list[dict[str, Any]],
        *,
        effort: str | None,
        schema: dict[str, Any] | None,
        schema_name: str,
        strict: bool,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"model": spec.name, "messages": messages}
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": schema, "strict": strict},
            }
        if effort and spec.supports_reasoning:
            # Gateway format: {"reasoning": {"effort": "low"}} (passed via extra_body)
            request["reasoning"] = {"effort": effort}
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if temperature is not None:
            request["temperature"] = temperature
        return request

    @staticmethod
    def _parse(raw: dict[str, Any], spec: ModelSpec, role: str, want_json: bool) -> LLMResult:
        choices = raw.get("choices") or []
        if not choices:
            raise LLMBadOutput("no choices in response")
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        data = None
        if want_json:
            try:
                data = json.loads(text)
            except (TypeError, ValueError) as exc:
                raise LLMBadOutput(f"invalid JSON: {text[:200]!r}") from exc
            if not isinstance(data, dict):
                raise LLMBadOutput("JSON is not an object")
        elif not text.strip():
            raise LLMBadOutput("empty answer")
        usage = raw.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        tokens_in = int(usage.get("prompt_tokens") or 0)
        tokens_cached = int(details.get("cached_tokens") or 0)
        tokens_out = int(usage.get("completion_tokens") or 0)
        cost = cost_from_response(raw)
        if cost is None:
            cost = cost_from_prices(
                price_input=spec.price_input,
                price_cached_input=spec.price_cached_input,
                price_output=spec.price_output,
                tokens_in=tokens_in,
                tokens_cached=tokens_cached,
                tokens_out=tokens_out,
            )
        return LLMResult(
            text=text,
            data=data,
            model=spec.name,
            role=role,
            tokens_in=tokens_in,
            tokens_cached=tokens_cached,
            tokens_out=tokens_out,
            cost=cost,
            generation_id=raw.get("id"),
            raw=raw,
        )

    async def _record(
        self,
        tenant_id: uuid.UUID | None,
        turn_id: uuid.UUID | None,
        role: str,
        spec: ModelSpec,
        prompt_key: str | None,
        prompt_version: int | None,
        request: dict[str, Any],
        raw: dict[str, Any] | None,
        result: LLMResult | None,
        started: float,
        attempt: int,
        error: str | None,
    ) -> uuid.UUID:
        latency_ms = int((time.monotonic() - started) * 1000)
        if result is not None:
            result.latency_ms = latency_ms
        call = LlmCall(
            tenant_id=tenant_id,
            turn_id=turn_id,
            role=role,
            model=spec.name,
            prompt_key=prompt_key,
            prompt_version=prompt_version,
            request=request,
            response=raw,
            tokens_in=result.tokens_in if result else 0,
            tokens_cached=result.tokens_cached if result else 0,
            tokens_out=result.tokens_out if result else 0,
            cost=result.cost if result else Decimal(0),
            latency_ms=latency_ms,
            generation_id=result.generation_id if result else (raw or {}).get("id"),
            attempt=attempt,
            error=error,
            finished_at=self.clock.now(),
        )
        try:
            async with self.sm() as session, session.begin():
                session.add(call)
        except Exception:  # the log must never break the turn
            log.exception("llm.record_failed", role=role, model=spec.name)
        return call.id
