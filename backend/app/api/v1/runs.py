"""Scenario runs over the public API — for a caller that shows the dialogue in its own
interface (one question at a time, its own «skip» and «confirm» buttons) instead of a chat
window.

  GET  /v1/scenarios/{key}     the scenario's questions, before any run exists
  POST /v1/runs/start          start the given scenario for a contact; the bot asks the
                               first question without a model call
  POST /v1/runs/message        one client message → the bot's reply, the run after the turn
                               and what the turn cost
  POST /v1/runs/state          the run, its questions and answers, the dialogue's messages
  POST /v1/runs/{id}/skip      close the asked question without an answer
  POST /v1/runs/{id}/confirm   the «yes, everything is correct» given in the caller's
                               interface

Auth: `Authorization: Bearer <api key>`. The key belongs to one channel: only scenarios
switched on in that channel start, only runs of that channel are seen.

A contact has one conversation, a conversation one open run per scenario — a caller that
needs several dialogues of one scenario uses one contact per dialogue.

Changes of a run outside a turn (start / skip / confirm) take the conversation lease like
POST /v1/events does and write through scenarios/outside.py.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ApiClient, ApiClientDep, ServicesDep
from app.api.v1.messages import ContactIn, MessageIn, MessageOut, post_message
from app.core.ingest import upsert_contact, upsert_conversation
from app.core.language import REPLY_LANGUAGE_PIN, normalize_code
from app.core.tenant_settings import effective_settings
from app.db.base import new_id
from app.db.models import (
    OPEN_STATUSES,
    Channel,
    ChannelScenario,
    Contact,
    Conversation,
    Decision,
    DecisionProvider,
    LlmCall,
    Message,
    MessageRole,
    RunStatus,
    Scenario,
    ScenarioRun,
    SendStatus,
    Tenant,
    Turn,
)
from app.errors import AppError, Conflict, NotFound
from app.scenarios import fields as fl
from app.scenarios.events import SKIPPED
from app.scenarios.outside import Change, confirmed, lock_run, settle, under_lease, write_run
from app.scenarios.plan import CONFIRM_QUESTION
from app.scenarios.questions import CONFIRM
from app.scenarios.schema import ActionEvent, ScenarioDef, parse_definition
from app.scenarios.store import load_definition
from app.services import Services

router = APIRouter(prefix="/v1", tags=["runs"])

# a run the client still talks in
TALKING = (RunStatus.FOCUS, RunStatus.PAUSED)


# --- what the caller gets -----------------------------------------------------------------


class FieldOut(BaseModel):
    key: str
    title: str | None
    question: str
    # empty / partial / filled / review
    state: str
    value: str | None
    # closed: has an accepted answer or was skipped
    complete: bool
    skipped: bool
    reasks: int
    stuck: bool


class RunOut(BaseModel):
    id: uuid.UUID
    scenario: str
    version: int
    title: str
    # focus / paused / waiting / completed / cancelled / abandoned
    status: str
    # the question the bot asked last: a field key, "@confirm" or null
    asking: str | None
    language: str
    needs_confirm: bool
    confirmed: bool
    # text of «is everything correct?» in the run's language
    confirm_question: str | None
    fields: list[FieldOut]
    started_at: datetime
    finished_at: datetime | None


class MessageRow(BaseModel):
    id: uuid.UUID
    role: str
    text: str
    created_at: datetime
    turn_id: uuid.UUID | None


class StateOut(BaseModel):
    conversation_id: uuid.UUID | None
    run: RunOut | None
    messages: list[MessageRow] = []


class UsageCall(BaseModel):
    # llm — a model call; decisions — the turn's yes/no questions answered by the decision
    # service (one line for all of them)
    kind: str
    role: str
    model: str
    prompt: str | None = None
    tokens_in: int
    tokens_out: int
    cost: float
    latency_ms: int | None = None
    error: str | None = None


class UsageOut(BaseModel):
    turn_id: uuid.UUID
    tokens_in: int
    tokens_out: int
    cost: float
    duration_ms: int | None
    calls: list[UsageCall]


class RunMessageOut(MessageOut):
    run: RunOut | None
    usage: UsageOut | None


def _text(defn: ScenarioDef, value: str | dict[str, str] | None, language: str) -> str | None:
    return defn.text(value, language).text if value else None


def _confirm_question(defn: ScenarioDef, language: str) -> str | None:
    if not defn.needs_confirm:
        return None
    own = defn.confirm.question if defn.confirm is not None else None
    if own is not None:
        return _text(defn, own, language)
    return CONFIRM_QUESTION.get(language, CONFIRM_QUESTION["en"])


def run_out(run: ScenarioRun, defn: ScenarioDef) -> RunOut:
    language = run.reply_language or defn.language
    states = fl.load_states(defn, run.fields)
    return RunOut(
        id=run.id,
        scenario=run.scenario_key,
        version=run.scenario_version,
        title=_text(defn, defn.title, language) or defn.key,
        status=run.status,
        asking=run.asking,
        language=language,
        needs_confirm=defn.needs_confirm,
        confirmed=confirmed(defn, dict(run.data or {})),
        confirm_question=_confirm_question(defn, language),
        fields=[
            FieldOut(
                key=f.key,
                title=_text(defn, f.title, language),
                question=_text(defn, f.question, language) or "",
                state=states[f.key].state,
                value=states[f.key].value,
                complete=states[f.key].complete,
                skipped=states[f.key].skipped,
                reasks=states[f.key].reasks,
                stuck=states[f.key].stuck,
            )
            for f in defn.fields
        ],
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


# --- reading ------------------------------------------------------------------------------


async def _conversation_of(
    session: AsyncSession, client: ApiClient, external_id: str
) -> Conversation | None:
    return (
        await session.execute(
            select(Conversation)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(
                Conversation.channel_id == client.channel_id,
                Contact.external_id == external_id,
            )
        )
    ).scalar_one_or_none()


async def _run_of(
    session: AsyncSession, conversation_id: uuid.UUID, scenario: str
) -> ScenarioRun | None:
    """The open run of the scenario; without one — the last closed."""
    rows = (
        await session.execute(
            select(ScenarioRun)
            .where(
                ScenarioRun.conversation_id == conversation_id,
                ScenarioRun.scenario_key == scenario,
            )
            .order_by(ScenarioRun.started_at.desc(), ScenarioRun.id.desc())
        )
    ).scalars()
    runs = list(rows)
    return next((r for r in runs if r.status in OPEN_STATUSES), runs[0] if runs else None)


async def _messages(
    session: AsyncSession, conversation_id: uuid.UUID, since: datetime | None, limit: int
) -> list[MessageRow]:
    if limit <= 0:
        return []
    query = select(Message).where(
        Message.conversation_id == conversation_id,
        Message.role != MessageRole.SYSTEM,
        Message.send_status.is_distinct_from(SendStatus.DROPPED),
    )
    if since is not None:
        query = query.where(Message.created_at >= since)
    rows = (
        await session.execute(
            query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
        )
    ).scalars()
    return [
        MessageRow(id=m.id, role=m.role, text=m.text, created_at=m.created_at, turn_id=m.turn_id)
        for m in reversed(list(rows))
    ]


async def _state(
    services: Services,
    conversation_id: uuid.UUID,
    *,
    scenario: str | None = None,
    run_id: uuid.UUID | None = None,
    messages: int = 0,
) -> StateOut:
    async with services.sm() as session:
        if run_id is not None:
            run = await session.get(ScenarioRun, run_id)
        else:
            assert scenario is not None
            run = await _run_of(session, conversation_id, scenario)
        if run is None:
            return StateOut(conversation_id=conversation_id, run=None)
        defn = await load_definition(session, run.scenario_id)
        rows = await _messages(session, conversation_id, run.started_at, messages)
        return StateOut(conversation_id=conversation_id, run=run_out(run, defn), messages=rows)


class StateIn(BaseModel):
    contact: ContactIn
    scenario: str = Field(min_length=1, max_length=64)
    # how many last messages of the run's dialogue to return (0 — none)
    messages: int = Field(default=0, ge=0, le=500)


@router.post("/runs/state", response_model=StateOut)
async def run_state(body: StateIn, client: ApiClientDep, services: ServicesDep) -> StateOut:
    async with services.sm() as session:
        conv = await _conversation_of(session, client, body.contact.external_id)
    if conv is None:
        return StateOut(conversation_id=None, run=None)
    return await _state(services, conv.id, scenario=body.scenario, messages=body.messages)


# --- start --------------------------------------------------------------------------------


class StartIn(BaseModel):
    contact: ContactIn
    # key of a scenario switched on in the key's channel
    scenario: str = Field(min_length=1, max_length=64)
    # the language the bot speaks in this conversation, whatever language the client writes
    # in (one of the tenant's languages). Without it the tenant's rules decide, as in a chat.
    language: str | None = Field(default=None, max_length=32)
    # questions the caller already has answers to: closed without an answer, never asked
    skip: list[str] = Field(default_factory=list, max_length=200)
    messages: int = Field(default=0, ge=0, le=500)


async def _channel_scenario(session: AsyncSession, client: ApiClient, key: str) -> Scenario:
    row = (
        await session.execute(
            select(Scenario)
            .join(
                ChannelScenario,
                (ChannelScenario.scenario_key == Scenario.key)
                & (ChannelScenario.channel_id == client.channel_id),
            )
            .where(
                Scenario.tenant_id == client.tenant_id,
                Scenario.key == key,
                Scenario.is_active,
                ChannelScenario.is_enabled,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("scenario.not_found", {"scenario": key})
    return row


async def _run_language(session: AsyncSession, client: ApiClient, wanted: str | None) -> str | None:
    if wanted is None:
        return None
    code = normalize_code(wanted)
    tenant = await session.get(Tenant, client.tenant_id)
    channel = await session.get(Channel, client.channel_id)
    assert tenant is not None
    settings = effective_settings(tenant.settings, channel.settings if channel else None)
    if code is None or code not in settings.languages:
        raise AppError(
            "run.language_not_supported",
            {"language": wanted, "languages": list(settings.languages)},
            status=422,
        )
    return code


class ScenarioField(BaseModel):
    key: str
    title: str | None
    question: str


class ScenarioOut(BaseModel):
    key: str
    version: int
    title: str
    language: str
    needs_confirm: bool
    confirm_question: str | None
    fields: list[ScenarioField]


@router.get("/scenarios/{key}", response_model=ScenarioOut)
async def scenario_questions(
    key: str, client: ApiClientDep, services: ServicesDep, language: str | None = None
) -> ScenarioOut:
    """The questions of a scenario switched on in the key's channel, in the order they are
    asked — for a caller that has to know them before a run exists (which ones to pass to
    `skip` at the start, how many there are). Texts are in `language` (one of the tenant's
    languages), without it in the scenario's own."""
    async with services.sm() as session:
        scenario = await _channel_scenario(session, client, key)
        defn = parse_definition(scenario.definition)
        code = await _run_language(session, client, language) or defn.language
    return ScenarioOut(
        key=scenario.key,
        version=scenario.version,
        title=_text(defn, defn.title, code) or defn.key,
        language=code,
        needs_confirm=defn.needs_confirm,
        confirm_question=_confirm_question(defn, code),
        fields=[
            ScenarioField(
                key=f.key,
                title=_text(defn, f.title, code),
                question=_text(defn, f.question, code) or "",
            )
            for f in defn.fields
        ],
    )


