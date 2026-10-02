"""Scenario actions: `on: <event>` → `do: <handler>` (docs/04 §4 `actions`).

Called inside a transaction with the run row locked: in the turn's write (events of
the turn) and in the silence / expire jobs. A handler only writes rows and queues jobs —
nothing goes outside before the commit, so a failed turn sends nothing.

Stage 2 handler: http_post (the result JSON to an endpoint). Stage 4 adds notify_telegram,
notify_email, message_client, start_scenario, handover.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScenarioRun
from app.logging import get_logger
from app.results.delivery import queue_delivery
from app.results.service import result_status
from app.scenarios.events import Event
from app.scenarios.schema import Action, ActionEvent, ScenarioDef

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)


@dataclass
class ActionContext:
    session: AsyncSession
    services: "Services"
    run: ScenarioRun
    definition: ScenarioDef
    action: Action
    index: int
    event: Event
    now: datetime


ActionHandler = Callable[[ActionContext], Awaitable[None]]


async def http_post(ctx: ActionContext) -> None:
    if ctx.action.endpoint is None:  # check_definition does not let this through
        log.error("action.endpoint_missing", scenario=ctx.definition.key, index=ctx.index)
        return
    await queue_delivery(
        ctx.session,
        ctx.run,
        ctx.definition,
        endpoint_name=ctx.action.endpoint,
        event=ctx.event.name,
        status=result_status(ctx.event.name, ctx.run.status, ctx.action.status),
        now=ctx.now,
    )


HANDLERS: dict[str, ActionHandler] = {"http_post": http_post}


def matches(action: Action, event: Event) -> bool:
    if action.on != event.name:
        return False
    return action.field is None or action.field == event.field


async def fire(
    session: AsyncSession,
    services: "Services",
    run: ScenarioRun,
    definition: ScenarioDef,
    events: Iterable[Event],
    *,
    now: datetime,
    only_action: int | None = None,
) -> int:
    """Run the actions matching the events, in the order of the events. Silence actions
    run only from the silence job (`only_action` = the action's index). Returns how many
    actions ran."""
    done = 0
    for event in events:
        for index, action in enumerate(definition.actions):
            if only_action is not None:
                if index != only_action:
                    continue
            elif action.on == ActionEvent.SILENCE:
                continue
            if not matches(action, event):
                continue
            handler = HANDLERS.get(action.do)
            if handler is None:
                log.error("action.unknown", do=action.do, scenario=definition.key)
                continue
            await handler(
                ActionContext(session, services, run, definition, action, index, event, now)
            )
            done += 1
    return done
