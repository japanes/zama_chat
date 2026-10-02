"""FakeDecider for tests: answers come from rules by question key, no network.

Strict on purpose: a question without a rule fails the test, so a test always knows
which questions the turn asked.
"""

import uuid
from collections.abc import Callable, Mapping
from fnmatch import fnmatchcase
from typing import Any

from app.db.models import DecisionProvider, DecisionReason
from app.decisions.engine import Answer, DecideContext, DecisionSet, Question, decision_row

# a rule: a fixed value, or (question, state) → value
Rule = Any | Callable[[Question, str], Any]


class FakeDecider:
    model = "fake/decider"

    def __init__(
        self,
        rules: Mapping[str, Rule] | None = None,
        *,
        confidence: float = 0.99,
        unsure: Mapping[str, str] | None = None,
    ):
        # exact key or fnmatch pattern ("field:*") → rule
        self.rules: dict[str, Rule] = dict(rules or {})
        self.confidence = confidence
        # pattern → below-threshold action (ask_client / operator): these answers come
        # back unsure, as JevDecider marks them by the policy
        self.unsure: dict[str, str] = dict(unsure or {})
        self.calls: list[tuple[str, list[Question]]] = []

    def asked(self) -> list[str]:
        return [q.key for _, questions in self.calls for q in questions]

    def _rule(self, key: str) -> Rule:
        if key in self.rules:
            return self.rules[key]
        for pattern, rule in self.rules.items():
            if fnmatchcase(key, pattern):
                return rule
        raise AssertionError(f"FakeDecider: no rule for question {key!r}")

    async def decide(
        self, state: str, questions: list[Question], ctx: DecideContext
    ) -> DecisionSet:
        self.calls.append((state, list(questions)))
        result = DecisionSet()
        batch_id = uuid.uuid4()
        for question in questions:
            rule = self._rule(question.key)
            raw = rule(question, state) if callable(rule) else rule
            value = question.coerce(raw)
            if value is None:
                raise AssertionError(f"FakeDecider: {raw!r} does not fit {question.key!r}")
            answer = Answer(
                key=question.key,
                type=question.type,
                value=value,
                confidence=self.confidence,
                provider=DecisionProvider.FAKE,
                model=self.model,
            )
            below = self._unsure(question.key)
            if below is not None:
                answer.unsure = True
                answer.below = below
                answer.reason = DecisionReason.UNSURE
            result.answers[question.key] = answer
            result.rows.append(
                decision_row(
                    question,
                    ctx=ctx,
                    batch_id=batch_id,
                    provider=DecisionProvider.FAKE,
                    model=self.model,
                    answer=answer,
                    reason=answer.reason,
                    used=not answer.unsure,
                )
            )
        return result

    def _unsure(self, key: str) -> str | None:
        for pattern, below in self.unsure.items():
            if key == pattern or fnmatchcase(key, pattern):
                return below
        return None
