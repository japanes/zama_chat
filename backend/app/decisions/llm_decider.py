"""Fallback decider: the questions go to the `decide` role model in one JSON-schema call.

Used for Jev answers below the threshold and when Jev fails. The model writes its own
confidence (0..1); it is not calibrated like Jev's, so it is clamped and logged as is.

Questions are renamed q1..qN in the request: keys like "start:booking.wants_booking" are
not valid schema property names everywhere, and short names keep the schema small.
"""

import json
import uuid
from typing import Any

from app.db.models import DecisionProvider, DecisionReason, DecisionType, LlmRole
from app.decisions.engine import Answer, DecideContext, DecisionSet, Question, decision_row
from app.errors import AppError
from app.llm.service import LLMService

PROMPT_KEY = "decide"

SYSTEM = (
    "You answer questions about a dialogue state. For every question pick the value that "
    "matches its criteria best and give your confidence from 0 to 1 "
    "(1 = certain, 0.5 = a coin toss). Answer only with JSON that matches the schema."
)


def _value_schema(question: Question) -> dict[str, Any]:
    if question.type == DecisionType.BOOLEAN:
        return {"type": "boolean"}
    if question.type == DecisionType.SCORE:
        return {"type": "integer", "enum": [int(k) for k in question.criteria]}
    return {"type": "string", "enum": question.options}


def build_schema(names: dict[str, Question]) -> dict[str, Any]:
    properties = {
        name: {
            "type": "object",
            "properties": {"value": _value_schema(q), "confidence": {"type": "number"}},
            "required": ["value", "confidence"],
            "additionalProperties": False,
        }
        for name, q in names.items()
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(names),
        "additionalProperties": False,
    }


def build_messages(state: str, names: dict[str, Question]) -> list[dict[str, str]]:
    questions = {
        name: {"type": str(q.type), "instructions": q.instructions, "criteria": dict(q.criteria)}
        for name, q in names.items()
    }
    user = json.dumps({"state": state, "questions": questions}, ensure_ascii=False, indent=1)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)


class LLMDecider:
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    async def decide(
        self,
        state: str,
        questions: list[Question],
        ctx: DecideContext,
        *,
        reason: DecisionReason = DecisionReason.PRIMARY,
    ) -> DecisionSet:
        if not questions:
            return DecisionSet()
        names = {f"q{i}": q for i, q in enumerate(questions, start=1)}
        result = await self.llm.complete(
            LlmRole.DECIDE,
            build_messages(state, names),
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            schema=build_schema(names),
            schema_name="decisions",
            prompt_key=PROMPT_KEY,
            overrides=ctx.overrides,
        )
        data = result.data or {}
        decided = DecisionSet()
        batch_id = uuid.uuid4()
        for index, (name, question) in enumerate(names.items()):
            item = data.get(name)
            raw_value = item.get("value") if isinstance(item, dict) else None
            value = question.coerce(raw_value)
            if value is None:
                # strict schema makes this rare; a wrong answer must not be used silently
                raise AppError(
                    "decisions.bad_output",
                    {"key": question.key, "value": json.dumps(raw_value, ensure_ascii=False)},
                )
            answer = Answer(
                key=question.key,
                type=question.type,
                value=value,
                confidence=_clamp(item.get("confidence")),
                provider=DecisionProvider.LLM,
                model=result.model,
                reason=reason,
            )
            decided.answers[question.key] = answer
            first = index == 0  # call cost and tokens go on the first row only
            decided.rows.append(
                decision_row(
                    question,
                    ctx=ctx,
                    batch_id=batch_id,
                    provider=DecisionProvider.LLM,
                    model=result.model,
                    reason=reason,
                    answer=answer,
                    raw={"confidence": item.get("confidence")},
                    latency_ms=result.latency_ms if first else 0,
                    tokens_in=result.tokens_in if first else 0,
                    tokens_out=result.tokens_out if first else 0,
                    # also in llm_calls; turn totals count only jev rows, so no double sum
                    cost=result.cost if first else 0,
                    generation_id=result.generation_id,
                    llm_call_id=result.call_id,
                )
            )
        return decided
