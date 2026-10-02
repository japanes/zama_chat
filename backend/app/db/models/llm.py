"""LLM models (added by the admin), role assignments and the call log.

No model names live in code: the admin adds models with prices and assigns them to
roles (reply, rewrite, extract, decide, vision, embed).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, TimestampsMixin


class LlmRole(StrEnum):
    REPLY = "reply"  # answer to the client, 3–4 variants for the operator
    REWRITE = "rewrite"  # context manager
    EXTRACT = "extract"  # field values
    DECIDE = "decide"  # fallback decider instead of Jev
    VISION = "vision"  # receipts (stage 6)
    EMBED = "embed"  # embeddings (stage 5)


# roles that need structured JSON output from the model
ROLES_NEED_JSON_SCHEMA = {LlmRole.REWRITE, LlmRole.EXTRACT, LlmRole.DECIDE, LlmRole.VISION}


class LlmModel(IdMixin, TimestampsMixin, Base):
    __tablename__ = "llm_models"

    name: Mapped[str] = mapped_column(String(200), unique=True)  # Gateway id: openai/gpt-6-luna
    label: Mapped[str] = mapped_column(String(200), default="")
    kind: Mapped[str] = mapped_column(String(16), default="language")  # language | embedding
    # USD per 1M tokens
    price_input: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    price_cached_input: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    price_output: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_output: Mapped[int | None] = mapped_column(Integer)
    supports_json_schema: Mapped[bool] = mapped_column(default=True)
    supports_reasoning: Mapped[bool] = mapped_column(default=False)
    supports_vision: Mapped[bool] = mapped_column(default=False)
    reasoning_efforts: Mapped[list[Any]] = mapped_column(default=list)
    is_active: Mapped[bool] = mapped_column(default=True)


class ModelRole(IdMixin, TimestampsMixin, Base):
    """Which model serves a role. tenant_id NULL = shared default; a tenant row wins."""

    __tablename__ = "model_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "role", postgresql_nulls_not_distinct=True),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(32))
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("llm_models.id", ondelete="RESTRICT"))
    reasoning_effort: Mapped[str | None] = mapped_column(String(16))
    timeout_s: Mapped[float] = mapped_column(Float, default=60.0)
    fallback_model_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_models.id", ondelete="RESTRICT")
    )


class LlmCall(IdMixin, CreatedMixin, Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_turn", "turn_id"),
        Index("ix_llm_calls_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(200))
    prompt_key: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    request: Mapped[dict[str, Any]] = mapped_column(default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column()
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cached: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    generation_id: Mapped[str | None] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
