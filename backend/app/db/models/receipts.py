"""Payment receipts the clients sent (stage 6, docs/03 §8): what the vision model read,
what code found wrong, who decided."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampsMixin


class ReceiptStatus(StrEnum):
    ACCEPTED = "accepted"  # by code (review off, no issues) or by an operator
    REVIEW = "review"  # waits for an operator (review_receipt task)
    REJECTED = "rejected"  # an operator rejected it / not a receipt / unreadable


class Receipt(IdMixin, TimestampsMixin, Base):
    __tablename__ = "receipts"
    __table_args__ = (
        # the same image sent again (duplicates are looked up per tenant)
        Index("ix_receipts_tenant_sha256", "tenant_id", "sha256"),
        Index("ix_receipts_tenant_reference", "tenant_id", "reference"),
        Index("ix_receipts_run", "run_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenario_runs.id", ondelete="CASCADE"))
    field_key: Mapped[str] = mapped_column(String(100))
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    url: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(100))
    sha256: Mapped[str | None] = mapped_column(String(64))
    # what the vision model read: {is_receipt, amount, currency, date, recipient, …}
    extracted: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # the transaction number from the receipt (duplicates)
    reference: Mapped[str | None] = mapped_column(String(200))
    # what was expected: {amount, currency, recipients, max_age_days}
    expected: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # what code found: [{"code": "amount_mismatch", "params": {...}}]
    issues: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16))
    # why it went to a person: always / issues / amount_above
    review_reason: Mapped[str | None] = mapped_column(String(32))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("llm_calls.id", ondelete="SET NULL")
    )
