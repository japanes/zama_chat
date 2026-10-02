"""Questions, answers and the Decider protocol.

A question is the Jev format as is: type + instructions + criteria. For boolean the
criteria keys are "true" / "false", for choice — the option keys, for score — "1".."N".
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from app.db.base import new_id
from app.db.models import Decision, DecisionProvider, DecisionReason, DecisionType
from app.errors import AppError
from app.llm.service import Overrides
from app.logging import get_logger

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)


@dataclass(frozen=True)
class Question:
    key: str
    type: DecisionType
    instructions: str
    criteria: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.type == DecisionType.BOOLEAN and set(self.criteria) != {"true", "false"}:
            raise ValueError(f"boolean question {self.key!r} needs criteria true/false")
        if not self.criteria:
            raise ValueError(f"question {self.key!r} has no criteria")

    @property
    def options(self) -> list[str]:
        return list(self.criteria)

    def to_jev(self) -> dict[str, Any]:
        return {
            "type": str(self.type),
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }

    def coerce(self, value: Any) -> Any:
        """A raw value → the question's value type; None when it does not fit."""
        if self.type == DecisionType.BOOLEAN:
            if isinstance(value, bool):
                return value
            if isinstance(value, str) and value.lower() in ("true", "false"):
                return value.lower() == "true"
            return None
        if self.type == DecisionType.SCORE:
            try:
                score = int(value)
            except (TypeError, ValueError):
                return None
            return score if str(score) in self.criteria else None
        value = str(value) if value is not None else None
        return value if value in self.criteria else None


@dataclass
class Answer:
    key: str
    type: DecisionType
    value: Any  # bool for boolean, option key for choice, int for score
    confidence: float  # 0..1, see jev_decider.parse_answer
    provider: DecisionProvider
    model: str
    reason: DecisionReason = DecisionReason.PRIMARY
    probabilities: dict[str, float] = field(default_factory=dict)
    decision_id: uuid.UUID | None = None


@dataclass
class DecisionSet:
    answers: dict[str, Answer] = field(default_factory=dict)
    # rows for the decisions log; written by run_decisions()
    rows: list[Decision] = field(default_factory=list)

    def value(self, key: str, default: Any = None) -> Any:
        answer = self.answers.get(key)
        return answer.value if answer is not None else default

    def merge(self, other: "DecisionSet") -> "DecisionSet":
        self.answers.update(other.answers)
        self.rows.extend(other.rows)
        return self


class DecisionsUnavailable(AppError):
    """No decider answered some questions. Carries the rows already made, so the failed
    attempts are still logged."""

    status = 503

    def __init__(self, keys: list[str], rows: list[Decision]) -> None:
        super().__init__("decisions.unavailable", {"keys": keys})
        self.rows = rows


@dataclass(frozen=True)
class DecideContext:
    tenant_id: uuid.UUID | None
    turn_id: uuid.UUID | None = None
    overrides: Overrides | None = None


class Decider(Protocol):
    async def decide(
        self, state: str, questions: list[Question], ctx: DecideContext
    ) -> DecisionSet: ...


def decision_row(
    question: Question,
    *,
    ctx: DecideContext,
    batch_id: uuid.UUID,
    provider: DecisionProvider,
    model: str,
    reason: DecisionReason = DecisionReason.PRIMARY,
    answer: Answer | None = None,
    raw: dict[str, Any] | None = None,
    used: bool = True,
    error: str | None = None,
    **usage: Any,
) -> Decision:
    stored: dict[str, Any] | None = None
    if answer is not None:
        stored = {"value": answer.value, **(raw or {})}
        if answer.probabilities and "probabilities" not in stored:
            stored["probabilities"] = answer.probabilities
    row = Decision(
        id=new_id(),
        tenant_id=ctx.tenant_id,
        turn_id=ctx.turn_id,
        batch_id=batch_id,
        key=question.key,
        type=str(question.type),
        question=question.to_jev(),
        answer=stored,
        confidence=answer.confidence if answer is not None else None,
        provider=str(provider),
        model=model,
        reason=str(reason),
        used=used,
        error=error,
        **usage,
    )
    if answer is not None:
        answer.decision_id = row.id
    return row


def question_keys_unique(questions: list[Question]) -> None:
    keys = [q.key for q in questions]
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate question keys: {sorted(k for k in keys if keys.count(k) > 1)}")


async def run_decisions(
    services: "Services",
    state: str,
    questions: list[Question],
    ctx: DecideContext,
    *,
    trace_step: dict[str, Any] | None = None,
) -> DecisionSet:
    """Ask the configured decider (services.extra["decider"]) and log every answer.

    The log is written in its own short transaction right away — like llm_calls, it stays
    even when the turn fails later."""
    from app.decisions.log import save_decisions

    if not questions:
        return DecisionSet()
    question_keys_unique(questions)
    decider: Decider = services.extra["decider"]
    try:
        result = await decider.decide(state, questions, ctx)
    except DecisionsUnavailable as exc:
        await save_decisions(services.sm, exc.rows)
        raise
    await save_decisions(services.sm, result.rows)
    missing = [q.key for q in questions if q.key not in result.answers]
    if missing:
        log.error("decisions.missing_answers", keys=missing)
    if trace_step is not None:
        trace_step.setdefault("decision_ids", []).extend(
            str(row.id) for row in result.rows if row.used
        )
    return result


def trace_answers(answers: Mapping[str, Answer]) -> dict[str, Any]:
    """Compact view for the turn trace: key → value, confidence, provider."""
    return {
        key: {"value": a.value, "confidence": round(a.confidence, 4), "provider": str(a.provider)}
        for key, a in answers.items()
    }
