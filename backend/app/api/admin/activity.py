"""What the bot did: results (scenario runs) with their deliveries, conversations with
the trace of every turn, and the sandbox — a turn from the admin, answered in the
response, nothing sent outside.

Operators see all of it; resending a result and the sandbox need the admin role.
Sandbox conversations are hidden from the lists unless `sandbox=true` is asked for.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, delete, func, or_, select, update

from app.admin.access import audit
from app.api.admin.common import (
    Page,
    channel_dict,
    get_channel,
    iso,
    money,
    page_params,
    sid,
    tenant_row,
)
from app.api.admin.deps import TenantAdmin, TenantOperator
from app.api.deps import ServicesDep
from app.api.v1.messages import AttachmentIn
from app.channels.instagram.window import window_closes_at
from app.core.ingest import attachment_note, save_client_message
from app.core.turn import run_turn
from app.db.models import (
    OPEN_STATUSES,
    Channel,
    ChannelKind,
    Contact,
    Conversation,
    Decision,
    Delivery,
    Endpoint,
    LlmCall,
    Message,
    MessageRole,
    OperatorTask,
    Receipt,
    Scenario,
    ScenarioRun,
    TaskStatus,
    Turn,
)
from app.errors import AppError, NotFound
from app.llm.registry import get_model
from app.llm.service import Overrides
from app.operator.tasks import task_dict
from app.results.delivery import resend
from app.results.service import collected
from app.scenarios.schema import parse_definition
from app.services import Services

router = APIRouter(prefix="/v1/admin/tenants/{tenant_id}", tags=["admin-activity"])

PageDep = Annotated[Page, Depends(page_params)]
SANDBOX_PREFIX = "sandbox:"
MESSAGE_META_KEYS = ("task_id", "variant", "edited", "source", "user_id", "send_error_detail")


def _collected(definition: dict[str, Any] | None, fields: dict[str, Any]) -> dict[str, Any]:
    try:
        return collected(parse_definition(definition), fields)
    except AppError:
        return {"fields": {}, "partial": {}, "missing": [], "broken_definition": True}


def contact_dict(c: Contact | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "id": str(c.id),
        "external_id": c.external_id,
        "name": c.name,
        "username": c.username,
    }


def message_dict(m: Message) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "role": m.role,
        "text": m.text,
        "attachments": m.attachments or [],
        "turn_id": sid(m.turn_id),
        "send_status": m.send_status,
        "send_error": m.send_error,
        "created_at": iso(m.created_at),
        "sent_at": iso(m.sent_at),
        # operator messages: task / variant / edited / source (instagram_app)
        "meta": {k: v for k, v in (m.meta or {}).items() if k in MESSAGE_META_KEYS},
    }


def run_dict(r: ScenarioRun, definition: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "scenario_key": r.scenario_key,
        "scenario_version": r.scenario_version,
        "status": r.status,
        "asking": r.asking,
        "conversation_id": str(r.conversation_id),
        "channel_id": str(r.channel_id),
        "client_language": r.client_language,
        "reply_language": r.reply_language,
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "last_activity_at": iso(r.last_activity_at),
        "delivery_seq": r.delivery_seq,
        **_collected(definition, r.fields),
    }


def delivery_dict(d: Delivery, endpoint_name: str | None) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "run_id": str(d.run_id),
        "endpoint": endpoint_name,
        "event": d.event,
        "seq": d.seq,
        "status": d.status,
        "attempts": d.attempts,
        "last_code": d.last_code,
        "last_error": d.last_error,
        "payload": d.payload,
        "created_at": iso(d.created_at),
        "sent_at": iso(d.sent_at),
    }


# --- results ------------------------------------------------------------------------------


def _results_query(
    tenant_id: uuid.UUID,
    *,
    status: str | None,
    scenario: str | None,
    channel_id: uuid.UUID | None,
    since: datetime | None,
    until: datetime | None,
    sandbox: bool,
    q: str | None,
) -> Select:
    query = (
        select(ScenarioRun, Scenario.definition, Contact)
        .join(Scenario, Scenario.id == ScenarioRun.scenario_id)
        .join(Conversation, Conversation.id == ScenarioRun.conversation_id)
        .join(Contact, Contact.id == Conversation.contact_id)
        .where(ScenarioRun.tenant_id == tenant_id, Conversation.sandbox.is_(sandbox))
    )
    if status:
        query = query.where(ScenarioRun.status.in_(status.split(",")))
    if scenario:
        query = query.where(ScenarioRun.scenario_key == scenario)
    if channel_id:
        query = query.where(ScenarioRun.channel_id == channel_id)
    if since:
        query = query.where(ScenarioRun.started_at >= since)
    if until:
        query = query.where(ScenarioRun.started_at < until)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Contact.name.ilike(like),
                Contact.username.ilike(like),
                Contact.external_id.ilike(like),
            )
        )
    return query


@router.get("/results")
async def list_results(
    access: TenantOperator,
    services: ServicesDep,
    page: PageDep,
    status: str | None = None,
    scenario: str | None = None,
    channel_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    sandbox: bool = False,
    q: str | None = None,
) -> dict[str, Any]:
    """Results = scenario runs, newest first; `status` may list several (a,b)."""
    query = _results_query(
        access.tenant_id,
        status=status,
        scenario=scenario,
        channel_id=channel_id,
        since=since,
        until=until,
        sandbox=sandbox,
        q=q,
    )
    async with services.sm() as session:
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await session.execute(
                query.order_by(ScenarioRun.started_at.desc(), ScenarioRun.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).all()
        run_ids = [r.id for r, _, _ in rows]
        last = (
            await session.execute(
                select(Delivery.run_id, Delivery.status, Delivery.event, Delivery.created_at)
                .where(Delivery.run_id.in_(run_ids))
                .distinct(Delivery.run_id)
                .order_by(Delivery.run_id, Delivery.created_at.desc())
            )
        ).all()
    last_by_run = {
        d.run_id: {"status": d.status, "event": d.event, "created_at": iso(d.created_at)}
        for d in last
    }
    return {
        "items": [
            {
                **run_dict(run, definition),
                "contact": contact_dict(contact),
                "last_delivery": last_by_run.get(run.id),
            }
            for run, definition, contact in rows
        ],
        "total": total,
    }


@router.get("/results/{run_id}")
async def get_result(
    run_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session:
        run = await tenant_row(session, ScenarioRun, run_id, access.tenant_id, "result.not_found")
        scenario = await session.get(Scenario, run.scenario_id)
        conv = await session.get(Conversation, run.conversation_id)
        contact = await session.get(Contact, conv.contact_id) if conv else None
        deliveries = (
            await session.execute(
                select(Delivery, Endpoint.name)
                .outerjoin(Endpoint, Endpoint.id == Delivery.endpoint_id)
                .where(Delivery.run_id == run.id)
                .order_by(Delivery.created_at.desc())
            )
        ).all()
        receipts = (
            (
                await session.execute(
                    select(Receipt).where(Receipt.run_id == run.id).order_by(Receipt.created_at)
                )
            )
            .scalars()
            .all()
        )
    return {
        "result": {
            **run_dict(run, scenario.definition if scenario else None),
            "field_states": run.fields or {},
            "events": run.events or [],
            "data": _public_data(run.data or {}),
            "scenario_title": scenario.title if scenario else None,
        },
        "contact": contact_dict(contact),
        "deliveries": [delivery_dict(d, name) for d, name in deliveries],
        "receipts": [receipt_dict(r) for r in receipts],
    }


def _public_data(data: dict[str, Any]) -> dict[str, Any]:
    """What the admin shows of run.data: confirmation, waiting, the external event."""
    keys = ("confirmed_at", "waiting_for", "waiting_since", "receipt_id", "external")
    return {k: data[k] for k in keys if k in data}


def receipt_dict(r: Receipt) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "field": r.field_key,
        "url": r.url,
        "content_type": r.content_type,
        "status": r.status,
        "review_reason": r.review_reason,
        "extracted": r.extracted or {},
        "expected": r.expected or {},
        "issues": r.issues or [],
        "decided_by": sid(r.decided_by),
        "decided_at": iso(r.decided_at),
        "decision_note": r.decision_note,
        "llm_call_id": sid(r.llm_call_id),
        "created_at": iso(r.created_at),
    }


@router.post("/deliveries/{delivery_id}/resend")
async def resend_delivery(
    delivery_id: uuid.UUID, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """A new version of the same event with the current fields and a new seq."""
    async with services.sm() as session, session.begin():
        await tenant_row(session, Delivery, delivery_id, access.tenant_id, "delivery.not_found")
        new = await resend(session, delivery_id, now=services.clock.now())
        if new is None:
            # the run or the endpoint is gone
            raise AppError("delivery.cannot_resend", {"id": str(delivery_id)})
        await audit(
            session,
            access.user,
            "delivery.resend",
            tenant_id=access.tenant_id,
            target_type="delivery",
            target_id=delivery_id,
            data={"new_delivery_id": str(new.id)},
        )
        endpoint = await session.get(Endpoint, new.endpoint_id) if new.endpoint_id else None
        return {"delivery": delivery_dict(new, endpoint.name if endpoint else None)}


# --- conversations ------------------------------------------------------------------------


@router.get("/conversations")
async def list_conversations(
    access: TenantOperator,
    services: ServicesDep,
    page: PageDep,
    channel_id: uuid.UUID | None = None,
    sandbox: bool = False,
    handover: bool | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    query = (
        select(Conversation, Contact, Channel.title)
        .join(Contact, Contact.id == Conversation.contact_id)
        .join(Channel, Channel.id == Conversation.channel_id)
        .where(Conversation.tenant_id == access.tenant_id, Conversation.sandbox.is_(sandbox))
    )
    if channel_id:
        query = query.where(Conversation.channel_id == channel_id)
    if handover is not None:
        query = query.where(Conversation.handover.is_(handover))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Contact.name.ilike(like),
                Contact.username.ilike(like),
                Contact.external_id.ilike(like),
            )
        )
    async with services.sm() as session:
        total = (
            await session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            await session.execute(
                query.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        ).all()
        ids = [c.id for c, _, _ in rows]
        last = (
            await session.execute(
                select(Message)
                .where(Message.conversation_id.in_(ids))
                .distinct(Message.conversation_id)
                .order_by(Message.conversation_id, Message.created_at.desc())
            )
        ).scalars()
        last_by_conv = {m.conversation_id: m for m in last}
        open_runs = dict(
            (
                await session.execute(
                    select(ScenarioRun.conversation_id, func.count())
                    .where(
                        ScenarioRun.conversation_id.in_(ids),
                        ScenarioRun.status.in_(OPEN_STATUSES),
                    )
                    .group_by(ScenarioRun.conversation_id)
                )
            ).all()
        )
    items = []
    for conv, contact, channel_title in rows:
        msg = last_by_conv.get(conv.id)
        items.append(
            {
                **conversation_dict(conv),
                "contact": contact_dict(contact),
                "channel_title": channel_title,
                "last_message": message_dict(msg) if msg else None,
                "open_runs": open_runs.get(conv.id, 0),
            }
        )
    return {"items": items, "total": total}


def conversation_dict(c: Conversation) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "channel_id": str(c.channel_id),
        "client_language": c.client_language,
        "reply_language": c.reply_language,
        "operator_mode": c.operator_mode,
        "handover": c.handover,
        "handover_at": iso(c.handover_at),
        "last_client_at": iso(c.last_client_at),
        "last_bot_at": iso(c.last_bot_at),
        "sandbox": c.sandbox,
        "state": c.state or {},
        "created_at": iso(c.created_at),
        "updated_at": iso(c.updated_at),
    }


def turn_summary(t: Turn) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "status": t.status,
        "input_text": t.input_text,
        "rewritten": t.rewritten,
        "client_language": t.client_language,
        "reply_language": t.reply_language,
        "tokens_in": t.tokens_in,
        "tokens_out": t.tokens_out,
        "cost": money(t.cost),
        "duration_ms": t.duration_ms,
        "error": t.error,
        "sandbox": t.sandbox,
        "created_at": iso(t.created_at),
        "finished_at": iso(t.finished_at),
    }


async def conversation_detail(session, conv: Conversation, *, limit: int = 500) -> dict[str, Any]:
    contact = await session.get(Contact, conv.contact_id)
    channel = await session.get(Channel, conv.channel_id)
    messages = list(
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        ).scalars()
    )
    turns = (
        await session.execute(
            select(Turn)
            .where(Turn.conversation_id == conv.id)
            .order_by(Turn.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    runs = (
        await session.execute(
            select(ScenarioRun, Scenario.definition)
            .join(Scenario, Scenario.id == ScenarioRun.scenario_id)
            .where(ScenarioRun.conversation_id == conv.id)
            .order_by(ScenarioRun.started_at.desc())
        )
    ).all()
    tasks = (
        await session.execute(
            select(OperatorTask)
            .where(
                OperatorTask.conversation_id == conv.id,
                OperatorTask.status == TaskStatus.OPEN,
            )
            .order_by(OperatorTask.created_at)
        )
    ).scalars()
    closes = (
        window_closes_at(conv.last_client_at)
        if channel is not None and channel.kind == ChannelKind.INSTAGRAM
        else None
    )
    return {
        "conversation": {**conversation_dict(conv), "window_closes_at": iso(closes)},
        "tasks": [task_dict(t) for t in tasks],
        "contact": contact_dict(contact),
        "channel": channel_dict(channel) if channel else None,
        "messages": [message_dict(m) for m in reversed(messages)],
        "turns": [turn_summary(t) for t in turns],
        "runs": [run_dict(r, d) for r, d in runs],
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session:
        conv = await tenant_row(
            session, Conversation, conversation_id, access.tenant_id, "conversation.not_found"
        )
        detail = await conversation_detail(session, conv)
        channel = detail["channel"]
        adapter = services.channel_adapter(channel["kind"]) if channel else None
        # whether an operator may write to the client now (Instagram: the reply window)
        detail["operator_can_write"] = bool(
            adapter is not None
            and not conv.sandbox
            and adapter.can_send(conv, MessageRole.OPERATOR, services.clock.now())
        )
        return detail


async def turn_detail(session, turn: Turn) -> dict[str, Any]:
    """Everything about one turn: input, rewrite, route, trace, every LLM call and every
    decision (with provider, confidence and cost)."""
    calls = (
        await session.execute(
            select(LlmCall).where(LlmCall.turn_id == turn.id).order_by(LlmCall.created_at)
        )
    ).scalars()
    decisions = (
        await session.execute(
            select(Decision).where(Decision.turn_id == turn.id).order_by(Decision.created_at)
        )
    ).scalars()
    messages = (
        await session.execute(
            select(Message).where(Message.turn_id == turn.id).order_by(Message.created_at)
        )
    ).scalars()
    return {
        "turn": {
            **turn_summary(turn),
            "conversation_id": str(turn.conversation_id),
            "route": turn.route or {},
            "trace": turn.trace or {},
        },
        "messages": [message_dict(m) for m in messages],
        "llm_calls": [
            {
                "id": str(c.id),
                "role": c.role,
                "model": c.model,
                "prompt_key": c.prompt_key,
                "prompt_version": c.prompt_version,
                "request": c.request,
                "response": c.response,
                "tokens_in": c.tokens_in,
                "tokens_cached": c.tokens_cached,
                "tokens_out": c.tokens_out,
                "cost": money(c.cost),
                "latency_ms": c.latency_ms,
                "generation_id": c.generation_id,
                "attempt": c.attempt,
                "error": c.error,
                "created_at": iso(c.created_at),
            }
            for c in calls
        ],
        "decisions": [
            {
                "id": str(d.id),
                "batch_id": str(d.batch_id),
                "key": d.key,
                "type": d.type,
                "question": d.question,
                "answer": d.answer,
                "confidence": d.confidence,
                "provider": d.provider,
                "model": d.model,
                "reason": d.reason,
                "used": d.used,
                "latency_ms": d.latency_ms,
                "tokens_in": d.tokens_in,
                "cost": money(d.cost),
                "generation_id": d.generation_id,
                "error": d.error,
            }
            for d in decisions
        ],
    }


@router.get("/turns/{turn_id}")
async def get_turn(
    turn_id: uuid.UUID, access: TenantOperator, services: ServicesDep
) -> dict[str, Any]:
    async with services.sm() as session:
        turn = await tenant_row(session, Turn, turn_id, access.tenant_id, "turn.not_found")
        return await turn_detail(session, turn)


# --- sandbox ------------------------------------------------------------------------------

SESSION_NAME = r"^[A-Za-z0-9_-]{1,64}$"


def sandbox_external_id(user_id: uuid.UUID, session_name: str) -> str:
    # per admin user and session name: two admins never share a sandbox conversation
    return f"{SANDBOX_PREFIX}{user_id}:{session_name}"


async def _sandbox_channel(session, tenant_id: uuid.UUID, channel_id: uuid.UUID | None) -> Channel:
    """The given channel (its scenarios and settings are used) or the tenant's first."""
    if channel_id is not None:
        return await get_channel(session, channel_id, tenant_id)
    channel = (
        (
            await session.execute(
                select(Channel).where(Channel.tenant_id == tenant_id).order_by(Channel.created_at)
            )
        )
        .scalars()
        .first()
    )
    if channel is None:
        raise NotFound("channel.not_found", {"tenant_id": str(tenant_id)})
    return channel


