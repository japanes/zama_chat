"""Decision log: every answer of Jev (or the fallback LLM) to a route / filter question.

One row per question. `batch_id` groups the questions sent in one /v1/evaluate request;
cost and tokens of a batch are written on its first row only, so sums stay correct.
"""

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, TimestampsMixin


class DecisionType(StrEnum):
    BOOLEAN = "boolean"
    CHOICE = "choice"
    SCORE = "score"


class DecisionProvider(StrEnum):
    JEV = "jev"
    LLM = "llm"
    FAKE = "fake"


class DecisionReason(StrEnum):
    PRIMARY = "primary"  # the main decider answered with enough confidence
    BELOW_THRESHOLD = "below_threshold"  # Jev was unsure → asked the LLM again
    JEV_ERROR = "jev_error"  # Jev failed (5xx / network / timeout) → LLM
    LLM_ERROR = "llm_error"  # the LLM was the main decider and failed → Jev
    SHADOW = "shadow"  # stage 7: both answered, this one was not used
    # Jev was unsure and the rule says «no answer»: the bot asks the client again (or an
    # operator picks the reply); the value is logged, not used
    UNSURE = "unsure"


class PolicyMode(StrEnum):
    """Who answers a question (rules in `decision_rules`)."""

    JEV = "jev"  # Jev; below the threshold — see BelowAction
    LLM = "llm"  # the `decide` role model only (Jev if it fails)
    SHADOW = "shadow"  # Jev's answer is used, the LLM answers too — for comparison only


class BelowAction(StrEnum):
    """What to do with a Jev answer below the rule's threshold."""

    LLM = "llm"  # ask the LLM, use its answer
    KEEP = "keep"  # use Jev's answer anyway
    # no LLM: the careful reading of Jev's answer (Question.unsure_as) — «answered in
    # full» is taken as «in part»; a value without one is no answer, as ask_client
    SAFE = "safe"
    ASK_CLIENT = "ask_client"  # count as «no answer»: the bot asks the client again
    OPERATOR = "operator"  # as ask_client, and an operator picks this turn's reply


class Decision(IdMixin, CreatedMixin, Base):
    __tablename__ = "decisions"
    __table_args__ = (
        Index("ix_decisions_turn", "turn_id"),
        Index("ix_decisions_tenant_key_created", "tenant_id", "key", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    batch_id: Mapped[uuid.UUID] = mapped_column()
    key: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16))
    # the question as sent: instructions + criteria
    question: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # {"value": …, "probability" | "probabilities": …}
    answer: Mapped[dict[str, Any] | None] = mapped_column()
    confidence: Mapped[float | None] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(String(32), default=DecisionReason.PRIMARY)
    # False when a later answer replaced this one (below threshold → LLM) or in shadow mode
    used: Mapped[bool] = mapped_column(default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal(0))
    generation_id: Mapped[str | None] = mapped_column(String(200))
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_calls.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)


class DecisionRule(IdMixin, TimestampsMixin, Base):
    """Policy of the questions matching `pattern` (exact key or fnmatch: «start:*»).
    tenant_id NULL = the shared rule; a tenant's rule with the same pattern replaces it.
    Among the matching rules the exact key wins, then the longest pattern."""

    __tablename__ = "decision_rules"
    __table_args__ = (UniqueConstraint("tenant_id", "pattern", postgresql_nulls_not_distinct=True),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    pattern: Mapped[str] = mapped_column(String(200))
    mode: Mapped[str] = mapped_column(String(16), default=PolicyMode.JEV)
    threshold: Mapped[float] = mapped_column(Float)
    below: Mapped[str] = mapped_column(String(16), default=BelowAction.SAFE)
    note: Mapped[str | None] = mapped_column(Text)
