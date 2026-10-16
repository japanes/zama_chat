"""Scenario runs over the public API — for a caller that shows the dialogue in its own
interface (one question at a time, its own «skip» and «confirm» buttons) instead of a chat
window.

  GET  /v1/scenarios/{key}     the scenario's questions, before any run exists
  POST /v1/scenarios/{key}/check
                               answers the caller collected itself (not in a dialogue) →
                               which of them pass the filters of their questions; no run,
                               no conversation, nothing is written but the decisions log
  POST /v1/runs/start          start the given scenario for a contact; the bot asks the
                               first question without a model call
  POST /v1/runs/message        one client message → the bot's reply, the run after the turn
                               and what the turn cost
  POST /v1/runs/state          the run, its questions and answers, the dialogue's messages
  POST /v1/runs/{id}/skip      close the asked question without an answer
  POST /v1/runs/{id}/confirm   the «yes, everything is correct» given in the caller's
                               interface
  POST /v1/runs/{id}/fields    new questions for a run of a `completion: by_caller`
                               scenario (a running one or one that waits for them); the bot
                               asks the first of them in its own words (a bot turn, paid
                               like any) or word for word (`added.wording: as_sent`)
  POST /v1/runs/{id}/finish    «no more questions»: a `by_caller` run completes, silently

Auth: `Authorization: Bearer <api key>`. The key belongs to one channel: only scenarios
switched on in that channel start, only runs of that channel are seen.

A contact has one conversation, a conversation one open run per scenario — a caller that
needs several dialogues of one scenario uses one contact per dialogue.

Changes of a run outside a turn (start / skip / confirm / fields / finish) take the
conversation lease like POST /v1/events does and write through scenarios/outside.py.

A choice question with `options_from: caller` has no options in the file: they come with
POST /v1/runs/start (`options`) and live with the run (scenario_runs.extra), like the
added questions do.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ApiClient, ApiClientDep, ServicesDep
from app.api.v1.messages import ContactIn, MessageIn, MessageOut, post_message
from app.core.ingest import upsert_contact, upsert_conversation
from app.core.language import REPLY_LANGUAGE_PIN, normalize_code, reply_language_for
from app.core.pipeline import ask_pipeline
from app.core.tenant_settings import effective_settings
from app.core.turn import run_turn
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
from app.decisions.engine import DecideContext, DecisionSet
from app.errors import AppError, Conflict, NotFound
from app.scenarios import fields as fl
from app.scenarios.checks import CheckItem, answers_fit, ask_filters
from app.scenarios.events import FIELDS_ADDED, PAUSED, SKIPPED
from app.scenarios.outside import (
    WAITING_FIELDS,
    Change,
    back_status,
    confirmed,
    lock_run,
    settle,
    under_lease,
    waiting_say,
    write_run,
)
from app.scenarios.plan import CONFIRM_QUESTION
from app.scenarios.questions import CONFIRM
from app.scenarios.schema import (
    ActionEvent,
    AddedFields,
    Completion,
    FieldDef,
    FieldType,
    Option,
    ScenarioDef,
    check_definition,
    parse_definition,
)
from app.scenarios.store import (
    ADDED_AT,
    effective_definition,
    load_definition,
    load_run_definition,
)
from app.services import Services

router = APIRouter(prefix="/v1", tags=["runs"])

# a run the client still talks in
TALKING = (RunStatus.FOCUS, RunStatus.PAUSED)


# --- what the caller gets -----------------------------------------------------------------


class OptionOut(BaseModel):
    key: str
    label: str


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
    # how the bot asked it: its message before the answer
    asked: str | None = None
    # show_if said the question does not apply: not asked, what was said is kept
    hidden: bool = False
    # an added question closed after too many failed answers, with what the client said
    closed: bool = False
    # a choice question: its options (from the file or from POST /v1/runs/start)
    options: list[OptionOut] | None = None
    # an added question (POST /v1/runs/{id}/fields): when it came
    added_at: datetime | None = None


class RunOut(BaseModel):
    id: uuid.UUID
    scenario: str
    version: int
    title: str
    # focus / paused / waiting / completed / cancelled / abandoned
    status: str
    # the question the bot asked last: a field key, "@confirm" or null
    asking: str | None
    # status waiting: "event" (POST /v1/events), "receipt_review", "fields" (a by_caller run
    # asked everything it has and waits for POST /v1/runs/{id}/fields or /finish)
    waiting_for: str | None = None
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


def _options(defn: ScenarioDef, f: FieldDef, language: str) -> list[OptionOut] | None:
    if f.type != FieldType.CHOICE:
        return None
    return [OptionOut(key=o.key, label=_text(defn, o.label, language) or o.key) for o in f.options]


def _added_at(run: ScenarioRun) -> dict[str, datetime]:
    out = {}
    for raw in (run.extra or {}).get("fields") or []:
        at = raw.get(ADDED_AT)
        if isinstance(at, str):
            out[str(raw["key"])] = datetime.fromisoformat(at)
    return out


def run_out(run: ScenarioRun, defn: ScenarioDef) -> RunOut:
    language = run.reply_language or defn.language
    states = fl.load_states(defn, run.fields)
    added = _added_at(run)
    data = dict(run.data or {})
    return RunOut(
        id=run.id,
        scenario=run.scenario_key,
        version=run.scenario_version,
        title=_text(defn, defn.title, language) or defn.key,
        status=run.status,
        asking=run.asking,
        waiting_for=data.get("waiting_for") if run.status == RunStatus.WAITING else None,
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
                asked=states[f.key].asked,
                hidden=states[f.key].hidden,
                closed=states[f.key].closed,
                options=_options(defn, f, language),
                added_at=added.get(f.key),
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
        defn = await load_run_definition(session, run)
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
    # the client's language as the caller knows it (the language of its interface). The bot
    # speaks the language the tenant's rule «client's language → reply language» gives for it,
    # and keeps it whatever language the client writes in. Without it the tenant's rules decide
    # by the client's messages, as in a chat.
    language: str | None = Field(default=None, max_length=32)
    # questions the caller already has answers to: closed without an answer, never asked
    skip: list[str] = Field(default_factory=list, max_length=200)
    # the options of the choice questions with `options_from: caller`, by question key;
    # required for each of them (not skipped)
    options: dict[str, list["OptionIn"]] = Field(default_factory=dict, max_length=50)
    messages: int = Field(default=0, ge=0, le=500)


class OptionIn(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    # what the client sees; the bot also reads it when it is asked about an option
    label: str = Field(min_length=1, max_length=1000)


StartIn.model_rebuild()

# options of one question given at the start
OPTIONS_MIN, OPTIONS_MAX = 1, 10


def _start_options(defn: ScenarioDef, body: StartIn) -> dict[str, list[dict[str, str]]]:
    """The caller's options, checked: only for `options_from: caller` questions, each of
    them has 1–10, keys unique."""
    caller = set(defn.caller_options)
    wrong = sorted(k for k in body.options if k not in caller)
    if wrong:
        raise AppError("run.options_not_allowed", {"fields": wrong}, status=422)
    missing = sorted(k for k in caller if k not in body.options and k not in body.skip)
    if missing:
        raise AppError("run.options_missing", {"fields": missing}, status=422)
    out: dict[str, list[dict[str, str]]] = {}
    for key, options in body.options.items():
        if not OPTIONS_MIN <= len(options) <= OPTIONS_MAX:
            raise AppError(
                "run.options_count",
                {"field": key, "min": OPTIONS_MIN, "max": OPTIONS_MAX, "given": len(options)},
                status=422,
            )
        keys = [o.key for o in options]
        if len(set(keys)) != len(keys):
            raise AppError("run.options_duplicate", {"field": key}, status=422)
        try:
            # a bad key («a b») fails as it would in the file
            out[key] = [Option.model_validate(o.model_dump()).model_dump() for o in options]
        except ValidationError as exc:
            raise AppError("run.options_invalid", {"field": key}, status=422) from exc
    return out


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


async def _reply_language(
    session: AsyncSession, client: ApiClient, client_language: str | None
) -> str | None:
    """The language the bot speaks to a client whose language the caller passed: the tenant's
    reply rule for it (ru → uk, «*» → en), as for a client who wrote in that language."""
    if client_language is None:
        return None
    tenant = await session.get(Tenant, client.tenant_id)
    channel = await session.get(Channel, client.channel_id)
    assert tenant is not None
    settings = effective_settings(tenant.settings, channel.settings if channel else None)
    code = normalize_code(client_language)
    if code is None:
        raise AppError(
            "run.language_not_supported",
            {"language": client_language, "languages": list(settings.languages)},
            status=422,
        )
    return reply_language_for(code, settings)


class ScenarioFilter(BaseModel):
    key: str
    # the yes/no question the answer has to pass
    ask: str


class ScenarioField(BaseModel):
    key: str
    # text, choice, date… (scenarios/schema.py FieldType)
    type: str
    title: str | None
    question: str
    # what counts as an answer; null when the scenario does not say
    description: str | None
    # the checks of the answer; empty — any answer is taken
    filters: list[ScenarioFilter]


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
                type=str(f.type),
                title=_text(defn, f.title, code),
                question=_text(defn, f.question, code) or "",
                description=_text(defn, f.description, code),
                filters=[
                    ScenarioFilter(key=flt.key, ask=_text(defn, flt.ask, code) or "")
                    for flt in f.filters
                ],
            )
            for f in defn.fields
        ],
    )


# --- answers collected outside a dialogue ---------------------------------------------------

# The decider reads an answer of this length whole. In a turn it sees the first
# checks.ANSWER_PREVIEW characters of what the client said; what a caller collected for a
# question is longer than a chat reply, and a check of its beginning alone would be no check.
CHECK_ANSWER_MAX = 10_000


class CheckAnswer(BaseModel):
    # key of a question of the scenario
    field: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=CHECK_ANSWER_MAX)


class CheckIn(BaseModel):
    # what to check: each answer against the filters of its question
    answers: list[CheckAnswer] = Field(min_length=1, max_length=50)
    # answers already taken, by question key — the context a filter may need, exactly as
    # a turn gives the decider the answers accepted earlier in the run
    accepted: dict[str, str] = Field(default_factory=dict)
    # the language of the questions and criteria (one of the tenant's languages); without
    # it the scenario's own
    language: str | None = Field(default=None, max_length=32)


class FailedFilter(BaseModel):
    key: str
    ask: str
    # the scenario's wording of «no» — what such an answer lacks; null when it gives none
    no: str | None


class CheckResult(BaseModel):
    field: str
    # every filter of the question said yes (also true for a question without filters)
    passed: bool
    # the filters that said no, or were not sure
    failed: list[FailedFilter]
    # the filters no decider answered: the answer is not checked, so it has not passed. (In
    # a dialogue such a filter lets the answer through — the client is not asked again
    # because of our failure; here nobody is waiting for a reply, the caller asks again.)
    unanswered: list[str]
    # how many filters the question has; 0 — the scenario checks nothing here
    filters: int


class CheckUsage(BaseModel):
    tokens_in: int
    tokens_out: int
    cost: float
    calls: list[UsageCall]


class CheckOut(BaseModel):
    # id of this check — the batch id of its rows in the decisions log
    id: uuid.UUID
    scenario: str
    version: int
    language: str
    results: list[CheckResult]
    # null when no filter was asked (the questions have none)
    usage: CheckUsage | None


def _check_usage(decisions: DecisionSet) -> CheckUsage:
    """What the batch cost: a line per decider. Tokens and cost sit on the first row of a
    decider call, the other rows carry zeros; the latency is the call's on every row."""
    by_model: dict[tuple[str, str], list[Any]] = {}
    for row in decisions.rows:
        total = by_model.setdefault((str(row.provider), row.model), [0, 0, Decimal(0), 0, None])
        total[0] += row.tokens_in or 0
        total[1] += row.tokens_out or 0
        total[2] += Decimal(str(row.cost or 0))
        total[3] = max(total[3], row.latency_ms or 0)
        total[4] = total[4] or row.error
    calls = [
        UsageCall(
            # as in a turn's usage: a model that answers the questions is a model call,
            # the decision service is a line of its own
            kind="llm" if provider == DecisionProvider.LLM else "decisions",
            role="decide",
            model=model,
            tokens_in=t_in,
            tokens_out=t_out,
            cost=float(cost),
            latency_ms=latency or None,
            error=error,
        )
        for (provider, model), (t_in, t_out, cost, latency, error) in by_model.items()
    ]
    return CheckUsage(
        tokens_in=sum(c.tokens_in for c in calls),
        tokens_out=sum(c.tokens_out for c in calls),
        cost=float(sum((v[2] for v in by_model.values()), Decimal(0))),
        calls=calls,
    )


