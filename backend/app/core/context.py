"""What a turn sees (TurnContext) and what it decided (TurnPlan).

The "think" part of a turn (rewrite → route → scenarios → respond) is a pipeline:
`async (services, ctx) -> TurnPlan`. It runs outside any DB transaction and must not
write conversation data itself — everything it wants written goes into the plan and is
applied by core/turn.py in the "write" transaction, after the lease check.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_settings import TenantSettings
from app.core.trace import Trace
from app.db.models import Conversation, Turn
from app.llm.service import Overrides

if TYPE_CHECKING:
    from app.services import Services


@dataclass(frozen=True)
class HistoryItem:
    role: str  # client / bot / operator / system
    text: str
    created_at: datetime


@dataclass
class TurnContext:
    turn_id: uuid.UUID
    conversation_id: uuid.UUID
    tenant_id: uuid.UUID
    channel_id: uuid.UUID
    channel_kind: str
    contact_id: uuid.UUID
    contact_name: str | None
    settings: TenantSettings
    tenant_title: str
    # client messages of this turn, merged
    pending_ids: list[uuid.UUID]
    input_text: str
    # earlier messages, oldest first (without this turn's input)
    history: list[HistoryItem]
    client_language: str | None
    reply_language: str | None
    state: dict[str, Any]
    state_version: int
    handover: bool
    operator_mode: bool
    now: datetime
    sandbox: bool = False
    overrides: Overrides | None = None
    # operator mode: how many reply variants to prepare (channel setting)
    operator_variants: int = 3
    trace: Trace = field(default_factory=Trace)
    # loaded by later stages (scenario runs, channel scenarios…)
    extra: dict[str, Any] = field(default_factory=dict)


# applied inside the "write" transaction, conversation row locked by hold()
WriteFn = Callable[[AsyncSession, Conversation, Turn], Awaitable[None]]


@dataclass
class TurnPlan:
    reply_text: str | None
    rewritten: str | None = None
    client_language: str | None = None
    reply_language: str | None = None
    route: dict[str, Any] = field(default_factory=dict)
    handover: bool = False
    state: dict[str, Any] | None = None  # new conversations.state (None = unchanged)
    reply_meta: dict[str, Any] = field(default_factory=dict)
    writes: list[WriteFn] = field(default_factory=list)
    # operator mode: reply variants for the operator instead of a reply to the client
    variants: list[str] | None = None


Pipeline = Callable[["Services", TurnContext], Awaitable[TurnPlan]]