@router.post("/runs/start", response_model=StateOut)
async def start_run(body: StartIn, client: ApiClientDep, services: ServicesDep) -> StateOut:
    """Starts the scenario and asks its first question (the scenario's own text, no model
    call). The scenario is already running for this contact → nothing changes, its state is
    returned: the dialogue goes on from the question it stopped at."""
    async with services.sm() as session, session.begin():
        scenario = await _channel_scenario(session, client, body.scenario)
        defn = parse_definition(scenario.definition)
        unknown = [key for key in body.skip if key not in defn.field_keys]
        if unknown:
            raise AppError("run.unknown_fields", {"fields": unknown}, status=422)
        language = await _run_language(session, client, body.language)
        contact = await upsert_contact(
            session,
            tenant_id=client.tenant_id,
            channel_id=client.channel_id,
            external_id=body.contact.external_id,
            name=body.contact.name,
            username=body.contact.username,
        )
        conversation_id = await upsert_conversation(
            session,
            tenant_id=client.tenant_id,
            channel_id=client.channel_id,
            contact_id=contact.id,
        )

    async def body_fn(session: AsyncSession, conv: Conversation) -> uuid.UUID:
        return await _start(session, services, conv, scenario, defn, language, body.skip)

    run_id = await under_lease(services, conversation_id, f"api:{uuid.uuid4().hex}", body_fn)
    return await _state(services, conversation_id, run_id=run_id, messages=body.messages)


