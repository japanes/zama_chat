"""Conversation lease: two turns of one conversation never run at the same time.

A turn is split in three parts so no DB connection idles during LLM calls:
  1. short transaction "take" — acquire the lease;
  2. LLM / Jev calls outside any transaction;
  3. short transaction "write" — `hold()` locks the row and checks the lease is still
     ours, then writes and releases.
If a worker dies, the lease expires by itself (lease_until) and the turn is retried.
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.db.models import Conversation
from app.errors import Conflict


async def try_acquire(
    session: AsyncSession, conversation_id: uuid.UUID, owner: str, now: datetime, seconds: int
) -> int | None:
    """Take the lease if it is free, expired or already ours. Returns state_version or
    None when someone else holds it. Call inside a transaction."""
    result = await session.execute(
        update(Conversation)
        .where(
            Conversation.id == conversation_id,
            or_(
                Conversation.lease_owner.is_(None),
                Conversation.lease_until < now,
                Conversation.lease_owner == owner,
            ),
        )
        .values(lease_owner=owner, lease_until=now + timedelta(seconds=seconds))
        .returning(Conversation.state_version)
    )
    return result.scalar_one_or_none()


async def acquire(
    sm: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    owner: str,
    clock: Clock,
    *,
    seconds: int,
    wait_seconds: float = 0.0,
    poll_seconds: float = 0.2,
) -> int:
    """Take the lease in its own short transaction, waiting up to `wait_seconds`
    (real time). Raises Conflict("conversation.busy") when it stays taken."""
    deadline = time.monotonic() + wait_seconds
    while True:
        async with sm() as session, session.begin():
            version = await try_acquire(session, conversation_id, owner, clock.now(), seconds)
        if version is not None:
            return version
        if time.monotonic() >= deadline:
            raise Conflict("conversation.busy", {"conversation_id": str(conversation_id)})
        await asyncio.sleep(poll_seconds)


async def extend(
    sm: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    owner: str,
    clock: Clock,
    *,
    seconds: int,
) -> bool:
    """Push lease_until forward during a long turn. False = the lease is lost."""
    now = clock.now()
    async with sm() as session, session.begin():
        result = await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.lease_owner == owner)
            .values(lease_until=now + timedelta(seconds=seconds))
            .returning(Conversation.id)
        )
        return result.scalar_one_or_none() is not None


async def hold(session: AsyncSession, conversation_id: uuid.UUID, owner: str) -> Conversation:
    """Inside the "write" transaction: lock the conversation row and make sure the lease
    is still ours. If it expired but nobody took it, it is still ours — nobody else
    could have written in between. Raises Conflict("lease.lost")."""
    conv = (
        await session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if conv.lease_owner != owner:
        raise Conflict("lease.lost", {"conversation_id": str(conversation_id)})
    return conv


def release_in(conv: Conversation) -> None:
    """Release the lease on a row already locked by `hold()` (same transaction)."""
    conv.lease_owner = None
    conv.lease_until = None


async def release(
    sm: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID, owner: str
) -> None:
    """Release the lease in its own transaction (error paths). No-op if not ours."""
    async with sm() as session, session.begin():
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.lease_owner == owner)
            .values(lease_owner=None, lease_until=None)
        )


@contextlib.asynccontextmanager
async def keep_alive(
    sm: async_sessionmaker[AsyncSession],
    conversation_id: uuid.UUID,
    owner: str,
    clock: Clock,
    *,
    seconds: int,
) -> AsyncIterator[None]:
    """Extend the lease every seconds/3 while the body runs (LLM retries and a fallback
    model can take longer than one lease). A lost lease is noticed by `hold()`."""

    async def beat() -> None:
        while True:
            await asyncio.sleep(max(1.0, seconds / 3))
            if not await extend(sm, conversation_id, owner, clock, seconds=seconds):
                return

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
