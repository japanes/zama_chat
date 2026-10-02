"""Decision log: every answer of Jev (or the fallback LLM) to a route / filter question.

One row per question. `batch_id` groups the questions sent in one /v1/evaluate request;
cost and tokens of a batch are written on its first row only, so sums stay correct.
"""

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin


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
    SHADOW = "shadow"  # stage 7: both answered, this one was not used


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