async def _check_overrides(services: Services, body: "SandboxTurnIn") -> Overrides | None:
    """The chosen model serves every LLM role of this turn (rewrite, extract, decide,
    reply), so it must be a language model with JSON output."""
    if not body.model and body.reasoning_effort is None:
        return None
    if body.model:
        async with services.sm() as session:
            model = await get_model(session, body.model)
        if not model.is_active:
            raise AppError("models.inactive", {"model": model.name})
        if model.kind != "language" or not model.supports_json_schema:
            raise AppError("sandbox.model_unsuitable", {"model": model.name})
        effort = body.reasoning_effort
        if effort:
            if not model.supports_reasoning:
                raise AppError("models.reasoning_unsupported", {"model": model.name})
            if model.reasoning_efforts and effort not in model.reasoning_efforts:
                raise AppError(
                    "models.reasoning_effort_invalid",
                    {"model": model.name, "effort": effort, "allowed": model.reasoning_efforts},
                )
    return Overrides(model=body.model or None, reasoning_effort=body.reasoning_effort)


class SandboxTurnIn(BaseModel):
    channel_id: uuid.UUID | None = None
    session: str = Field(default="default", pattern=SESSION_NAME)
    text: str | None = Field(default=None, max_length=8000)
    # a link to an image / file, as a client would send it (a receipt check is real:
    # the vision model reads it)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=10)
    # only for this turn; the role settings in the DB stay as they are
    model: str | None = Field(default=None, max_length=200)
    reasoning_effort: str | None = Field(default=None, max_length=16)  # "" = off


