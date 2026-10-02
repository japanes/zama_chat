"""Tenants, their channels and API keys."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, TimestampsMixin


class Tenant(IdMixin, TimestampsMixin, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    # languages, reply_language, business_description, company_title, bot_name,
    # silence_minutes … — validated by app.core.tenant_settings.TenantSettings
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)


class ChannelKind(StrEnum):
    HTTP = "http"
    INSTAGRAM = "instagram"


class ChannelStatus(StrEnum):
    CONNECTED = "connected"
    EXPIRING = "expiring"
    ERROR = "error"
    DISABLED = "disabled"


class Channel(IdMixin, TimestampsMixin, Base):
    """Where conversations come from. Each tenant has any number of channels.

    One external account belongs to exactly one tenant: unique(kind, account_id).
    """

    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("kind", "account_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[str | None] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(1000))
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=ChannelStatus.CONNECTED)
    status_error: Mapped[str | None] = mapped_column(String(1000))
    # operator_mode, merge_pause_s, merge_max_wait_s and optional overrides of tenant
    # settings (languages, reply_language, business_description)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ApiKey(IdMixin, CreatedMixin, Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_tenant", "tenant_id"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="")
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