async def _start(
    session: AsyncSession,
    services: Services,
    conv: Conversation,
    scenario: Scenario,
    defn: ScenarioDef,
    language: str | None,
    skip: list[str],
) -> uuid.UUID:
    now = services.clock.now()
    open_runs = list(
        (
            await session.execute(
                select(ScenarioRun)
                .where(
                    ScenarioRun.conversation_id == conv.id,
                    ScenarioRun.status.in_(OPEN_STATUSES),
                )
                .with_for_update()
            )
        ).scalars()
    )
    same = next((r for r in open_runs if r.scenario_key == defn.key), None)
    if same is not None:
        return same.id
    focus = next((r for r in open_runs if r.status == RunStatus.FOCUS), None)
    if focus is not None:
        raise Conflict("run.other_in_focus", {"scenario": focus.scenario_key})

    if language is not None:
        conv.state = {**(conv.state or {}), REPLY_LANGUAGE_PIN: language}
        conv.reply_language = language
    said_in = language or conv.reply_language or defn.language

    states = fl.load_states(defn, {})
    change = Change()
    change.add(ActionEvent.STARTED)
    for key in skip:
        fl.set_skipped(states[key], now)
        change.add(SKIPPED, key)

    run = ScenarioRun(
        id=new_id(),
        tenant_id=conv.tenant_id,
        channel_id=conv.channel_id,
        conversation_id=conv.id,
        scenario_id=scenario.id,
        scenario_key=defn.key,
        scenario_version=scenario.version,
        status=RunStatus.FOCUS,
        fields={},
        events=[],
        data={},
        delivery_seq=0,
        client_language=conv.client_language,
        reply_language=said_in,
        started_at=now,
        last_activity_at=now,
    )
    data: dict[str, Any] = {}
    _ask_next(run, defn, states, data, change, said_in)
    session.add(run)
    await write_run(session, services, conv, run, defn, states, data, change, now, meta=_META)
    return run.id


