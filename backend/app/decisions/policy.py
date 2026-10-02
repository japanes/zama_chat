"""Per-question policy: who answers (Jev / the LLM / both for comparison), the confidence
threshold and what to do with an unsure Jev answer.

Rules live in `decision_rules` (admin → Decisions): a pattern — an exact key or fnmatch
(«start:*», «filter:booking.*») — and its mode, threshold and below-threshold action.
A tenant's rule replaces the shared rule with the same pattern; among the rules matching
a key the exact key wins, then the longest pattern. No rule → Jev, DEFAULT_THRESHOLD,
ask the LLM. The rules are read at the start of every turn: a change works at once.
"""

import re
import uuid
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BelowAction, DecisionRule, PolicyMode

# TODO(review): starting value; the real ones come from the Jev probe report
# (tmp/jev-probe/report.md) and the decisions log (admin → Decisions).
DEFAULT_THRESHOLD = 0.7
DEFAULT_PATTERN = "*"

PATTERN_RE = re.compile(r"^[A-Za-z0-9_:.*?\[\]!-]{1,200}$")


@dataclass(frozen=True)
class Rule:
    pattern: str = DEFAULT_PATTERN
    mode: str = PolicyMode.JEV
    threshold: float = DEFAULT_THRESHOLD
    below: str = BelowAction.LLM

    def to_dict(self) -> dict[str, object]:
        return {
            "pattern": self.pattern,
            "mode": str(self.mode),
            "threshold": self.threshold,
            "below": str(self.below),
        }


@dataclass
class DecisionPolicy:
    default_threshold: float = DEFAULT_THRESHOLD
    # exact key or fnmatch pattern → threshold (stage 1 form; still works)
    thresholds: dict[str, float] = field(default_factory=dict)
    # pattern → full rule (stage 7)
    rules: dict[str, Rule] = field(default_factory=dict)
    # one mode for every question (`make eval SHADOW=1`, the sandbox); the thresholds and
    # below-threshold actions of the rules stay
    force_mode: str | None = None

    def rule(self, key: str) -> Rule:
        found = self._match(key)
        if found is None:
            found = Rule(threshold=self._threshold_only(key))
        if self.force_mode is not None and found.mode != self.force_mode:
            found = replace(found, mode=self.force_mode)
        return found

    def pattern_for(self, key: str) -> str | None:
        """The pattern of the rule that decides `key`; None = no rule, the defaults."""
        if key in self.rules:
            return key
        matches = [p for p in self.rules if fnmatchcase(key, p)]
        return max(matches, key=len) if matches else None

    def _match(self, key: str) -> Rule | None:
        pattern = self.pattern_for(key)
        return self.rules[pattern] if pattern is not None else None

    def _threshold_only(self, key: str) -> float:
        if key in self.thresholds:
            return self.thresholds[key]
        matches = [p for p in self.thresholds if fnmatchcase(key, p)]
        if matches:
            return self.thresholds[max(matches, key=len)]
        return self.default_threshold

    def threshold(self, key: str) -> float:
        return self.rule(key).threshold

    def is_sure(self, key: str, confidence: float) -> bool:
        return confidence >= self.threshold(key)

    def with_mode(self, mode: str | None) -> "DecisionPolicy":
        if mode is None:
            return self
        return replace(self, force_mode=mode)


def rule_of(row: DecisionRule) -> Rule:
    return Rule(row.pattern, row.mode, row.threshold, row.below)


async def rule_rows(session: AsyncSession, tenant_id: uuid.UUID | None) -> list[DecisionRule]:
    """The shared rules and, for a tenant, its own ones."""
    owner = DecisionRule.tenant_id.is_(None)
    if tenant_id is not None:
        owner = owner | (DecisionRule.tenant_id == tenant_id)
    query = select(DecisionRule).where(owner).order_by(DecisionRule.pattern)
    return list((await session.execute(query)).scalars())


def policy_from(rows: list[DecisionRule]) -> DecisionPolicy:
    rules: dict[str, Rule] = {}
    for row in sorted(rows, key=lambda r: r.tenant_id is not None):  # shared first
        rules[row.pattern] = rule_of(row)
    return DecisionPolicy(rules=rules)


async def load_policy(session: AsyncSession, tenant_id: uuid.UUID | None) -> DecisionPolicy:
    return policy_from(await rule_rows(session, tenant_id))
