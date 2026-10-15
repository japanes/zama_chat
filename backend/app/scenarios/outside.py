"""Changes of a scenario run from outside a turn: an operator's decision on a receipt in
review, an external event (POST /v1/events), the public API of runs (api/v1/runs.py). Like
a turn and the timers, they take the conversation lease first — never in the middle of a
turn — and write in one transaction: the run, its events → actions, the bot's message to
the client, the timers.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.outbox import queue_outgoing
from app.core import lease
from app.db.models import (
    OPEN_STATUSES,
    Conversation,
    MessageRole,
    RunStatus,
    ScenarioRun,
)
from app.errors import Conflict
from app.jobs.queue import cancel, cancel_prefix, enqueue
from app.logging import get_logger
from app.scenarios import fields as fl
from app.scenarios.events import Event
from app.scenarios.schema import ActionEvent, Completion, ScenarioDef, pick_text
from app.scenarios.store import expires_at

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

# a turn takes 10–30 s; an operator's click or an event waits this long for it
WAIT_SECONDS = 15.0

# scenario_runs.data.waiting_for of a `completion: by_caller` run that asked everything
WAITING_FIELDS = "fields"

# TODO(review): the bot's words after a person decided on a receipt
RECEIPT_TEXTS: dict[str, dict[str, str]] = {
    "accepted": {
        "en": "Thank you, the payment is confirmed.",
        "uk": "Дякуємо, оплату підтверджено.",
        "ru": "Спасибо, оплата подтверждена.",
    },
    "rejected": {
        "en": "Unfortunately we could not confirm the payment. Please send the correct receipt.",
        "uk": "На жаль, ми не змогли підтвердити оплату. Надішліть, будь ласка, правильну "
        "квитанцію.",
        "ru": "К сожалению, мы не смогли подтвердить оплату. Пришлите, пожалуйста, "
        "правильную квитанцию.",
    },
}


@dataclass
class Change:
    """What happened to the run: events for the log and the actions, the bot's words."""

    events: list[Event] = field(default_factory=list)
    say: str | None = None

    def add(self, name: str, field_key: str | None = None) -> None:
        self.events.append(Event(name, field_key))


async def under_lease[T](
    services: "Services",
    conversation_id: uuid.UUID,
    owner: str,
    body: Callable[[AsyncSession, Conversation], Awaitable[T]],
    *,
    wait_seconds: float = WAIT_SECONDS,
) -> T:
    """Take the lease, waiting a little for a running turn (still busy →
    Conflict("conversation.busy"), the caller answers 409), run `body` in one transaction
    with the conversation row locked, release."""
    await lease.acquire(
        services.sm,
        conversation_id,
        owner,
        services.clock,
        seconds=services.settings.lease_seconds,
        wait_seconds=wait_seconds,
    )
    try:
        async with services.sm() as session, session.begin():
            conv = await lease.hold(session, conversation_id, owner)
            result = await body(session, conv)
            lease.release_in(conv)
            return result
    except BaseException:
        await lease.release(services.sm, conversation_id, owner)
        raise


async def lock_run(session: AsyncSession, run_id: uuid.UUID) -> ScenarioRun | None:
    return await session.get(ScenarioRun, run_id, with_for_update=True, populate_existing=True)


async def back_status(session: AsyncSession, run: ScenarioRun) -> str:
    """A run that waited goes back to the client: into the focus, or paused when another
    run of the conversation has it."""
    other = (
        await session.execute(
            select(ScenarioRun.id).where(
                ScenarioRun.conversation_id == run.conversation_id,
                ScenarioRun.status == RunStatus.FOCUS,
                ScenarioRun.id != run.id,
            )
        )
    ).first()
    return RunStatus.PAUSED if other is not None else RunStatus.FOCUS


def confirmed(defn: ScenarioDef, data: dict[str, Any]) -> bool:
    return not defn.needs_confirm or bool(data.get("confirmed_at"))


async def settle(
    session: AsyncSession,
    run: ScenarioRun,
    defn: ScenarioDef,
    states: dict[str, fl.FieldState],
    data: dict[str, Any],
    change: Change,
    now: datetime,
) -> None:
    """After an outside change: everything collected (and confirmed) → completed, or
    waiting for an event / for the caller's questions (by_caller); else the run goes back to
    the client asking the next field."""
    if fl.all_complete(states) and confirmed(defn, data):
        run.asking = None
        if defn.completion == Completion.EXTERNAL_EVENT:
            run.status = RunStatus.WAITING
            data.update(waiting_for="event", waiting_since=now.isoformat())
            data.pop("receipt_id", None)
            change.add(ActionEvent.WAITING)
        elif defn.completion == Completion.BY_CALLER:
            if run.status != RunStatus.WAITING:
                run.status = RunStatus.WAITING
                data.update(waiting_for=WAITING_FIELDS, waiting_since=now.isoformat())
                change.add(ActionEvent.WAITING)
        else:
            run.status = RunStatus.COMPLETED
            run.finished_at = now
            change.add(ActionEvent.COMPLETED)
        return
    run.status = await back_status(session, run)
    for key in ("waiting_for", "waiting_since", "receipt_id"):
        data.pop(key, None)
    nxt = fl.next_field(defn, states, confirmed=confirmed(defn, data))
    run.asking = nxt.key if nxt is not None else None


