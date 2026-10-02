"""Scenario timers (jobs):

- silence_check — the client has been silent for N minutes inside a scenario → the
  `on: silence` action (a partial JSON). Nothing is closed: the client may come back.
  The timer is moved by every turn (dedupe `silence:<run>:<action>`);
- expire_run — `resume: within_hours` ran out (or `waiting.hours` for a run waiting for
  an event) → the run is abandoned, final JSON. Moved by every turn (dedupe
  `expire:<run>`).

Both take the conversation lease first, like a turn: a timer never runs in the middle
of a turn, and a turn that just ran is seen (last_client_at / last_activity_at).
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import fire
from app.core import lease
from app.core.tenant_settings import effective_settings
from app.db.models import (
    OPEN_STATUSES,
    Channel,
    Conversation,
    RunStatus,
    ScenarioRun,
    Tenant,
)
from app.errors import Conflict
from app.jobs.queue import cancel, cancel_prefix, enqueue
from app.jobs.worker import JobContext, Retry
from app.logging import get_logger
from app.scenarios.events import Event
from app.scenarios.schema import ActionEvent, ScenarioDef
from app.scenarios.store import expires_at, load_definition

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

BUSY_RETRY_S = 5.0

Body = Callable[[AsyncSession, Conversation, ScenarioRun, ScenarioDef], Awaitable[None]]


async def _with_run(services: "Services", ctx: JobContext, run_id: uuid.UUID, body: Body) -> None:
    async with services.sm() as session:
        found = await session.get(ScenarioRun, run_id)
        if found is None or found.status not in OPEN_STATUSES:
            return
        conversation_id = found.conversation_id
    owner = f"job:{ctx.id}"
    try:
        await lease.acquire(
            services.sm,
            conversation_id,
            owner,
            services.clock,
            seconds=services.settings.lease_seconds,
        )
    except Conflict as exc:
        raise Retry(BUSY_RETRY_S, "conversation busy") from exc
    try:
        async with services.sm() as session, session.begin():
            conv = await lease.hold(session, conversation_id, owner)
            run = await session.get(
                ScenarioRun, run_id, with_for_update=True, populate_existing=True
            )
            if run is not None and run.status in OPEN_STATUSES:
                defn = await load_definition(session, run.scenario_id)
                await body(session, conv, run, defn)
            lease.release_in(conv)
    except BaseException:
        await lease.release(services.sm, conversation_id, owner)
        raise


def _log_event(run: ScenarioRun, event: Event, now: datetime) -> None:
    run.events = [*(run.events or []), event.log_entry(now)]


async def silence_minutes(
    session: AsyncSession, run: ScenarioRun, defn: ScenarioDef, index: int
) -> float:
    action = defn.actions[index]
    if action.after_minutes:
        return action.after_minutes
    tenant = await session.get(Tenant, run.tenant_id)
    channel = await session.get(Channel, run.channel_id)
    return effective_settings(
        tenant.settings if tenant else {}, channel.settings if channel else None
    ).silence_minutes


async def silence_check_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    run_id = uuid.UUID(ctx.payload["run_id"])
    index = int(ctx.payload["action"])

    async def body(
        session: AsyncSession, conv: Conversation, run: ScenarioRun, defn: ScenarioDef
    ) -> None:
        now = services.clock.now()
        if run.status == RunStatus.WAITING:
            return  # collected, waiting for an event: the client owes nothing
        if index >= len(defn.actions) or defn.actions[index].on != ActionEvent.SILENCE:
            log.warning("silence.action_gone", run_id=str(run.id), index=index)
            return
        minutes = await silence_minutes(session, run, defn, index)
        since = conv.last_client_at or run.last_activity_at
        due = since + timedelta(minutes=minutes)
        if due > now:
            # the client wrote after this timer was set and the turn did not move it
            await enqueue(
                session,
                "silence_check",
                {"run_id": str(run.id), "action": index},
                run_at=due,
                tenant_id=run.tenant_id,
                dedupe_key=f"silence:{run.id}:{index}",
            )
            return
        event = Event(ActionEvent.SILENCE)
        _log_event(run, event, now)
        await fire(session, services, run, defn, [event], now=now, only_action=index)
        log.info("silence.fired", run_id=str(run.id), action=index)

    await _with_run(services, ctx, run_id, body)


async def expire_run_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    run_id = uuid.UUID(ctx.payload["run_id"])

    async def body(
        session: AsyncSession, conv: Conversation, run: ScenarioRun, defn: ScenarioDef
    ) -> None:
        now = services.clock.now()
        at = expires_at(defn, run.last_activity_at, status=run.status, data=run.data)
        if at is None:
            return
        if at > now:
            await enqueue(
                session,
                "expire_run",
                {"run_id": str(run.id)},
                run_at=at,
                tenant_id=run.tenant_id,
                dedupe_key=f"expire:{run.id}",
            )
            return
        await abandon(session, services, run, defn, now)

    await _with_run(services, ctx, run_id, body)


async def abandon(
    session: AsyncSession,
    services: "Services",
    run: ScenarioRun,
    defn: ScenarioDef,
    now: datetime,
) -> None:
    run.status = RunStatus.ABANDONED
    run.asking = None
    run.finished_at = now
    event = Event(ActionEvent.ABANDONED)
    _log_event(run, event, now)
    await cancel_prefix(session, f"silence:{run.id}:")
    await cancel(session, f"expire:{run.id}")
    await fire(session, services, run, defn, [event], now=now)
    log.info("scenario.abandoned", run_id=str(run.id))
