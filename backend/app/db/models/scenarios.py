"""Scenarios (versioned JSON definitions), their runs (= results) and which scenarios
work in which channel."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedMixin, IdMixin, TimestampsMixin


class Scenario(IdMixin, CreatedMixin, Base):
    """One version of a scenario of a tenant's library. The definition is the whole YAML
    as JSON (checked by app.scenarios.schema); a run keeps the version it was made with."""

    __tablename__ = "scenarios"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", "version"),
        Index(
            "uq_scenarios_active",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    definition: Mapped[dict[str, Any]] = mapped_column()
    is_active: Mapped[bool] = mapped_column(default=True)
    author: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)


class RunStatus(StrEnum):
    FOCUS = "focus"  # the bot asks its questions; at most one per conversation
    PAUSED = "paused"  # open, takes answers, but the bot does not ask about it
    WAITING = "waiting"  # data collected, waiting for an event (payment, CRM, operator)
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"  # resume: within_hours ran out


OPEN_STATUSES = (RunStatus.FOCUS, RunStatus.PAUSED, RunStatus.WAITING)


class ScenarioRun(IdMixin, TimestampsMixin, Base):
    """A started scenario = a result: its id is the permanent `result_id` sent outside.

    `fields` holds the state of every field (app.scenarios.fields.FieldState)."""

    __tablename__ = "scenario_runs"
    __table_args__ = (
        Index(
            "uq_scenario_runs_focus",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'focus'"),
        ),
        Index(
            "uq_scenario_runs_open",
            "conversation_id",
            "scenario_key",
            unique=True,
            postgresql_where=text("status IN ('focus', 'paused', 'waiting')"),
        ),
        Index("ix_scenario_runs_tenant_started", "tenant_id", "started_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id"))
    scenario_key: Mapped[str] = mapped_column(String(100))
    scenario_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    fields: Mapped[dict[str, Any]] = mapped_column(default=dict)
    # the field the bot asked about in its last reply (re-ask counting)
    asking: Mapped[str | None] = mapped_column(String(100))
    # events of the run for the admin: [{"event", "field", "at", "turn_id"}]
    events: Mapped[list[Any]] = mapped_column(default=list)
    # version of the result sent outside; grows with every delivery
    delivery_seq: Mapped[int] = mapped_column(Integer, default=0)
    # stage 6: {"confirmed_at", "waiting_for": event | receipt_review | approval,
    #           "waiting_since", "external": {event data}, "events_seen": [event_id…]}
    data: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    # completion by_caller / options_from caller: {"fields": [field definitions + added_at],
    # "options": {field: [{key, label}]}} — the run's own part of its fields
    # (scenarios/store.effective_definition)
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    # background_answers: the dialogue of the run as the client wrote it, the source of the
    # answers written in the background (scenarios/answers.py): [{"turn", "bot", "client",
    # "rewritten"?, "messages", "at"}]. Deferred: a turn does not read it
    dialogue: Mapped[list[Any]] = mapped_column(
        default=list, server_default=text("'[]'::jsonb"), deferred=True
    )
    client_language: Mapped[str | None] = mapped_column(String(16))
    reply_language: Mapped[str | None] = mapped_column(String(16))
    started_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("turns.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChannelScenario(IdMixin, CreatedMixin, Base):
    """Which scenarios work in a channel and in which order. The order decides who gets
    the focus when two scenarios start with an equal sum of votes."""

    __tablename__ = "channel_scenarios"
    __table_args__ = (UniqueConstraint("channel_id", "scenario_key"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    scenario_key: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(default=True)