@router.post("/scenarios/{key}/check", response_model=CheckOut)
async def check_answers(
    key: str, body: CheckIn, client: ApiClientDep, services: ServicesDep
) -> CheckOut:
    """Answers the caller collected itself (from the client's files, a form, another
    system) go through the filters of their questions — the same yes/no questions, the
    same criteria, the same decider and the same rules a turn of the dialogue uses
    (scenarios/checks.py). One batch for all answers.

    Nothing of a dialogue is touched: no contact, no conversation, no run. The decider's
    answers are written to the decisions log without a turn.

    Only text questions: a date, a phone or a choice is checked by code inside a turn
    (scenarios/typed.py), and that part is not run here."""
    async with services.sm() as session:
        scenario = await _channel_scenario(session, client, key)
        defn = parse_definition(scenario.definition)
        code = await _run_language(session, client, body.language) or defn.language

    by_key = {f.key: f for f in defn.fields}
    asked = [a.field for a in body.answers]
    unknown = sorted({k for k in [*asked, *body.accepted] if k not in by_key})
    if unknown:
        raise AppError("run.unknown_fields", {"fields": unknown}, status=422)
    twice = sorted({k for k in asked if asked.count(k) > 1})
    if twice:
        raise AppError("check.field_twice", {"fields": twice}, status=422)
    typed = sorted(k for k in asked if by_key[k].type != FieldType.TEXT)
    if typed:
        raise AppError("check.not_a_text_field", {"fields": typed}, status=422)
    values = {a.field: a.value.strip() for a in body.answers}
    empty = sorted(k for k, v in values.items() if not v)
    if empty:
        raise AppError("check.empty_answer", {"fields": empty}, status=422)

    # the answers taken earlier, as a run would hold them; the ones under check are not
    # context for themselves (checks.check_state leaves them out)
    states = fl.load_states(
        defn,
        {
            k: {"state": fl.FILLED, "value": v.strip()}
            for k, v in body.accepted.items()
            if v.strip() and k not in values
        },
    )
    items = [CheckItem(defn, by_key[k], values[k], states) for k in asked]
    if not answers_fit(items, code, answer_chars=CHECK_ANSWER_MAX):
        # the decider would read the last answers cut; fewer answers per call do fit
        raise AppError("check.too_long", {"answers": len(items)}, status=422)
    check_id = new_id()
    outcome = await ask_filters(
        services, DecideContext(client.tenant_id), items, code, answer_chars=CHECK_ANSWER_MAX
    )

    results = []
    for k in asked:
        f = by_key[k]
        failed = set(outcome.failed[(defn.key, k)])
        results.append(
            CheckResult(
                field=k,
                passed=not failed and not outcome.unanswered[(defn.key, k)],
                failed=[
                    FailedFilter(
                        key=flt.key,
                        ask=_text(defn, flt.ask, code) or "",
                        no=_text(defn, flt.no, code),
                    )
                    for flt in f.filters
                    if flt.key in failed
                ],
                unanswered=outcome.unanswered[(defn.key, k)],
                filters=len(f.filters),
            )
        )
    decisions = outcome.decisions
    if decisions is not None and decisions.rows:
        check_id = decisions.rows[0].batch_id
    return CheckOut(
        id=check_id,
        scenario=scenario.key,
        version=scenario.version,
        language=code,
        results=results,
        usage=_check_usage(decisions) if decisions is not None else None,
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
        options = _start_options(defn, body)
        language = await _reply_language(session, client, body.language)
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
        return await _start(
            session, services, conv, scenario, defn, language, body.skip, options=options
        )

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
    *,
    options: dict[str, list[dict[str, str]]] | None = None,
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
    extra: dict[str, Any] = {"options": options} if options else {}
    defn = effective_definition(defn, extra)

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
        extra=extra,
        delivery_seq=0,
        client_language=conv.client_language,
        reply_language=said_in,
        started_at=now,
        last_activity_at=now,
    )
    data: dict[str, Any] = {}
    _ask_next(run, defn, states, data, change, said_in)
    if run.asking is None and defn.completion == Completion.BY_CALLER:
        # every question skipped: it waits for the caller's questions at once
        await settle(session, run, defn, states, data, change, now)
        change.say = waiting_say(defn, said_in)
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
        state = run_out(run, await load_run_definition(session, run)) if run else None
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
    defn = await load_run_definition(session, run)
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
    elif run.status == RunStatus.WAITING and data.get("waiting_for") == WAITING_FIELDS:
        change.say = waiting_say(defn, run.reply_language or defn.language)
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
    defn = await load_run_definition(session, run)
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


