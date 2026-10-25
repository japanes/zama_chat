"""Questions, answers and the Decider protocol.

A question is the Jev format as is: type + instructions + criteria. For boolean the
criteria keys are "true" / "false", for choice — the option keys, for score — "1".."N".

A state too long for one request is asked in parts (core/state_render.py): every part is a
request of its own and the answers are put together by merge_parts().
"""

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol

from app.db.base import new_id
from app.db.models import BelowAction, Decision, DecisionProvider, DecisionReason, DecisionType
from app.decisions.policy import DecisionPolicy, load_policy
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
    # the «nothing of this here» option of a choice: what a part of a split message
    # answers when the question is about another part (a boolean has it: false)
    neutral: str | None = None
    # options that describe a message as a whole («a bare greeting»): a part of a split
    # message is never the whole message, so in a part they count as neutral
    whole_only: frozenset[str] = frozenset()
    # BelowAction.SAFE: Jev's unsure value → the value it is taken as («answered in full»
    # → «in part»); a value not named here is no answer: callers get their default
    unsure_as: Mapping[Any, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type == DecisionType.BOOLEAN and set(self.criteria) != {"true", "false"}:
            raise ValueError(f"boolean question {self.key!r} needs criteria true/false")
        if not self.criteria:
            raise ValueError(f"question {self.key!r} has no criteria")
        if self.neutral is not None and self.neutral not in self.criteria:
            raise ValueError(f"question {self.key!r}: neutral {self.neutral!r} is no option")
        if not self.whole_only <= set(self.criteria):
            raise ValueError(f"question {self.key!r}: whole_only names no option")

    @property
    def neutral_value(self) -> Any:
        if self.type == DecisionType.BOOLEAN:
            return False
        return self.neutral

    def careful(self, value: Any) -> Any:
        """What an unsure answer is taken as under BelowAction.SAFE; None — no answer."""
        return self.unsure_as.get(value)

    def says_nothing(self, value: Any) -> bool:
        """The value a part answers when it has nothing on the question."""
        if self.type == DecisionType.BOOLEAN:
            return value is False
        return value is not None and (value == self.neutral or value in self.whole_only)

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
    # below the threshold and the rule says «no answer» (ask_client / operator): the
    # callers get their default — the bot asks the client again
    unsure: bool = False
    below: str | None = None


@dataclass
class DecisionSet:
    answers: dict[str, Answer] = field(default_factory=dict)
    # rows for the decisions log; written by run_decisions()
    rows: list[Decision] = field(default_factory=list)

    def value(self, key: str, default: Any = None) -> Any:
        """The answer; `default` when there is none or it is unsure."""
        answer = self.answers.get(key)
        if answer is None or answer.unsure:
            return default
        return answer.value

    def is_unsure(self, key: str) -> bool:
        answer = self.answers.get(key)
        return answer is not None and answer.unsure

    @property
    def operator_keys(self) -> list[str]:
        """Unsure answers whose rule wants an operator to pick the reply."""
        return [k for k, a in self.answers.items() if a.unsure and a.below == BelowAction.OPERATOR]

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


@dataclass(frozen=True, eq=False)
class DecideContext:
    tenant_id: uuid.UUID | None
    turn_id: uuid.UUID | None = None
    overrides: Overrides | None = None
    # the turn's rules (read once per turn); None → run_decisions() reads them
    policy: "DecisionPolicy | None" = None
    # keys of unsure answers that want an operator: the turn's reply goes to a person
    unsure: list[str] | None = None


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


def merge_parts(question: Question, answers: list[Answer]) -> Answer:
    """One answer of the answers the parts of a split state gave.

    A part that says something (a sure answer other than the neutral one) is the answer:
    «yes» in one part is «yes» for the message, «stop» anywhere is a stop; of several, the
    surest. Else a part that is unsure makes the message unsure. Else nothing was found:
    the neutral answer, as sure as the least sure part. A question without a neutral
    option (score) takes the surest answer."""
    if len(answers) == 1:
        return answers[0]
    sure = [a for a in answers if not a.unsure]
    found = [a for a in sure if not question.says_nothing(a.value)]
    if found:
        return max(found, key=lambda a: a.confidence)
    unsure = [a for a in answers if a.unsure]
    if unsure:
        return max(unsure, key=lambda a: a.confidence)
    neutral = [a for a in sure if a.value == question.neutral_value]
    if neutral:
        return min(neutral, key=lambda a: a.confidence)
    # every part gave a whole-message option («a bare greeting»): none of them is the
    # message, the message says nothing of the kind
    least = min(sure, key=lambda a: a.confidence)
    return replace(least, value=question.neutral_value)


def _merged(asks: Sequence[tuple[str, list[Question]]], results: list[DecisionSet]) -> DecisionSet:
    """The parts' answers put together; the rows of every part are kept, a row is used
    only when its answer is the one taken."""
    out = DecisionSet()
    questions: dict[str, Question] = {}
    given: dict[str, list[tuple[int, Answer]]] = {}
    for index, ((_, asked), result) in enumerate(zip(asks, results, strict=True)):
        questions.update((q.key, q) for q in asked)
        for key, answer in result.answers.items():
            given.setdefault(key, []).append((index, answer))
        out.rows.extend(result.rows)
    for key, pairs in given.items():
        answer = merge_parts(questions[key], [a for _, a in pairs])
        out.answers[key] = answer
        if len(pairs) == 1:
            continue
        # a whole-message option turned neutral is no part's answer: no row is used
        taken = next((i for i, a in pairs if a is answer), None)
        for index, result in enumerate(results):
            if index == taken:
                continue
            for row in result.rows:
                if row.key == key:
                    row.used = False
    return out


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
    return await run_decision_parts(services, [(state, questions)], ctx, trace_step=trace_step)


async def run_decision_parts(
    services: "Services",
    asks: Sequence[tuple[str, list[Question]]],
    ctx: DecideContext,
    *,
    trace_step: dict[str, Any] | None = None,
) -> DecisionSet:
    """run_decisions() for a state asked in parts: (state, questions) a request, all at
    once. A question asked in several parts gets one answer (merge_parts); every row is
    logged. A part that fails fails them all, after the rows of the others are logged."""
    from app.decisions.log import save_decisions

    asks = [(state, questions) for state, questions in asks if questions]
    if not asks:
        return DecisionSet()
    for _, questions in asks:
        question_keys_unique(questions)
    decider: Decider = services.extra["decider"]
    if ctx.policy is None:
        async with services.sm() as session:
            ctx = replace(ctx, policy=await load_policy(session, ctx.tenant_id))
    if ctx.overrides is not None and ctx.overrides.decision_mode and ctx.policy is not None:
        ctx = replace(ctx, policy=ctx.policy.with_mode(ctx.overrides.decision_mode))
    if len(asks) == 1:
        state, questions = asks[0]
        try:
            result = await decider.decide(state, questions, ctx)
        except DecisionsUnavailable as exc:
            await save_decisions(services.sm, exc.rows)
            raise
    else:
        done = await asyncio.gather(
            *(decider.decide(state, questions, ctx) for state, questions in asks),
            return_exceptions=True,
        )
        failed = [d for d in done if isinstance(d, BaseException)]
        if failed:
            rows = [row for d in done if isinstance(d, DecisionSet) for row in d.rows]
            rows += [row for d in failed if isinstance(d, DecisionsUnavailable) for row in d.rows]
            await save_decisions(services.sm, rows)
            lost = [d for d in failed if isinstance(d, DecisionsUnavailable)]
            if len(lost) == len(failed):
                raise DecisionsUnavailable(
                    sorted({k for d in lost for k in d.params["keys"]}), rows
                )
            raise failed[0]
        result = _merged(asks, [d for d in done if isinstance(d, DecisionSet)])
    await save_decisions(services.sm, result.rows)
    keys = list(dict.fromkeys(q.key for _, questions in asks for q in questions))
    missing = [k for k in keys if k not in result.answers]
    if missing:
        log.error("decisions.missing_answers", keys=missing)
    if ctx.unsure is not None:
        ctx.unsure.extend(k for k in result.operator_keys if k not in ctx.unsure)
    if trace_step is not None:
        trace_step.setdefault("decision_ids", []).extend(
            str(row.id) for row in result.rows if row.used
        )
        if len(asks) > 1:
            trace_step["parts"] = trace_step.get("parts", 0) + len(asks)
    return result


def trace_answers(answers: Mapping[str, Answer]) -> dict[str, Any]:
    """Compact view for the turn trace: key → value, confidence, provider (+ why another
    decider answered, + unsure)."""
    out: dict[str, Any] = {}
    for key, a in answers.items():
        item: dict[str, Any] = {
            "value": a.value,
            "confidence": round(a.confidence, 4),
            "provider": str(a.provider),
        }
        if a.reason != DecisionReason.PRIMARY:
            item["reason"] = str(a.reason)
        if a.unsure:
            item["unsure"] = a.below or True
        out[key] = item
    return out