async def write_run(
    session: AsyncSession,
    services: "Services",
    conv: Conversation,
    run: ScenarioRun,
    defn: ScenarioDef,
    states: dict[str, fl.FieldState],
    data: dict[str, Any],
    change: Change,
    now: datetime,
    *,
    meta: dict[str, Any],
) -> None:
    """Store the run, log its events, fire its actions, move its timers, send `say`."""
    from app.actions.registry import fire

    run.fields = fl.dump_states(states)
    run.data = dict(data)
    run.events = [*(run.events or []), *(e.log_entry(now) for e in change.events)]
    run.last_activity_at = now
    if run.status not in OPEN_STATUSES and run.finished_at is None:
        run.finished_at = now
    await session.flush()
    if change.events:
        await fire(session, services, run, defn, change.events, now=now)
    await move_timers(session, services, run, defn, now)
    if change.say and not conv.handover:
        try:
            await queue_outgoing(
                session,
                services,
                conv,
                change.say,
                role=MessageRole.BOT,
                now=now,
                meta={"source": "scenario", "scenario": defn.key, **meta},
            )
        except Conflict as exc:
            # Instagram: more than 24 h since the client's last message
            log.info("scenario.say_skipped", reason=exc.code, run_id=str(run.id))


async def move_timers(
    session: AsyncSession, services: "Services", run: ScenarioRun, defn: ScenarioDef, now: datetime
) -> None:
    if run.status not in OPEN_STATUSES or run.status == RunStatus.WAITING:
        await cancel_prefix(session, f"silence:{run.id}:")
    else:
        from app.scenarios.jobs import silence_minutes

        for index, action in enumerate(defn.actions):
            if action.on != ActionEvent.SILENCE:
                continue
            minutes = await silence_minutes(session, run, defn, index)
            await enqueue(
                session,
                "silence_check",
                {"run_id": str(run.id), "action": index},
                run_at=now + timedelta(minutes=minutes),
                tenant_id=run.tenant_id,
                dedupe_key=f"silence:{run.id}:{index}",
            )
    at = (
        expires_at(defn, run.last_activity_at, status=run.status, data=run.data)
        if run.status in OPEN_STATUSES
        else None
    )
    if at is None:
        await cancel(session, f"expire:{run.id}")
        return
    await enqueue(
        session,
        "expire_run",
        {"run_id": str(run.id)},
        run_at=at,
        tenant_id=run.tenant_id,
        dedupe_key=f"expire:{run.id}",
    )


def waiting_say(defn: ScenarioDef, language: str) -> str | None:
    """What a by_caller run says, word for word, when it starts waiting outside a turn
    (`waiting.say`); None — nothing (a turn words it, scenarios/plan.py)."""
    if defn.waiting.say is None:
        return None
    return pick_text(defn.waiting.say, language, defn.language).text


def language_of(conv: Conversation, run: ScenarioRun, defn: ScenarioDef) -> str:
    return conv.reply_language or run.reply_language or defn.language


def receipt_text(kind: str, language: str) -> str:
    texts = RECEIPT_TEXTS[kind]
    return texts.get(language) or texts["en"]


def apply_receipt_decision(
    defn: ScenarioDef,
    states: dict[str, fl.FieldState],
    data: dict[str, Any],
    *,
    field_key: str,
    accept: bool,
    language: str,
    change: Change,
    now: datetime,
) -> None:
    """The person's verdict on the receipt field: accepted → filled; rejected → the
    client is asked for a correct receipt (issue receipt_rejected)."""
    from app.scenarios.events import RECEIPT_ACCEPTED, RECEIPT_REJECTED

    fs = states[field_key]
    fs.issue = None if accept else "receipt_rejected"
    fs.issue_params = {}
    fl.set_checked(fs, [], now)  # rejected: partial with the issue
    if accept:
        change.add(RECEIPT_ACCEPTED, field_key)
        change.add(ActionEvent.FIELD_FILLED, field_key)
    else:
        change.add(RECEIPT_REJECTED, field_key)
    data.pop("receipt_id", None)
    change.say = receipt_text("accepted" if accept else "rejected", language)


def fill_say(
    defn: ScenarioDef,
    text: str | dict[str, str] | None,
    language: str,
    states: dict[str, fl.FieldState],
    data: dict[str, Any],
) -> str | None:
    """`say` of an event rule: {field_key} → the client's answer, {data.name} → the
    event's data. Unknown names stay as they are."""
    if not text:
        return None
    template = pick_text(text, language, defn.language).text
    values: dict[str, Any] = {k: fs.value or "" for k, fs in states.items()}
    values.update({f"data.{k}": v for k, v in data.items() if not isinstance(v, dict | list)})

    out = template
    for name, value in values.items():
        out = out.replace("{" + name + "}", str(value))
    return out