# --- added questions / finish (completion: by_caller) ------------------------------------------


class AddedFilterIn(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    # the yes/no question the answer has to pass
    ask: str | dict[str, str]
    # what such an answer lacks (the bot says it when it asks again); optional
    no: str | dict[str, str] | None = None


class AddedFieldIn(BaseModel):
    # unique in the run: neither the scenario's nor an added question has it
    key: str = Field(min_length=1, max_length=64)
    # what to ask: the meaning, the bot words it (or sends it as is — `added.wording`)
    question: str | dict[str, str]
    # what counts as an answer
    description: str | dict[str, str] | None = None
    title: str | dict[str, str] | None = None
    filters: list[AddedFilterIn] = Field(default_factory=list, max_length=10)


class FieldsIn(BaseModel):
    fields: list[AddedFieldIn] = Field(min_length=1, max_length=20)
    # take the dialogue: the run in the focus is put aside (paused) and this one asks now;
    # without it a run that waited goes back paused while another one has the focus
    focus: bool = False
    messages: int = Field(default=0, ge=0, le=500)


class FieldsOut(StateOut):
    # the bot turn that asked the question in the bot's own words; null — no model call
    usage: UsageOut | None = None


def _added_def(item: AddedFieldIn) -> FieldDef:
    raw = item.model_dump(exclude_none=True)
    try:
        return FieldDef.model_validate({**raw, "type": FieldType.TEXT})
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in e.get("loc", ())), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        raise AppError(
            "run.field_invalid", {"fields": [item.key], "errors": errors}, status=422
        ) from exc


