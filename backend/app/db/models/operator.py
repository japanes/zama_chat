"""Operator work: tasks for a human (choose a reply, a handed-over client…) and
notifiers — where the team gets told about them (Telegram, email)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampsMixin


class TaskKind(StrEnum):
    CHOOSE_REPLY = "choose_reply"  # operator mode: pick / edit one of the bot's variants
    HANDOVER = "handover"  # the client asked for a human; the bot is silent
    APPROVE_ACTION = "approve_action"  # stage 6: an action with requires_approval
    REVIEW_RECEIPT = "review_receipt"  # stage 6: a payment receipt to check


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class OperatorTask(IdMixin, TimestampsMixin, Base):
    """Something a human has to do in a conversation.

    choose_reply: payload = {variants, turn_id, input_text, …}; a new client message
    rebuilds the variants of the same open task (`version` + 1), so an operator who
    picks a variant of an old version gets `operator_task.outdated`.
    approve_action: payload = {run_id, scenario, action, do, event, field, fields};
    review_receipt: payload = {run_id, receipt_id, field, scenario, extracted, issues, …}.
    """

    __tablename__ = "operator_tasks"
    __table_args__ = (
        Index("ix_operator_tasks_tenant_status", "tenant_id", "status", "created_at"),
        Index("ix_operator_tasks_conversation", "conversation_id", "created_at"),
        # one open choose_reply / handover per conversation; approvals and receipts may
        # be several at a time (two actions, two receipts)
        Index(
            "uq_operator_tasks_open",
            "conversation_id",
            "kind",
            unique=True,
            postgresql_where="status = 'open' AND kind IN ('choose_reply', 'handover')",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.OPEN)
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # how it ended: {"action": "sent", "variant": 1, "edited": false, "message_id": …}
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotifierKind(StrEnum):
    TELEGRAM = "telegram"
    EMAIL = "email"


# events a notifier can subscribe to (scenario actions name the notifier directly)
NOTIFY_EVENTS = (
    "handover",
    "choose_reply",
    "channel_error",
    "approve_action",  # stage 6: an action waits for «approve»
    "review_receipt",  # stage 6: a payment receipt waits for a check
)


class Notifier(IdMixin, TimestampsMixin, Base):
    """Where the team is told about things. Scenario actions refer to it by `name`
    (`do: notify_telegram`, `params: {notifier: managers}`); `events` are system events
    it also gets (a client asked for a human, a channel broke…).

    telegram: secret_enc = bot token (Fernet), config = {chat_ids: [...]};
    email: config = {to: [...]}. `language` — en / uk / ru of the message texts.
    """

    __tablename__ = "notifiers"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    events: Mapped[list[Any]] = mapped_column(default=list)
    language: Mapped[str] = mapped_column(String(16), default="en")
    is_active: Mapped[bool] = mapped_column(default=True)
