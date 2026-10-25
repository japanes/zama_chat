"""Main decider: Jev through the Vercel AI Gateway, `POST {gateway}/evaluate`.

- the questions are split into requests by the Jev limits (limits.py), sent in parallel;
- confidence: boolean → max(p, 1−p), choice / score → probability of the pick;
- 400-like errors are our bug: no retry, a loud log;
- 5xx / 429 / 408 / network / timeout: one retry, then the fallback LLM (reason=jev_error);
- cost, tokens and generationId go to the log (cost on the first row of a request).

Per question the policy (decisions/policy.py, rules from the admin) says:
- mode jev: Jev answers; below the rule threshold — `below`:
  safe (no rule: the default) → no LLM, the careful reading of Jev's answer
  (Question.careful: «answered in full» → «in part»; a value without one is no answer,
  as ask_client; reason=unsure),
  llm → the unsure questions go to the fallback LLM in one call (reason=below_threshold;
  if that call fails, the Jev answer is kept), keep → Jev's answer is used anyway,
  ask_client / operator → the answer is marked unsure: callers get their default and the
  bot asks the client again (operator: and a person picks this turn's reply);
- mode llm: the LLM answers alone (if it fails — Jev, reason=llm_error);
- mode shadow: as jev, and the LLM answers the same questions at the same time; its
  rows are logged with reason=shadow, used=false — for the agreement stats. A shadow
  answer is reused when Jev is unsure or fails (no second LLM call).
LLM confidence is not calibrated: the thresholds apply to Jev answers only.

The Gateway key is sent in the header only; it never reaches logs or rows.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.db.models import BelowAction, DecisionProvider, DecisionReason, PolicyMode
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
from app.decisions.policy import DecisionPolicy, Rule
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
        policy = ctx.policy or self.policy
        rules = {q.key: policy.rule(q.key) for q in questions}
        to_jev = [q for q in questions if rules[q.key].mode != PolicyMode.LLM]
        to_llm = [q for q in questions if rules[q.key].mode != PolicyMode.JEV]

        batches = split_batches(state, to_jev, self.limits) if to_jev else []
        asked, llm = await asyncio.gather(
            asyncio.gather(*(self._ask(state, b, ctx) for b in batches)),
            self._ask_llm(state, to_llm, rules, ctx),
        )

        result = DecisionSet()
        again: dict[str, DecisionReason] = {}
        for batch in asked:
            result.rows.extend(batch.rows)
        if llm is not None:
            result.rows.extend(llm.rows)
        shadow = llm.answers if llm is not None else {}

        # mode llm: the LLM answer; the LLM failed → Jev
        lost = [q for q in to_llm if rules[q.key].mode == PolicyMode.LLM and q.key not in shadow]
        for question in to_llm:
            if rules[question.key].mode == PolicyMode.LLM and question.key in shadow:
                result.answers[question.key] = shadow[question.key]
        if lost:
            await self._ask_jev_instead(state, lost, ctx, result)

        for batch in asked:
            for question in batch.questions:
                key = question.key
                rule = rules[key]
                answer = batch.answers.get(key)
                if answer is None:
                    if key in shadow:
                        self._use_llm(result, shadow[key], DecisionReason.JEV_ERROR)
                    else:
                        again[key] = DecisionReason.JEV_ERROR
                    continue
                result.answers[key] = answer
                if answer.confidence >= rule.threshold or rule.below == BelowAction.KEEP:
                    continue
                careful = question.careful(answer.value)
                if rule.below == BelowAction.SAFE and careful is not None:
                    answer.value = careful
                    answer.reason = DecisionReason.UNSURE
                    row = _row(result.rows, key, DecisionProvider.JEV)
                    if row is not None:
                        row.reason = str(DecisionReason.UNSURE)
                elif rule.below in (BelowAction.SAFE, BelowAction.ASK_CLIENT, BelowAction.OPERATOR):
                    answer.unsure = True
                    answer.below = str(rule.below)
                    answer.reason = DecisionReason.UNSURE
                    row = _row(result.rows, key, DecisionProvider.JEV)
                    if row is not None:
                        row.reason = str(DecisionReason.UNSURE)
                        row.used = False
                elif key in shadow:
                    self._use_llm(result, shadow[key], DecisionReason.BELOW_THRESHOLD)
                else:
                    again[key] = DecisionReason.BELOW_THRESHOLD
        if again:
            await self._ask_fallback(
                state, [q for q in questions if q.key in again], again, ctx, result
            )
        return result

    async def _ask_llm(
        self,
        state: str,
        questions: list[Question],
        rules: dict[str, Rule],
        ctx: DecideContext,
    ) -> DecisionSet | None:
        """The LLM part of modes llm and shadow: one call; None when it fails."""
        if not questions or self.fallback is None:
            return None
        try:
            answered = await self.fallback.decide(state, questions, ctx)
        except AppError as exc:
            log.warning("decisions.llm_failed", code=exc.code, keys=[q.key for q in questions])
            return None
        for row in answered.rows:
            if rules[row.key].mode == PolicyMode.SHADOW:
                row.reason = str(DecisionReason.SHADOW)
                row.used = False
        for key, answer in answered.answers.items():
            if rules[key].mode == PolicyMode.SHADOW:
                answer.reason = DecisionReason.SHADOW
        return answered

    async def _ask_jev_instead(
        self, state: str, questions: list[Question], ctx: DecideContext, result: DecisionSet
    ) -> None:
        """Mode llm and the LLM failed: Jev answers, whatever its confidence."""
        batches = split_batches(state, questions, self.limits)
        for batch in await asyncio.gather(*(self._ask(state, b, ctx) for b in batches)):
            for row in batch.rows:
                row.reason = str(DecisionReason.LLM_ERROR)
            for answer in batch.answers.values():
                answer.reason = DecisionReason.LLM_ERROR
            result.rows.extend(batch.rows)
            result.answers.update(batch.answers)
        missing = [q.key for q in questions if q.key not in result.answers]
        if missing:
            raise DecisionsUnavailable(missing, result.rows)

    @staticmethod
    def _use_llm(result: DecisionSet, answer: Answer, reason: DecisionReason) -> None:
        """A shadow LLM answer replaces Jev's: Jev is unsure or failed."""
        answer.reason = reason
        result.answers[answer.key] = answer
        for row in result.rows:
            if row.key != answer.key:
                continue
            if row.provider == DecisionProvider.LLM:
                row.used = True
                row.reason = str(reason)
            elif row.provider == DecisionProvider.JEV:
                row.used = False

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


def _row(rows: list[Any], key: str, provider: DecisionProvider) -> Any:
    return next((r for r in rows if r.key == key and r.provider == provider), None)