_META = {"via": "api"}


def _ask_next(
    run: ScenarioRun,
    defn: ScenarioDef,
    states: dict[str, fl.FieldState],
    data: dict[str, Any],
    change: Change,
    language: str,
) -> None:
    """The question an open run asks now, in the scenario's own words: the next field, or
    «is everything correct?» when everything is collected."""
    nxt = fl.next_field(defn, states, confirmed=confirmed(defn, data))
    if nxt is not None:
        fl.register_ask(states, run.asking, nxt.key)
        run.asking = nxt.key
        change.say = _text(defn, nxt.question, language)
    elif fl.complete_except_receipts(defn, states) and not confirmed(defn, data):
        run.asking = CONFIRM
        change.say = _confirm_question(defn, language)
    else:
        run.asking = None


# --- a message ------------------------------------------------------------------------------


class RunMessageIn(MessageIn):
    # the scenario the caller's interface is showing: its run must be open, and its state
    # is returned. Without it — the conversation's run in the focus.
    scenario: str | None = Field(default=None, min_length=1, max_length=64)


@router.post("/runs/message", response_model=RunMessageOut)
async def run_message(
    body: RunMessageIn, client: ApiClientDep, services: ServicesDep
) -> RunMessageOut:
    """POST /v1/messages plus the run after the turn and the turn's cost. With `scenario` a
    message for a dialogue that is not running is refused before any model is called."""
    if body.scenario is not None:
        async with services.sm() as session:
            conv = await _conversation_of(session, client, body.contact.external_id)
            run = await _run_of(session, conv.id, body.scenario) if conv is not None else None
        if run is None or run.status not in TALKING:
            raise Conflict("run.not_open", {"status": run.status if run else None})

    out = await post_message(MessageIn(**body.model_dump(exclude={"scenario"})), client, services)

    async with services.sm() as session:
        if body.scenario is not None:
            run = await _run_of(session, out.conversation_id, body.scenario)
        else:
            run = (
                await session.execute(
                    select(ScenarioRun).where(
                        ScenarioRun.conversation_id == out.conversation_id,
                        ScenarioRun.status == RunStatus.FOCUS,
                    )
                )
            ).scalar_one_or_none()
        state = run_out(run, await load_definition(session, run.scenario_id)) if run else None
        usage = await _usage(session, out.turn_id) if out.turn_id is not None else None
    return RunMessageOut(**out.model_dump(), run=state, usage=usage)


