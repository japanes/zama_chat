"""Conversation lease on a real PostgreSQL: one turn at a time per conversation."""

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.core import lease
from app.core.ingest import save_client_message
from app.db.models import Conversation
from app.errors import Conflict
from tests.factories import make_tenant

pytestmark = pytest.mark.db


async def _conversation(sm, clock) -> uuid.UUID:
    bundle = await make_tenant(sm)
    async with sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            contact_external_id="c1",
            text="hi",
            now=clock.now(),
        )
    return ingested.conversation_id


async def _conv(sm, conv_id) -> Conversation:
    async with sm() as session:
        return (
            await session.execute(select(Conversation).where(Conversation.id == conv_id))
        ).scalar_one()


async def test_acquire_returns_state_version_and_sets_owner(sm, clock):
    conv_id = await _conversation(sm, clock)
    version = await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    assert version == 0
    conv = await _conv(sm, conv_id)
    assert conv.lease_owner == "a"
    assert conv.lease_until is not None
    assert (conv.lease_until - clock.now()).total_seconds() == 90


async def test_second_owner_gets_busy(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    with pytest.raises(Conflict) as err:
        await lease.acquire(sm, conv_id, "b", clock, seconds=90, wait_seconds=0.3, poll_seconds=0.1)
    assert err.value.code == "conversation.busy"
    assert (await _conv(sm, conv_id)).lease_owner == "a"


async def test_same_owner_takes_again(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    assert await lease.acquire(sm, conv_id, "a", clock, seconds=90) == 0


async def test_expired_lease_is_taken_over(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "dead", clock, seconds=90)
    clock.advance(seconds=91)
    await lease.acquire(sm, conv_id, "alive", clock, seconds=90)
    assert (await _conv(sm, conv_id)).lease_owner == "alive"


async def test_waiting_owner_gets_lease_after_release(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)

    async def release_soon() -> None:
        await asyncio.sleep(0.2)
        await lease.release(sm, conv_id, "a")

    _, version = await asyncio.gather(
        release_soon(),
        lease.acquire(sm, conv_id, "b", clock, seconds=90, wait_seconds=3.0, poll_seconds=0.05),
    )
    assert version == 0
    assert (await _conv(sm, conv_id)).lease_owner == "b"


async def test_hold_with_foreign_owner_is_rejected(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    clock.advance(seconds=91)
    await lease.acquire(sm, conv_id, "b", clock, seconds=90)
    async with sm() as session, session.begin():
        with pytest.raises(Conflict) as err:
            await lease.hold(session, conv_id, "a")
    assert err.value.code == "lease.lost"


async def test_hold_after_expiry_without_taker_is_still_ours(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    clock.advance(seconds=200)
    async with sm() as session, session.begin():
        conv = await lease.hold(session, conv_id, "a")
        lease.release_in(conv)
    conv = await _conv(sm, conv_id)
    assert conv.lease_owner is None
    assert conv.lease_until is None


async def test_extend_only_for_owner(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    clock.advance(seconds=60)
    assert await lease.extend(sm, conv_id, "a", clock, seconds=90)
    assert not await lease.extend(sm, conv_id, "b", clock, seconds=90)
    conv = await _conv(sm, conv_id)
    assert (conv.lease_until - clock.now()).total_seconds() == 90


async def test_release_is_noop_for_foreign_owner(sm, clock):
    conv_id = await _conversation(sm, clock)
    await lease.acquire(sm, conv_id, "a", clock, seconds=90)
    await lease.release(sm, conv_id, "b")
    assert (await _conv(sm, conv_id)).lease_owner == "a"
    await lease.release(sm, conv_id, "a")
    assert (await _conv(sm, conv_id)).lease_owner is None


async def test_try_acquire_returns_current_state_version(sm, clock):
    conv_id = await _conversation(sm, clock)
    async with sm() as session, session.begin():
        conv = await session.get(Conversation, conv_id)
        conv.state_version = 7
    async with sm() as session, session.begin():
        assert await lease.try_acquire(session, conv_id, "a", clock.now(), 90) == 7
    async with sm() as session, session.begin():
        assert await lease.try_acquire(session, conv_id, "b", clock.now(), 90) is None
