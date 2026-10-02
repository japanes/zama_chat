"""Main decider: Jev through the Vercel AI Gateway, `POST {gateway}/evaluate`.

- the questions are split into requests by the Jev limits (limits.py), sent in parallel;
- confidence: boolean → max(p, 1−p), choice / score → probability of the pick;
- 400-like errors are our bug: no retry, a loud log;
- 5xx / 429 / 408 / network / timeout: one retry, then the fallback LLM (reason=jev_error);
- below the policy threshold only the unsure questions go to the fallback, in one call
  (reason=below_threshold); if that call fails, the Jev answer is kept;
- cost, tokens and generationId go to the log (cost on the first row of a request).

The Gateway key is sent in the header only; it never reaches logs or rows.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.db.models import DecisionProvider, DecisionReason
from app.decisions.engine import (
    Answer,
    DecideContext,
    DecisionSet,
    DecisionsUnavailable,
    Question,
    decision_row,
)
from app.decisions.limits import Limits, split_batches
from app.decisions.llm_decider import LLMDecider
from app.decisions.policy import DecisionPolicy
from app.errors import AppError
from app.logging import get_logger
from app.settings import Settings

log = get_logger(__name__)

RETRY_STATUSES = {408, 429}


@dataclass
class JevCall:
    status: int  # 0 = no HTTP answer (network / timeout)
    data: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    error: str = ""
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def retryable(self) -> bool:
        return self.status == 0 or self.status >= 500 or self.status in RETRY_STATUSES


@dataclass
class JevBatch:
    questions: list[Question]
    answers: dict[str, Answer] = field(default_factory=dict)
    rows: list[Any] = field(default_factory=list)


def parse_answer(raw: dict[str, Any]) -> tuple[Any, float, dict[str, float]]:
    """(value, confidence, probabilities); value None when the answer has nothing usable."""
    probabilities = {
        str(k): float(v)
        for k, v in (raw.get("probabilities") or {}).items()
        if isinstance(v, int | float)
    }
    if "probability" in raw:
        p = float(raw["probability"])
        return p >= 0.5, max(p, 1 - p), probabilities
    for kind in ("choice", "score"):
        if kind in raw and raw[kind] is not None:
            value = raw[kind]
            return value, probabilities.get(str(value), 0.0), probabilities
    return None, 0.0, probabilities


def _tokens(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int | float):
            return int(value)
    return 0


def _cost(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value is not None else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


class JevDecider:
    provider = DecisionProvider.JEV

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        *,
        fallback: LLMDecider | None = None,
        policy: DecisionPolicy | None = None,
        limits: Limits | None = None,
        retry_delay_s: float = 0.5,
    ) -> None:
        self.http = http
        self.url = settings.ai_gateway_url.rstrip("/") + "/evaluate"
        self.model = settings.jev_model
        self.zero_data_retention = settings.jev_zero_data_retention
        self.timeout_s = settings.jev_timeout_s
        self._api_key = settings.ai_gateway_api_key
        self.fallback = fallback
        self.policy = policy or DecisionPolicy()
        self.limits = limits or Limits()
        self.retry_delay_s = retry_delay_s

    # --- HTTP -------------------------------------------------------------------------

    def request_body(self, state: str, questions: list[Question]) -> dict[str, Any]:
        return {
            "model": self.model,
            "state": state,
            "questions": {q.key: q.to_jev() for q in questions},
            "providerOptions": {
                "gateway": {
                    "only": [self.model.split("/", 1)[0]],
                    "zeroDataRetention": self.zero_data_retention,
                }
            },
        }

    async def _post(self, body: dict[str, Any]) -> JevCall:
        headers = {"Authorization": f"Bearer {self._api_key.get_secret_value()}"}
        started = time.monotonic()
        try:
            response = await self.http.post(
                self.url, json=body, headers=headers, timeout=self.timeout_s
            )
        except httpx.HTTPError as exc:
            return JevCall(
                status=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=type(exc).__name__,
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        error = ""
        if response.status_code != 200:
            err = data.get("error")
            message = err.get("message") if isinstance(err, dict) else response.text
            error = f"{response.status_code}: {message}"[:300]
        return JevCall(response.status_code, data, latency_ms, error)

    async def evaluate(self, state: str, questions: list[Question]) -> JevCall:
        """One request with one retry for temporary failures."""
        body = self.request_body(state, questions)
        call = await self._post(body)
        if call.ok:
            return call
        if not call.retryable:
            log.error(
                "jev.request_rejected",
                status=call.status,
                error=call.error,
                keys=[q.key for q in questions],
            )
            return call
        log.warning("jev.retry", status=call.status, error=call.error)
        if self.retry_delay_s:
            await asyncio.sleep(self.retry_delay_s)
        second = await self._post(body)
        second.attempts = 2
        second.latency_ms += call.latency_ms
        if not second.ok:
            log.warning("jev.failed", status=second.status, error=second.error)
        return second

    # --- one request → rows -----------------------------------------------------------

    async def _ask(self, state: str, questions: list[Question], ctx: DecideContext) -> JevBatch:
        call = await self.evaluate(state, questions)
        batch = JevBatch(questions)
        batch_id = uuid.uuid4()
        usage = call.data.get("usage") or {} if call.ok else {}
        gateway = (
            ((call.data.get("providerMetadata") or {}).get("gateway") or {}) if call.ok else {}
        )
        generation_id = gateway.get("generationId")
        cost = _cost(gateway.get("cost"))
        answers = call.data.get("answers") or {} if call.ok else {}
        if call.ok:
            log.info(
                "jev.answered",
                questions=len(questions),
                latency_ms=call.latency_ms,
                cost=str(cost),
                generation_id=generation_id,
            )
        for index, question in enumerate(questions):
            first = index == 0
            usage_fields = {
                "latency_ms": call.latency_ms,
                "tokens_in": _tokens(usage, "inputTokens", "input_tokens", "prompt_tokens")
                if first
                else 0,
                "tokens_out": _tokens(usage, "outputTokens", "output_tokens", "completion_tokens")
                if first
                else 0,
                "cost": cost if first else Decimal(0),
                "generation_id": generation_id,
            }
            raw = answers.get(question.key)
            answer = None
            error = call.error or None
            if call.ok:
                value, confidence, probabilities = (
                    parse_answer(raw) if isinstance(raw, dict) else (None, 0.0, {})
                )
                value = question.coerce(value) if value is not None else None
                if value is None:
                    error = "no usable answer"
                    log.warning("jev.no_answer", key=question.key)
                else:
                    answer = Answer(
                        key=question.key,
                        type=question.type,
                        value=value,
                        confidence=confidence,
                        provider=DecisionProvider.JEV,
                        model=self.model,
                        probabilities=probabilities,
                    )
                    batch.answers[question.key] = answer
            batch.rows.append(
                decision_row(
                    question,
                    ctx=ctx,
                    batch_id=batch_id,
                    provider=DecisionProvider.JEV,
                    model=self.model,
                    answer=answer,
                    raw=raw if isinstance(raw, dict) else None,
                    used=answer is not None,
                    error=error,
                    **usage_fields,
                )
            )
        return batch

    # --- the Decider protocol ---------------------------------------------------------

    async def decide(
        self, state: str, questions: list[Question], ctx: DecideContext
    ) -> DecisionSet:
        if not questions:
            return DecisionSet()
        batches = split_batches(state, questions, self.limits)
        asked = await asyncio.gather(*(self._ask(state, b, ctx) for b in batches))

        result = DecisionSet()
        again: dict[str, DecisionReason] = {}
        for batch in asked:
            result.rows.extend(batch.rows)
            for question in batch.questions:
                answer = batch.answers.get(question.key)
                if answer is None:
                    again[question.key] = DecisionReason.JEV_ERROR
                    continue
                result.answers[question.key] = answer
                if not self.policy.is_sure(question.key, answer.confidence):
                    again[question.key] = DecisionReason.BELOW_THRESHOLD
        if again:
            await self._ask_fallback(
                state, [q for q in questions if q.key in again], again, ctx, result
            )
        return result

    async def _ask_fallback(
        self,
        state: str,
        questions: list[Question],
        reasons: dict[str, DecisionReason],
        ctx: DecideContext,
        result: DecisionSet,
    ) -> None:
        second: DecisionSet | None = None
        if self.fallback is not None:
            try:
                second = await self.fallback.decide(state, questions, ctx)
            except AppError as exc:
                log.warning("decisions.fallback_failed", code=exc.code, keys=list(reasons))
        if second is not None:
            for key, answer in second.answers.items():
                answer.reason = reasons[key]
                result.answers[key] = answer
            for row in second.rows:
                row.reason = str(reasons[row.key])
            replaced = set(second.answers)
            for row in result.rows:
                if row.provider == DecisionProvider.JEV and row.key in replaced:
                    row.used = False
            result.rows.extend(second.rows)
        missing = [k for k in reasons if k not in result.answers]
        if missing:
            # below-threshold questions keep the Jev answer; only failed ones are missing
            raise DecisionsUnavailable(missing, result.rows)