def _stored(f: FieldDef) -> dict[str, Any]:
    return f.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


@router.post("/runs/{run_id}/fields", response_model=FieldsOut)
async def add_fields(
    run_id: uuid.UUID, body: FieldsIn, client: ApiClientDep, services: ServicesDep
) -> FieldsOut:
    """New text questions for a run of a `completion: by_caller` scenario, asked after the
    scenario's own: a running run asks them in turn, a run that waits for them goes back to
    the client. The same question sent again changes nothing; the same key with another
    text is refused. The bot asks the first new question at once when the run has the
    focus: in its own words — a bot turn with the card of the dialogue, paid like any turn
    (`usage`) — or word for word (`added.wording: as_sent`)."""
    found = await _own_run(services, client, run_id)

    async def body_fn(session: AsyncSession, conv: Conversation) -> bool:
        return await _add_fields(session, services, conv, run_id, body)

    owner = f"api:{uuid.uuid4().hex}"
    ask = await under_lease(services, found.conversation_id, owner, body_fn)
    usage = None
    if ask:
        outcome = await run_turn(
            services,
            found.conversation_id,
            owner=owner,
            wait_seconds=services.settings.http_lease_wait_seconds,
            pipeline=ask_pipeline,
            bot_only=True,
        )
        if outcome is not None:
            async with services.sm() as session:
                usage = await _usage(session, outcome.turn_id)
    state = await _state(services, found.conversation_id, run_id=run_id, messages=body.messages)
    return FieldsOut(**state.model_dump(), usage=usage)


