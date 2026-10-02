"""Where results go (endpoints) and every attempt to send one (deliveries)."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, TimestampsMixin


class Endpoint(IdMixin, TimestampsMixin, Base):
    """An external API that receives results. Scenario actions refer to it by `name`."""

    __tablename__ = "endpoints"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(2000))
    # HMAC key for X-Zama-Signature, Fernet-encrypted (services.crypto)
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    headers: Mapped[dict[str, Any]] = mapped_column(default=dict)
    is_active: Mapped[bool] = mapped_column(default=True)


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"  # a newer version of the same result replaced it


class Delivery(IdMixin, TimestampsMixin, Base):
    """One version of a result sent to one endpoint. Its id is the `delivery_id` /
    Idempotency-Key: the receiver drops a duplicate by it."""

    __tablename__ = "deliveries"
    __table_args__ = (
        Index("ix_deliveries_run", "run_id", "created_at"),
        Index("ix_deliveries_tenant_status", "tenant_id", "status"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenario_runs.id", ondelete="CASCADE"))
    endpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("endpoints.id", ondelete="SET NULL")
    )
    event: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DevSinkItem(IdMixin, CreatedMixin, Base):
    """JSON received by POST /dev/sink/{name} (dev only): check deliveries without an
    external service."""

    __tablename__ = "dev_sink_items"
    __table_args__ = (Index("ix_dev_sink_items_name_created", "name", "created_at"),)

    name: Mapped[str] = mapped_column(String(100))
    body: Mapped[dict[str, Any]] = mapped_column()
    headers: Mapped[dict[str, Any]] = mapped_column(default=dict)
