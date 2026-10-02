"""Contacts, conversations, messages and turns."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
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


class Contact(IdMixin, CreatedMixin, Base):
    """A client in one channel (Instagram IGSID, external id of an API caller…)."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("channel_id", "external_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(200))
    username: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(200))
    profile: Mapped[dict[str, Any]] = mapped_column(default=dict)


class Conversation(IdMixin, TimestampsMixin, Base):
    """One dialog with one contact. Turns of one conversation never run in parallel:
    a turn holds the lease (lease_owner / lease_until) for its whole duration."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_tenant_last", "tenant_id", "last_client_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    client_language: Mapped[str | None] = mapped_column(String(16))
    reply_language: Mapped[str | None] = mapped_column(String(16))
    # service data only (last_presented options, etc.) — see app.core.state
    state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    operator_mode: Mapped[bool] = mapped_column(default=False)
    # client asked for a human: the bot stays silent until an operator releases it
    handover: Mapped[bool] = mapped_column(default=False)
    handover_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_bot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_version: Mapped[int] = mapped_column(Integer, default=0)


class MessageRole(StrEnum):
    CLIENT = "client"
    BOT = "bot"
    OPERATOR = "operator"
    SYSTEM = "system"


class SendStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    DROPPED = "dropped"


class Message(IdMixin, CreatedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        # the same Meta mid / client_message_id twice → ON CONFLICT DO NOTHING
        UniqueConstraint("channel_id", "external_id"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index(
            "ix_messages_unprocessed",
            "conversation_id",
            postgresql_where="role = 'client' AND turn_id IS NULL",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    external_id: Mapped[str | None] = mapped_column(String(200))
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("turns.id", ondelete="SET NULL", use_alter=True)
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # bot / operator replies only
    send_status: Mapped[str | None] = mapped_column(String(16))
    send_error: Mapped[str | None] = mapped_column(String(1000))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)


class TurnStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SUPERSEDED = "superseded"  # new client messages arrived before the reply was sent


class Turn(IdMixin, CreatedMixin, Base):
    """One bot turn: merged client input → rewrite → route → scenarios → reply."""

    __tablename__ = "turns"
    __table_args__ = (Index("ix_turns_conversation_created", "conversation_id", "created_at"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16), default=TurnStatus.RUNNING)
    input_text: Mapped[str] = mapped_column(Text, default="")
    rewritten: Mapped[str | None] = mapped_column(Text)
    client_language: Mapped[str | None] = mapped_column(String(16))
    reply_language: Mapped[str | None] = mapped_column(String(16))
    route: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal(0))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    sandbox: Mapped[bool] = mapped_column(default=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