async def _add_fields(
    session: AsyncSession,
    services: Services,
    conv: Conversation,
    run_id: uuid.UUID,
    body: FieldsIn,
) -> bool:
    """True — the bot has to word the question now (a bot turn after the lease)."""
    now = services.clock.now()
    run = await lock_run(session, run_id)
    assert run is not None
    base = await load_definition(session, run.scenario_id)
    defn = effective_definition(base, run.extra)
    if defn.completion != Completion.BY_CALLER:
        raise AppError("run.fields_not_allowed", {"completion": str(defn.completion)}, status=422)
    data = dict(run.data or {})
    waiting = run.status == RunStatus.WAITING and data.get("waiting_for") == WAITING_FIELDS
    if run.status not in TALKING and not waiting:
        raise Conflict("run.not_open", {"status": run.status})

    extra = dict(run.extra or {})
    stored = list(extra.get("fields") or [])
    by_key = {str(raw["key"]): {k: v for k, v in raw.items() if k != ADDED_AT} for raw in stored}
    new: list[dict[str, Any]] = []
    for item in body.fields:
        f = _added_def(item)
        raw = _stored(f)
        if f.key in by_key:
            if by_key[f.key] != raw:
                raise Conflict("run.field_exists", {"field": f.key})
            continue  # sent again: nothing to do
        if f.key in defn.field_keys or any(r["key"] == f.key for r in new):
            raise Conflict("run.field_exists", {"field": f.key})
        new.append(raw)
    if not new:
        return False
    rules = defn.added or AddedFields()
    if rules.max_fields is not None and len(stored) + len(new) > rules.max_fields:
        raise AppError(
            "run.too_many_fields", {"max": rules.max_fields, "have": len(stored)}, status=422
        )
    extra["fields"] = [*stored, *({**raw, ADDED_AT: now.isoformat()} for raw in new)]
    try:
        # checked from the scenario itself: the fields added before once, and without the
        # caller's options — a field `options_from: caller` holding options is refused as written
        check_definition(effective_definition(base, {"fields": extra["fields"]}), [])
        defn = effective_definition(base, extra)
    except ValidationError as exc:
        keys = [r["key"] for r in new]
        raise AppError("run.field_invalid", {"fields": keys}, status=422) from exc
    run.extra = extra
    states = fl.load_states(defn, run.fields)
    change = Change()
    for raw in new:
        change.add(FIELDS_ADDED, raw["key"])

    took = body.focus and run.status != RunStatus.FOCUS
    if took:
        await _pause_focus(session, services, conv, run, now)
    if waiting or took:
        for key in ("waiting_for", "waiting_since"):
            data.pop(key, None)
        run.status = RunStatus.FOCUS if body.focus else await back_status(session, run)
    ask = False
    # a run with a question on the screen asks the new ones in turn
    if run.status == RunStatus.FOCUS and (waiting or took or run.asking is None):
        _ask_next(run, defn, states, data, change, run.reply_language or defn.language)
        if rules.wording == "own_words":
            change.say = None
            ask = run.asking is not None
    await write_run(session, services, conv, run, defn, states, data, change, now, meta=_META)
    return ask


