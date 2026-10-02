"""Background jobs queue in PostgreSQL (no Redis).

A job is written in the same transaction as the data it belongs to, so a crash
never loses it. Silence timers and message merging are jobs with run_at that are
rescheduled by dedupe_key.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampsMixin


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(IdMixin, TimestampsMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_due", "run_at", postgresql_where=text("status = 'queued'")),
        Index(
            "ix_jobs_running_locked", "locked_until", postgresql_where=text("status = 'running'")
        ),
        # a job that is already running cannot be rescheduled — a new one is queued
        Index(
            "uq_jobs_dedupe_queued",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'queued' AND dedupe_key IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(300))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