async def _usage(session: AsyncSession, turn_id: uuid.UUID) -> UsageOut | None:
    """What the turn was paid for: every model call, and one line for the decision service."""
    turn = await session.get(Turn, turn_id)
    if turn is None:
        return None
    calls = [
        UsageCall(
            kind="llm",
            role=c.role,
            model=c.model,
            prompt=c.prompt_key,
            tokens_in=c.tokens_in or 0,
            tokens_out=c.tokens_out or 0,
            cost=float(c.cost or 0),
            latency_ms=c.latency_ms,
            error=c.error,
        )
        for c in (
            await session.execute(
                select(LlmCall).where(LlmCall.turn_id == turn_id).order_by(LlmCall.created_at)
            )
        ).scalars()
    ]
    # LLM-decider rows are already among the model calls; the decision service is paid apart
    jev = (
        await session.execute(
            select(
                Decision.model,
                func.coalesce(func.sum(Decision.tokens_in), 0),
                func.coalesce(func.sum(Decision.tokens_out), 0),
                func.coalesce(func.sum(Decision.cost), 0),
            )
            .where(Decision.turn_id == turn_id, Decision.provider == DecisionProvider.JEV)
            .group_by(Decision.model)
        )
    ).all()
    calls += [
        UsageCall(
            kind="decisions",
            role="decide",
            model=model,
            tokens_in=int(t_in),
            tokens_out=int(t_out),
            cost=float(Decimal(cost)),
        )
        for model, t_in, t_out, cost in jev
    ]
    return UsageOut(
        turn_id=turn.id,
        tokens_in=turn.tokens_in or 0,
        tokens_out=turn.tokens_out or 0,
        cost=float(turn.cost or 0),
        duration_ms=turn.duration_ms,
        calls=calls,
    )


# --- skip / confirm ---------------------------------------------------------------------------


async def _own_run(services: Services, client: ApiClient, run_id: uuid.UUID) -> ScenarioRun:
    async with services.sm() as session:
        run = await session.get(ScenarioRun, run_id)
    if run is None or run.tenant_id != client.tenant_id or run.channel_id != client.channel_id:
        raise NotFound("run.not_found", {"run_id": str(run_id)})
    return run


class SkipIn(BaseModel):
    # the question to close; without it — the one the bot asked last
    field: str | None = Field(default=None, min_length=1, max_length=64)
    messages: int = Field(default=0, ge=0, le=500)