async def _pause_focus(
    session: AsyncSession,
    services: Services,
    conv: Conversation,
    run: ScenarioRun,
    now: datetime,
) -> None:
    """`focus: true`: the run talking now is put aside, as by the client's «stop»; it gets
    the dialogue back when this one has asked everything (scenarios/runtime.py pass_focus)."""
    other = (
        await session.execute(
            select(ScenarioRun)
            .where(
                ScenarioRun.conversation_id == run.conversation_id,
                ScenarioRun.status == RunStatus.FOCUS,
                ScenarioRun.id != run.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if other is None:
        return
    other_defn = await load_run_definition(session, other)
    change = Change()
    change.add(PAUSED)
    other.status = RunStatus.PAUSED
    states = fl.load_states(other_defn, other.fields)
    data = dict(other.data or {})
    await write_run(
        session, services, conv, other, other_defn, states, data, change, now, meta=_META
    )


class FinishIn(BaseModel):
    messages: int = Field(default=0, ge=0, le=500)


@router.post("/runs/{run_id}/finish", response_model=StateOut)
async def finish_run(
    run_id: uuid.UUID, body: FinishIn, client: ApiClientDep, services: ServicesDep
) -> StateOut:
    """«No more questions»: a run of a `completion: by_caller` scenario completes — silently,
    the bot says nothing — and its actions on `completed` fire (the results go out). A run
    already completed → nothing changes."""
    found = await _own_run(services, client, run_id)

    async def body_fn(session: AsyncSession, conv: Conversation) -> None:
        await _finish(session, services, conv, run_id)

    await under_lease(services, found.conversation_id, f"api:{uuid.uuid4().hex}", body_fn)
    return await _state(services, found.conversation_id, run_id=run_id, messages=body.messages)


async def _finish(
    session: AsyncSession, services: Services, conv: Conversation, run_id: uuid.UUID
) -> None:
    now = services.clock.now()
    run = await lock_run(session, run_id)
    assert run is not None
    defn = await load_run_definition(session, run)
    if defn.completion != Completion.BY_CALLER:
        raise AppError("run.finish_not_allowed", {"completion": str(defn.completion)}, status=422)
    if run.status == RunStatus.COMPLETED:
        return
    if run.status not in OPEN_STATUSES:
        raise Conflict("run.not_open", {"status": run.status})
    data = dict(run.data or {})
    for key in ("waiting_for", "waiting_since"):
        data.pop(key, None)
    run.status = RunStatus.COMPLETED
    run.asking = None
    run.finished_at = now
    change = Change()
    change.add(ActionEvent.COMPLETED)
    states = fl.load_states(defn, run.fields)
    await write_run(session, services, conv, run, defn, states, data, change, now, meta=_META)
