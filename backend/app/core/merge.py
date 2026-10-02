"""Merge pause for asynchronous channels: a client often writes several short messages
in a row, the bot answers them all in one turn.

Every new message moves the `run_turn` job to now + pause, but never later than
max_wait after the first message still waiting for an answer (channel settings
`merge_pause_s`, `merge_max_wait_s`).
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, MessageRole
from app.jobs.queue import enqueue

# TODO(review): 5 s / 20 s — start values; tune by real dialogs
DEFAULT_PAUSE_S = 5.0
DEFAULT_MAX_WAIT_S = 20.0


def merge_times(channel_settings: dict[str, Any]) -> tuple[float, float]:
    pause = float(channel_settings.get("merge_pause_s") or DEFAULT_PAUSE_S)
    max_wait = float(channel_settings.get("merge_max_wait_s") or DEFAULT_MAX_WAIT_S)
    return max(pause, 0.0), max(max_wait, pause)


def turn_run_at(
    now: datetime, first_waiting_at: datetime | None, pause_s: float, max_wait_s: float
) -> datetime:
    run_at = now + timedelta(seconds=pause_s)
    if first_waiting_at is not None:
        run_at = min(run_at, first_waiting_at + timedelta(seconds=max_wait_s))
    return max(run_at, now)


async def schedule_turn(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    channel_settings: dict[str, Any],
    now: datetime,
) -> datetime:
    """Queue (or move) the turn of a conversation. Call in the transaction that saved
    the client message."""
    first = (
        await session.execute(
            select(func.min(Message.created_at)).where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.CLIENT,
                Message.turn_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    pause, max_wait = merge_times(channel_settings)
    run_at = turn_run_at(now, first, pause, max_wait)
    await enqueue(
        session,
        "run_turn",
        {"conversation_id": str(conversation_id)},
        run_at=run_at,
        tenant_id=tenant_id,
        dedupe_key=f"turn:{conversation_id}",
        on_conflict="reschedule",
    )
    return run_at