@router.post("/runs/{run_id}/skip", response_model=StateOut)
async def skip_question(
    run_id: uuid.UUID, body: SkipIn, client: ApiClientDep, services: ServicesDep
) -> StateOut:
    """Closes a question without an answer and asks the next one (the scenario's own text,
    no model call). A question already closed → nothing changes."""
    found = await _own_run(services, client, run_id)

    async def body_fn(session: AsyncSession, conv: Conversation) -> None:
        await _skip(session, services, conv, run_id, body.field)

    await under_lease(services, found.conversation_id, f"api:{uuid.uuid4().hex}", body_fn)
    return await _state(services, found.conversation_id, run_id=run_id, messages=body.messages)


async def _skip(
    session: AsyncSession,
    services: Services,
    conv: Conversation,
    run_id: uuid.UUID,
    field_key: str | None,
) -> None:
    now = services.clock.now()
    run = await lock_run(session, run_id)
    assert run is not None
    if run.status not in TALKING:
        raise Conflict("run.not_open", {"status": run.status})
    defn = await load_definition(session, run.scenario_id)
    key = field_key or run.asking
    if key is None or key == CONFIRM:
        raise Conflict("run.nothing_asked")
    if key not in defn.field_keys:
        raise AppError("run.unknown_fields", {"fields": [key]}, status=422)
    states = fl.load_states(defn, run.fields)
    if states[key].skipped:
        return
    data = dict(run.data or {})
    change = Change()
    asked = run.asking
    had_answer = states[key].value is not None
    fl.set_skipped(states[key], now)
    change.add(SKIPPED, key)
    if had_answer:
        # the confirmed summary showed an answer that is gone now
        data.pop("confirmed_at", None)
    await settle(session, run, defn, states, data, change, now)
    # Another question was closed, not the one on the screen: that one is still waiting for
    # its answer and is neither said again nor counted as asked twice.
    if run.status in TALKING and (asked is None or key == asked):
        _ask_next(run, defn, states, data, change, run.reply_language or defn.language)
        if run.status != RunStatus.FOCUS:
            change.say = None  # put aside: the question is asked when the run comes back
    await write_run(session, services, conv, run, defn, states, data, change, now, meta=_META)


class ConfirmIn(BaseModel):
    messages: int = Field(default=0, ge=0, le=500)


@router.post("/runs/{run_id}/confirm", response_model=StateOut)
async def confirm_run(
    run_id: uuid.UUID, body: ConfirmIn, client: ApiClientDep, services: ServicesDep
) -> StateOut:
    """The client's «yes, everything is correct», given in the caller's interface: the run
    completes and its actions fire. Every question must be closed first (409 otherwise). A
    run already confirmed → nothing changes."""
    found = await _own_run(services, client, run_id)

    async def body_fn(session: AsyncSession, conv: Conversation) -> None:
        await _confirm(session, services, conv, run_id)

    await under_lease(services, found.conversation_id, f"api:{uuid.uuid4().hex}", body_fn)
    return await _state(services, found.conversation_id, run_id=run_id, messages=body.messages)


async def _confirm(
    session: AsyncSession, services: Services, conv: Conversation, run_id: uuid.UUID
) -> None:
    now = services.clock.now()
    run = await lock_run(session, run_id)
    assert run is not None
    data = dict(run.data or {})
    defn = await load_definition(session, run.scenario_id)
    if run.status in (RunStatus.COMPLETED, RunStatus.WAITING) or (
        run.status in TALKING and defn.needs_confirm and data.get("confirmed_at")
    ):
        return
    if run.status not in TALKING:
        raise Conflict("run.not_open", {"status": run.status})
    states = fl.load_states(defn, run.fields)
    if not fl.complete_except_receipts(defn, states):
        missing = [f.key for f in defn.fields if not states[f.key].complete]
        raise Conflict("run.not_collected", {"missing": missing})
    change = Change()
    if defn.needs_confirm:
        data["confirmed_at"] = now.isoformat()
        change.add(ActionEvent.CONFIRMED)
    await settle(session, run, defn, states, data, change, now)
    if run.status in TALKING:
        # a receipt is asked after the «yes»
        _ask_next(run, defn, states, data, change, run.reply_language or defn.language)
    await write_run(session, services, conv, run, defn, states, data, change, now, meta=_META)
