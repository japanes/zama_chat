"""Scenario actions: `on: <event>` → `do: <handler>` (docs/04 §4 `actions`).

Called inside a transaction with the run row locked: in the turn's write (events of
the turn) and in the silence / expire jobs. A handler only writes rows and queues jobs —
nothing goes outside before the commit, so a failed turn sends nothing.

Handlers:
- http_post: the result JSON to an endpoint (`endpoint: <name>`);
- notify_telegram / notify_email: tell the team through a notifier
  (`params: {notifier: <name>}`), with the answers collected so far;
- message_client: a message from the bot (`params: {text: LocalizedText}`), only when
  the channel may write now (Instagram: the 24-hour window) and nobody took over;
- handover: the bot goes quiet in this conversation, an operator gets a task.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.outbox import queue_outgoing
from app.db.models import Conversation, MessageRole, NotifierKind, ScenarioRun
from app.errors import Conflict
from app.logging import get_logger
from app.notify.service import conversation_values, find_notifier, notify_event, queue
from app.operator import tasks as operator_tasks
from app.results.delivery import queue_delivery
from app.results.service import result_status
from app.scenarios.events import Event
from app.scenarios.schema import Action, ActionEvent, ScenarioDef, pick_text

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


MAX_FIELD_TEXT = 300


def fields_text(run: ScenarioRun, definition: ScenarioDef) -> str:
    """The answers so far, one per line, in the scenario's field order."""
    lines = []
    for f in definition.fields:
        value = (run.fields or {}).get(f.key, {}).get("value")
        if value in (None, "", []):
            continue
        text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        if len(text) > MAX_FIELD_TEXT:
            text = text[: MAX_FIELD_TEXT - 1] + "…"
        lines.append(f"{f.key}: {text}")
    return "\n".join(lines)


async def _conversation(ctx: ActionContext) -> Conversation:
    # in a turn's write this is the conversation row the turn holds (identity map)
    conv = await ctx.session.get(Conversation, ctx.run.conversation_id)
    assert conv is not None
    return conv


def _notify_handler(kind: str) -> ActionHandler:
    async def notify(ctx: ActionContext) -> None:
        name = str(ctx.action.params.get("notifier") or "")
        notifier = await find_notifier(ctx.session, ctx.run.tenant_id, name)
        if notifier is None or notifier.kind != kind:
            log.error("action.notifier_missing", notifier=name, kind=kind, run_id=str(ctx.run.id))
            return
        if not notifier.is_active:
            return
        conv = await _conversation(ctx)
        values = await conversation_values(ctx.session, ctx.services, conv)
        values.update(
            scenario=pick_text(
                ctx.definition.title, notifier.language, ctx.definition.language
            ).text,
            event=ctx.event.name,
            field=ctx.event.field or "",
            fields=fields_text(ctx.run, ctx.definition),
        )
        await queue(ctx.session, notifier, "scenario", values, now=ctx.now)

    return notify


async def message_client(ctx: ActionContext) -> None:
    text_value = ctx.action.params.get("text")
    if not text_value:  # check_definition does not let this through
        return
    conv = await _conversation(ctx)
    if conv.handover:
        return  # a person talks to the client now
    language = conv.reply_language or ctx.run.reply_language or ctx.definition.language
    text = pick_text(text_value, language, ctx.definition.language).text
    # TODO(review): a text missing in the reply language goes out in the scenario's main
    # language here (no translation call inside the write transaction)
    try:
        await queue_outgoing(
            ctx.session,
            ctx.services,
            conv,
            text,
            role=MessageRole.BOT,
            now=ctx.now,
            meta={"source": "action", "scenario": ctx.definition.key, "event": ctx.event.name},
        )
    except Conflict as exc:
        log.info("action.message_skipped", reason=exc.code, run_id=str(ctx.run.id))


async def handover(ctx: ActionContext) -> None:
    conv = await _conversation(ctx)
    if conv.handover:
        return
    conv.handover = True
    conv.handover_at = ctx.now
    text = f"{ctx.definition.key}: {ctx.event.name}"
    _task, created = await operator_tasks.open_handover(
        ctx.session, conv, turn_id=None, text=text, now=ctx.now
    )
    if created:
        values = await conversation_values(ctx.session, ctx.services, conv, text=text)
        await notify_event(
            ctx.session, tenant_id=conv.tenant_id, event="handover", values=values, now=ctx.now
        )


HANDLERS: dict[str, ActionHandler] = {
    "http_post": http_post,
    "notify_telegram": _notify_handler(NotifierKind.TELEGRAM),
    "notify_email": _notify_handler(NotifierKind.EMAIL),
    "message_client": message_client,
    "handover": handover,
}


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