@router.post("/sandbox/turn")
async def sandbox_turn(
    body: SandboxTurnIn, access: TenantAdmin, services: ServicesDep
) -> dict[str, Any]:
    """One turn as a client would have it, answered here. Scenario runs and fields are
    real (and visible with `sandbox=true`), but no actions, timers or sends happen."""
    overrides = await _check_overrides(services, body)
    files = [a.model_dump(mode="json", exclude_none=True) for a in body.attachments]
    text = (body.text or "").strip() or (attachment_note(files) if files else "")
    if not text:
        raise AppError("sandbox.text_required")
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        channel = await _sandbox_channel(session, access.tenant_id, body.channel_id)
        ingested = await save_client_message(
            session,
            tenant_id=access.tenant_id,
            channel_id=channel.id,
            contact_external_id=sandbox_external_id(access.user.id, body.session),
            contact_name=f"Sandbox · {access.user.name or access.user.email}",
            text=text,
            now=now,
            attachments=files,
        )
        await session.execute(
            update(Conversation)
            .where(Conversation.id == ingested.conversation_id)
            .values(sandbox=True)
        )

    error: dict[str, Any] | None = None
    outcome = None
    try:
        outcome = await run_turn(
            services,
            ingested.conversation_id,
            owner=f"sandbox:{uuid.uuid4().hex}",
            wait_seconds=services.settings.http_lease_wait_seconds,
            only_message_id=ingested.message_id,
            sandbox=True,
            overrides=overrides,
        )
    except AppError as exc:
        error = exc.to_dict()
    except Exception as exc:
        error = {"code": "turn.failed", "params": {"error": type(exc).__name__}}

    async with services.sm() as session, session.begin():
        if outcome is not None:
            turn = await session.get(Turn, outcome.turn_id)
        else:
            turn = (
                (
                    await session.execute(
                        select(Turn)
                        .where(Turn.conversation_id == ingested.conversation_id)
                        .order_by(Turn.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if error is not None:
                # a failed turn gives its message back; in the sandbox it must not be
                # answered again together with the next one
                await session.execute(
                    update(Message)
                    .where(Message.id == ingested.message_id, Message.turn_id.is_(None))
                    .values(turn_id=turn.id if turn else None, processed_at=now)
                )
        conv = await session.get(Conversation, ingested.conversation_id)
        assert conv is not None
        return {
            "conversation_id": str(ingested.conversation_id),
            "reply": outcome.reply_text if outcome else None,
            "handover": outcome.handover if outcome else False,
            "status": outcome.status if outcome else None,
            "error": error,
            "turn": await turn_detail(session, turn) if turn else None,
            "state": await conversation_detail(session, conv, limit=200),
        }


async def _sandbox_conversation(
    session, access, channel_id: uuid.UUID | None, session_name: str
) -> Conversation | None:
    channel = await _sandbox_channel(session, access.tenant_id, channel_id)
    return (
        await session.execute(
            select(Conversation)
            .join(Contact, Contact.id == Conversation.contact_id)
            .where(
                Contact.channel_id == channel.id,
                Contact.external_id == sandbox_external_id(access.user.id, session_name),
            )
        )
    ).scalar_one_or_none()


SessionQuery = Annotated[str, Query(pattern=SESSION_NAME)]


@router.get("/sandbox")
async def sandbox_state(
    access: TenantAdmin,
    services: ServicesDep,
    channel_id: uuid.UUID | None = None,
    session: SessionQuery = "default",
) -> dict[str, Any]:
    async with services.sm() as db:
        conv = await _sandbox_conversation(db, access, channel_id, session)
        return {"state": await conversation_detail(db, conv, limit=200) if conv else None}


@router.get("/sandbox/sessions")
async def sandbox_sessions(access: TenantAdmin, services: ServicesDep) -> dict[str, Any]:
    """This user's sandbox sessions in the tenant."""
    prefix = sandbox_external_id(access.user.id, "")
    async with services.sm() as db:
        rows = (
            await db.execute(
                select(Contact, Conversation)
                .join(Conversation, Conversation.contact_id == Contact.id)
                .where(
                    Contact.tenant_id == access.tenant_id,
                    Contact.external_id.startswith(prefix, autoescape=True),
                )
                .order_by(Conversation.updated_at.desc())
            )
        ).all()
    return {
        "items": [
            {
                "session": contact.external_id[len(prefix) :],
                "channel_id": str(contact.channel_id),
                "conversation_id": str(conv.id),
                "updated_at": iso(conv.updated_at),
            }
            for contact, conv in rows
        ]
    }


@router.delete("/sandbox")
async def sandbox_reset(
    access: TenantAdmin,
    services: ServicesDep,
    channel_id: uuid.UUID | None = None,
    session: SessionQuery = "default",
) -> dict[str, Any]:
    """Start over: the sandbox contact goes with its conversation, turns and runs."""
    async with services.sm() as db, db.begin():
        conv = await _sandbox_conversation(db, access, channel_id, session)
        if conv is not None:
            await db.execute(delete(Contact).where(Contact.id == conv.contact_id))
    return {"ok": True}
