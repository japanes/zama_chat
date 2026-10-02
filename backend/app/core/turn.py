"""run_turn(): take → think (outside transactions) → write.

  TAKE   short transaction: lease; create the turn; link unprocessed client messages;
         load tenant / channel / contact / history.
  THINK  the pipeline (LLM / Jev calls), lease kept alive meanwhile.
  WRITE  short transaction: hold() checks the lease is still ours; the reply goes to
         `messages` (+ send_message job for asynchronous channels, outbox); turn totals;
         plan writes; state_version + 1; lease released.

New client messages that arrived while an asynchronous channel turn was thinking make
the reply stale: it is dropped and the turn runs again with everything (at most
`turn_restart_limit` times in a row). HTTP turns take only their own message.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import lease
from app.core.context import HistoryItem, Pipeline, TurnContext, TurnPlan
from app.core.tenant_settings import effective_settings
from app.core.trace import Trace
from app.db.models import (
    Channel,
    Contact,
    Conversation,
    LlmCall,
    Message,
    MessageRole,
    SendStatus,
    Tenant,
    Turn,
    TurnStatus,
)
from app.errors import AppError
from app.jobs.queue import enqueue
from app.llm.service import Overrides
from app.logging import get_logger
from app.services import Services

log = get_logger(__name__)


@dataclass
class TurnOutcome:
    turn_id: uuid.UUID
    conversation_id: uuid.UUID
    status: str
    reply_text: str | None
    reply_message_id: uuid.UUID | None
    handover: bool = False


def default_pipeline() -> Pipeline:
    # imported lazily: the full pipeline pulls in decisions / scenarios
    from app.core.pipeline import full_pipeline

    return full_pipeline


async def run_turn(
    services: Services,
    conversation_id: uuid.UUID,
    *,
    owner: str,
    wait_seconds: float = 0.0,
    only_message_id: uuid.UUID | None = None,
    sandbox: bool = False,
    overrides: Overrides | None = None,
    pipeline: Pipeline | None = None,
) -> TurnOutcome | None:
    """Run one turn. None when there was nothing to answer (no unprocessed messages)."""
    settings = services.settings
    pipeline = pipeline or services.extra.get("pipeline") or default_pipeline()
    await lease.acquire(
        services.sm,
        conversation_id,
        owner,
        services.clock,
        seconds=settings.lease_seconds,
        wait_seconds=wait_seconds,
    )
    restarts = 0
    try:
        while True:
            outcome = await _one_pass(
                services,
                conversation_id,
                owner=owner,
                only_message_id=only_message_id,
                sandbox=sandbox,
                overrides=overrides,
                pipeline=pipeline,
                allow_restart=only_message_id is None and restarts < settings.turn_restart_limit,
            )
            if outcome is None or outcome.status != TurnStatus.SUPERSEDED:
                return outcome
            restarts += 1
            log.info("turn.restart", conversation_id=str(conversation_id), restarts=restarts)
    finally:
        # normal path released the lease in the write transaction; this is a no-op then
        await lease.release(services.sm, conversation_id, owner)


async def _one_pass(
    services: Services,
    conversation_id: uuid.UUID,
    *,
    owner: str,
    only_message_id: uuid.UUID | None,
    sandbox: bool,
    overrides: Overrides | None,
    pipeline: Pipeline,
    allow_restart: bool,
) -> TurnOutcome | None:
    started = time.monotonic()
    ctx = await _take(
        services,
        conversation_id,
        only_message_id=only_message_id,
        sandbox=sandbox,
        overrides=overrides,
    )
    if ctx is None:
        return None
    bound = log.bind(turn_id=str(ctx.turn_id), conversation_id=str(conversation_id))
    try:
        async with lease.keep_alive(
            services.sm,
            conversation_id,
            owner,
            services.clock,
            seconds=services.settings.lease_seconds,
        ):
            plan = await pipeline(services, ctx)
        return await _write(
            services, ctx, plan, owner=owner, started=started, allow_restart=allow_restart
        )
    except Exception as exc:
        bound.exception("turn.failed")
        await _fail(services, ctx, exc, started)
        raise


async def _take(
    services: Services,
    conversation_id: uuid.UUID,
    *,
    only_message_id: uuid.UUID | None,
    sandbox: bool,
    overrides: Overrides | None,
) -> TurnContext | None:
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        conv = await session.get(Conversation, conversation_id)
        if conv is None:
            raise AppError("conversation.not_found", status=404)
        tenant = await session.get(Tenant, conv.tenant_id)
        channel = await session.get(Channel, conv.channel_id)
        contact = await session.get(Contact, conv.contact_id)
        assert tenant is not None and channel is not None and contact is not None

        turn = Turn(
            tenant_id=conv.tenant_id,
            conversation_id=conv.id,
            status=TurnStatus.RUNNING,
            client_language=conv.client_language,
            reply_language=conv.reply_language,
            sandbox=sandbox,
        )
        session.add(turn)
        await session.flush()

        cond = [
            Message.conversation_id == conv.id,
            Message.role == MessageRole.CLIENT,
            Message.turn_id.is_(None),
        ]
        if only_message_id is not None:
            cond.append(Message.id == only_message_id)
        pending = (
            await session.execute(
                update(Message)
                .where(*cond)
                .values(turn_id=turn.id)
                .returning(Message.id, Message.text, Message.created_at)
            )
        ).all()
        if not pending:
            # nothing to answer: the turn row is not worth keeping
            await session.delete(turn)
            return None
        pending = sorted(pending, key=lambda r: (r.created_at, r.id))
        input_text = "\n".join(r.text for r in pending if r.text).strip()
        turn.input_text = input_text

        history = await _load_history(session, conv.id, turn.id, services.settings.history_messages)
        return TurnContext(
            turn_id=turn.id,
            conversation_id=conv.id,
            tenant_id=conv.tenant_id,
            channel_id=conv.channel_id,
            channel_kind=channel.kind,
            contact_id=contact.id,
            contact_name=contact.name or contact.username,
            settings=effective_settings(tenant.settings, channel.settings),
            tenant_title=tenant.title,
            pending_ids=[r.id for r in pending],
            input_text=input_text,
            history=history,
            client_language=conv.client_language,
            reply_language=conv.reply_language,
            state=dict(conv.state or {}),
            state_version=conv.state_version,
            handover=conv.handover,
            operator_mode=conv.operator_mode or bool(channel.settings.get("operator_mode")),
            now=now,
            sandbox=sandbox,
            overrides=overrides,
            trace=Trace(),
        )


async def _load_history(
    session: AsyncSession, conversation_id: uuid.UUID, turn_id: uuid.UUID, limit: int
) -> list[HistoryItem]:
    rows = (
        await session.execute(
            select(Message.role, Message.text, Message.created_at)
            .where(
                Message.conversation_id == conversation_id,
                # this turn's input is passed separately; other unprocessed client
                # messages belong to other (HTTP) turns
                ~(
                    (Message.role == MessageRole.CLIENT)
                    & (Message.turn_id.is_(None) | (Message.turn_id == turn_id))
                ),
                # dropped replies were never seen by the client
                Message.send_status.is_distinct_from(SendStatus.DROPPED),
            )
            # uuid7 ids grow with time: a tie on created_at keeps the insert order
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
    ).all()
    return [HistoryItem(r.role, r.text, r.created_at) for r in reversed(rows)]


async def _write(
    services: Services,
    ctx: TurnContext,
    plan: TurnPlan,
    *,
    owner: str,
    started: float,
    allow_restart: bool,
) -> TurnOutcome:
    now = services.clock.now()
    adapter = services.channel_adapter(ctx.channel_kind)
    synchronous = ctx.sandbox or (adapter is not None and adapter.synchronous)
    async with services.sm() as session, session.begin():
        conv = await lease.hold(session, ctx.conversation_id, owner)
        turn = await session.get(Turn, ctx.turn_id)
        assert turn is not None

        if allow_restart and not synchronous and await _has_new_messages(session, conv.id):
            # the reply would answer an old question — throw it away, think again
            await _supersede(session, ctx, turn, plan, now, started)
            return TurnOutcome(turn.id, conv.id, TurnStatus.SUPERSEDED, None, None)

        reply_id: uuid.UUID | None = None
        if plan.reply_text:
            reply = Message(
                tenant_id=ctx.tenant_id,
                channel_id=ctx.channel_id,
                conversation_id=conv.id,
                role=MessageRole.BOT,
                text=plan.reply_text,
                turn_id=turn.id,
                send_status=SendStatus.SENT if synchronous else SendStatus.PENDING,
                sent_at=now if synchronous else None,
                meta=plan.reply_meta,
                created_at=now,
            )
            session.add(reply)
            await session.flush()
            reply_id = reply.id
            if not synchronous:
                await enqueue(
                    session,
                    "send_message",
                    {"message_id": str(reply.id)},
                    run_at=now,
                    tenant_id=ctx.tenant_id,
                    dedupe_key=f"send:{reply.id}",
                )
            conv.last_bot_at = now

        for write in plan.writes:
            await write(session, conv, turn)

        if plan.client_language:
            conv.client_language = plan.client_language
        if plan.reply_language:
            conv.reply_language = plan.reply_language
        if plan.handover and not conv.handover:
            conv.handover = True
            conv.handover_at = now
        if plan.state is not None:
            conv.state = plan.state
        conv.state_version += 1

        await session.execute(
            update(Message).where(Message.id.in_(ctx.pending_ids)).values(processed_at=now)
        )
        await _finish_turn(session, ctx, turn, plan, TurnStatus.DONE, now, started)
        lease.release_in(conv)

    return TurnOutcome(
        turn.id, ctx.conversation_id, TurnStatus.DONE, plan.reply_text, reply_id, plan.handover
    )


async def _has_new_messages(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    found = await session.execute(
        select(Message.id)
        .where(
            Message.conversation_id == conversation_id,
            Message.role == MessageRole.CLIENT,
            Message.turn_id.is_(None),
        )
        .limit(1)
    )
    return found.first() is not None


async def _supersede(
    session: AsyncSession,
    ctx: TurnContext,
    turn: Turn,
    plan: TurnPlan,
    now: datetime,
    started: float,
) -> None:
    if plan.reply_text:
        session.add(
            Message(
                tenant_id=ctx.tenant_id,
                channel_id=ctx.channel_id,
                conversation_id=ctx.conversation_id,
                role=MessageRole.BOT,
                text=plan.reply_text,
                turn_id=turn.id,
                send_status=SendStatus.DROPPED,
                meta={**plan.reply_meta, "dropped": "new_client_messages"},
                created_at=now,
            )
        )
    # give the messages back: the next pass takes them together with the new ones
    await session.execute(
        update(Message).where(Message.id.in_(ctx.pending_ids)).values(turn_id=None)
    )
    await _finish_turn(session, ctx, turn, plan, TurnStatus.SUPERSEDED, now, started)


async def _finish_turn(
    session: AsyncSession,
    ctx: TurnContext,
    turn: Turn,
    plan: TurnPlan | None,
    status: str,
    now: datetime,
    started: float,
    error: str | None = None,
) -> None:
    tokens_in, tokens_out, cost = await _turn_totals(session, turn.id)
    turn.status = status
    turn.input_text = ctx.input_text
    if plan is not None:
        turn.rewritten = plan.rewritten
        turn.client_language = plan.client_language or ctx.client_language
        turn.reply_language = plan.reply_language or ctx.reply_language
        turn.route = plan.route
    turn.trace = ctx.trace.to_dict()
    turn.tokens_in = tokens_in
    turn.tokens_out = tokens_out
    turn.cost = cost
    turn.duration_ms = int((time.monotonic() - started) * 1000)
    turn.error = error
    turn.finished_at = now


async def _turn_totals(session: AsyncSession, turn_id: uuid.UUID) -> tuple[int, int, Decimal]:
    """Sum of everything paid for in this turn: LLM calls (+ Jev decisions, stage 1)."""
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(LlmCall.tokens_in), 0),
                func.coalesce(func.sum(LlmCall.tokens_out), 0),
                func.coalesce(func.sum(LlmCall.cost), 0),
            ).where(LlmCall.turn_id == turn_id)
        )
    ).one()
    tokens_in, tokens_out, cost = int(row[0]), int(row[1]), Decimal(row[2])
    for extra in TURN_COST_SOURCES:
        t_in, t_out, c = await extra(session, turn_id)
        tokens_in, tokens_out, cost = tokens_in + t_in, tokens_out + t_out, cost + c
    return tokens_in, tokens_out, cost


# other tables with per-turn costs register here (decisions — stage 1)
CostSource = Callable[[AsyncSession, uuid.UUID], Awaitable[tuple[int, int, Decimal]]]
TURN_COST_SOURCES: list[CostSource] = []


async def _fail(services: Services, ctx: TurnContext, exc: Exception, started: float) -> None:
    """Mark the turn failed and give its messages back, so a retry (job retry, the same
    client_message_id again) picks them up."""
    error = exc.to_dict() if isinstance(exc, AppError) else {"code": type(exc).__name__}
    message = f"{error}: {exc}"[:2000]
    try:
        async with services.sm() as session, session.begin():
            turn = await session.get(Turn, ctx.turn_id)
            if turn is None:
                return
            await session.execute(
                update(Message)
                .where(Message.id.in_(ctx.pending_ids), Message.turn_id == ctx.turn_id)
                .values(turn_id=None)
            )
            await _finish_turn(
                session,
                ctx,
                turn,
                None,
                TurnStatus.FAILED,
                services.clock.now(),
                started,
                error=message,
            )
    except Exception:
        log.exception("turn.fail_record_failed", turn_id=str(ctx.turn_id))
