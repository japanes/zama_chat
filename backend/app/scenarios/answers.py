"""scenario_answers — the answers of a scenario with `background_answers`, written after
the bot replied (schema.ScenarioDef.background_answers).

A turn does not read the text fields the client's words answer: the decider's `field:`
answer puts the turn on the field's `pending` list (runtime.mark_background), the turn's
words go to the run's dialogue (scenario_runs.dialogue) and this job is queued. The bot
asks the next question at once; the job reads the words (extract), runs the filters and
puts the answers into the run while the client answers it.

- The jobs of a run go one after another: a job that finds another one running for the
  same run comes back in a few seconds.
- The reading goes without the conversation lease (a turn is not kept waiting); the
  answers are written under it, over the run as it is then: a field a turn changed
  meanwhile keeps the turn's state and stays pending (the next job reads it again).
- Every LLM call and decision is paid in a turn of its own (turns.route.kind =
  "scenario_answers", no client message, no reply): the run's usage counts it.
"""

import copy
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.registry import fire
from app.core import lease
from app.core.context import TurnContext
from app.core.tenant_settings import effective_settings
from app.core.trace import Trace
from app.core.turn import turn_totals
from app.db.models import (
    OPEN_STATUSES,
    Channel,
    Contact,
    Conversation,
    Job,
    JobStatus,
    Scenario,
    ScenarioRun,
    Tenant,
    Turn,
    TurnStatus,
)
from app.decisions.policy import load_policy
from app.errors import Conflict
from app.jobs.queue import enqueue
from app.jobs.worker import JobContext, Retry
from app.llm.service import Overrides
from app.logging import get_logger
from app.prompts.builtin import EXTRACT_KEY
from app.prompts.store import load_prompts
from app.scenarios.fields import FieldState, dump_states, load_states
from app.scenarios.runtime import (
    ANSWERS_JOB,
    Written,
    answers_key,
    check_fields,
    enqueue_answers,
    finish_runs,
    load_said,
    update_timers,
    write_pending,
)
from app.scenarios.schema import ScenarioDef
from app.scenarios.store import open_run_row
from app.scenarios.work import WorkRun

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

KIND = ANSWERS_JOB  # turns.route.kind of the job's turn
BUSY_RETRY_S = 5.0
# a turn holds the lease while its models answer: the job waits for it to write
LEASE_WAIT_S = 120.0

# what a turn may change in a field while the job reads it: then the job's answer is
# not put over the turn's
CONTENT = (
    "state",
    "value",
    "failed_filters",
    "needs_recheck",
    "skipped",
    "hidden",
    "closed",
    "norm",
    "issue",
    "ref",
)


def _content(fs: FieldState) -> tuple[Any, ...]:
    return tuple(getattr(fs, k) for k in CONTENT)


def merge_states(
    defn: ScenarioDef,
    row_fields: dict[str, Any] | None,
    start: dict[str, FieldState],
    mine: dict[str, FieldState],
    read: dict[str, list[str]],
) -> dict[str, FieldState]:
    """The run's fields as they are now (`row_fields`) with the job's work put in.
    `start` — the states the job read, `mine` — after its work, `read` — field → the
    turns whose words it read for it. A field a turn changed meanwhile keeps the turn's
    state (and its pending turns: the next job reads them again); a field whose turns a
    turn wrote meanwhile is left as it is; the turns a field got meanwhile stay pending."""
    current = load_states(defn, row_fields)
    out: dict[str, FieldState] = {}
    for key, cur in current.items():
        before, after = start.get(key), mine.get(key)
        turns = read.get(key)
        if (
            before is None
            or after is None
            or _content(cur) != _content(before)
            or (turns is not None and not set(turns) <= set(cur.pending))
            or (turns is None and _content(after) == _content(before) and after.show == before.show)
        ):
            out[key] = cur
            continue
        merged = copy.deepcopy(after)
        merged.pending = [t for t in cur.pending if t not in (turns or [])]
        merged.stuck = cur.stuck
        if not merged.complete:
            merged.reasks = cur.reasks
        if after.asked == before.asked:
            merged.asked = cur.asked
        if merged.pending:
            # newer words wait for the next job: the decider's marks on them stand
            merged.answered, merged.answered_partly = cur.answered, cur.answered_partly
        out[key] = merged
    return out


@dataclass
class _Job:
    run: WorkRun
    ctx: TurnContext
    question_language: str
    language: str


async def scenario_answers_job(ctx: JobContext) -> None:
    services: Services = ctx.services
    run_id = uuid.UUID(ctx.payload["run_id"])
    now = services.clock.now()
    if await _another_running(services, ctx, run_id, now):
        # the jobs of a run go one after another: this one comes back
        async with services.sm() as session, session.begin():
            await enqueue(
                session,
                ANSWERS_JOB,
                ctx.payload,
                run_at=now + timedelta(seconds=BUSY_RETRY_S),
                tenant_id=ctx.tenant_id,
                dedupe_key=answers_key(run_id),
                on_conflict="earliest",
            )
        return
    raw = ctx.payload.get("overrides")
    job = await _load(
        services,
        run_id,
        sandbox=bool(ctx.payload.get("sandbox")),
        overrides=Overrides(**raw) if raw else None,
        now=now,
    )
    if job is None:
        return
    run, tctx = job.run, job.ctx
    said = await load_said(services, run)
    tctx.input_text = "\n\n".join(s.client for s in said.values())
    await _open_turn(services, tctx, run)
    started = time.monotonic()
    try:
        prompts = await load_prompts(services.sm, tctx.tenant_id, (EXTRACT_KEY,))
        start = copy.deepcopy(run.states)
        with tctx.trace.step("answers", run_id=str(run.id), scenario=run.key) as step:
            written = await write_pending(
                services,
                tctx,
                [run],
                said,
                prompt=prompts.get(EXTRACT_KEY),
                language=job.language,
                now=now,
            )
            rounds, _ = await check_fields(
                services,
                tctx,
                [run],
                written.due,
                question_language=job.question_language,
                step=step,
                now=now,
                checked=set(),
            )
            step.update(
                fields=[k for _, k in written.due],
                not_found=written.not_found,
                extracted=written.extracted,
                rejected=written.rejected,
                check_rounds=rounds,
                llm_call_ids=written.llm_call_ids,
            )
        await _write(services, ctx, tctx, run, start, written, started)
    except Retry:
        await _close_turn(services, tctx, TurnStatus.FAILED, started, "conversation busy")
        raise
    except Exception as exc:
        await _close_turn(
            services, tctx, TurnStatus.FAILED, started, f"{type(exc).__name__}: {exc}"[:500]
        )
        raise


async def _another_running(
    services: "Services", ctx: JobContext, run_id: uuid.UUID, now: datetime
) -> bool:
    async with services.sm() as session:
        found = await session.scalar(
            select(Job.id)
            .where(
                Job.dedupe_key == answers_key(run_id),
                Job.status == JobStatus.RUNNING,
                Job.id != ctx.id,
                Job.locked_until > now,
            )
            .limit(1)
        )
    return found is not None


async def _load(
    services: "Services",
    run_id: uuid.UUID,
    *,
    sandbox: bool,
    overrides: Overrides | None,
    now: datetime,
) -> _Job | None:
    """The run with answers to write and a turn context for the models (no client
    message, no history: the words come from the run's dialogue)."""
    async with services.sm() as session:
        row = await session.get(ScenarioRun, run_id)
        if row is None or row.status not in OPEN_STATUSES:
            return None
        scenario = await session.get(Scenario, row.scenario_id)
        opened = open_run_row(row, scenario) if scenario is not None else None
        if opened is None:
            return None
        run = WorkRun.from_open(opened)
        if not run.takes_data or not run.pending:
            return None
        conv = await session.get(Conversation, row.conversation_id)
        tenant = await session.get(Tenant, row.tenant_id)
        channel = await session.get(Channel, row.channel_id)
        assert conv is not None and tenant is not None and channel is not None
        contact = await session.get(Contact, conv.contact_id)
        assert contact is not None
        policy = await load_policy(session, row.tenant_id)
    if overrides is not None:
        policy = policy.with_mode(overrides.decision_mode)
    settings = effective_settings(tenant.settings, channel.settings)
    # the language of the dialogue as the turns use it (core/pipeline.py)
    language = conv.reply_language or settings.primary_language
    ctx = TurnContext(
        turn_id=uuid.uuid4(),  # replaced by the job's turn (_open_turn)
        conversation_id=conv.id,
        tenant_id=row.tenant_id,
        channel_id=row.channel_id,
        channel_kind=channel.kind,
        contact_id=contact.id,
        contact_name=contact.name or contact.username,
        settings=settings,
        tenant_title=tenant.title,
        pending_ids=[],
        input_text="",
        history=[],
        client_language=conv.client_language,
        reply_language=conv.reply_language,
        state=dict(conv.state or {}),
        state_version=conv.state_version,
        handover=conv.handover,
        operator_mode=False,
        now=now,
        sandbox=sandbox,
        overrides=overrides,
        policy=policy,
        trace=Trace(),
    )
    return _Job(run=run, ctx=ctx, question_language=language, language=language)


async def _open_turn(services: "Services", ctx: TurnContext, run: WorkRun) -> None:
    async with services.sm() as session, session.begin():
        turn = Turn(
            tenant_id=ctx.tenant_id,
            conversation_id=ctx.conversation_id,
            status=TurnStatus.RUNNING,
            input_text=ctx.input_text,
            client_language=ctx.client_language,
            reply_language=ctx.reply_language,
            route={"kind": KIND, "run_id": str(run.id), "scenario": run.key},
            sandbox=ctx.sandbox,
        )
        session.add(turn)
        await session.flush()
        ctx.turn_id = turn.id


def _finish(
    turn: Turn,
    ctx: TurnContext,
    totals: tuple[int, int, Any],
    status: str,
    now: datetime,
    started: float,
    error: str | None = None,
) -> None:
    turn.status = status
    turn.trace = ctx.trace.to_dict()
    turn.tokens_in, turn.tokens_out, turn.cost = totals
    turn.duration_ms = int((time.monotonic() - started) * 1000)
    turn.error = error
    turn.finished_at = now


async def _close_turn(
    services: "Services", ctx: TurnContext, status: str, started: float, error: str
) -> None:
    """The job failed: its turn keeps what was paid and why."""
    async with services.sm() as session, session.begin():
        turn = await session.get(Turn, ctx.turn_id)
        if turn is None or turn.status != TurnStatus.RUNNING:
            return
        _finish(
            turn,
            ctx,
            await turn_totals(session, turn.id),
            status,
            services.clock.now(),
            started,
            error,
        )


async def _write(
    services: "Services",
    job: JobContext,
    ctx: TurnContext,
    run: WorkRun,
    start: dict[str, FieldState],
    written: Written,
    started: float,
) -> None:
    """The answers go into the run as it is now, under the conversation lease."""
    owner = f"job:{job.id}"
    try:
        await lease.acquire(
            services.sm,
            ctx.conversation_id,
            owner,
            services.clock,
            seconds=services.settings.lease_seconds,
            wait_seconds=LEASE_WAIT_S,
        )
    except Conflict as exc:
        raise Retry(BUSY_RETRY_S, "conversation busy") from exc
    try:
        async with services.sm() as session, session.begin():
            conv = await lease.hold(session, ctx.conversation_id, owner)
            now = services.clock.now()
            row = await session.get(
                ScenarioRun, run.id, with_for_update=True, populate_existing=True
            )
            if row is not None and row.status in OPEN_STATUSES:
                await _merge(session, services, ctx, row, run, start, written, now)
            turn = await session.get(Turn, ctx.turn_id)
            if turn is not None:
                _finish(
                    turn, ctx, await turn_totals(session, turn.id), TurnStatus.DONE, now, started
                )
            lease.release_in(conv)
    except BaseException:
        await lease.release(services.sm, ctx.conversation_id, owner)
        raise


async def _merge(
    session: AsyncSession,
    services: "Services",
    ctx: TurnContext,
    row: ScenarioRun,
    run: WorkRun,
    start: dict[str, FieldState],
    written: Written,
    now: datetime,
) -> None:
    scenario = await session.get(Scenario, row.scenario_id)
    opened = open_run_row(row, scenario) if scenario is not None else None
    if opened is None:
        return
    final = WorkRun.from_open(opened)
    read = {k: turns for (rid, k), turns in written.read.items() if rid == run.id}
    final.states = merge_states(final.definition, row.fields, start, run.states, read)
    if final.takes_data:
        # a run whose last answers came in: completed (or waiting) as in a turn
        finish_runs([final], now)
    turn_id = str(ctx.turn_id)
    row.status = final.status
    row.fields = dump_states(final.states)
    if not final.is_open:
        row.asking = None
        row.finished_at = now
    row.data = dict(final.data)
    if final.events:
        row.events = [*(row.events or []), *(e.log_entry(now, turn_id) for e in final.events)]
    # last_activity_at stays: the client did nothing (silence and expiry count from it)
    if final.pending and final.is_open:
        await enqueue_answers(session, ctx, run.id, now)
    log.info(
        "scenario.answers_written",
        run_id=str(run.id),
        fields=[k for _, k in written.due],
        not_found=written.not_found,
        pending=final.pending,
        status=final.status,
    )
    if ctx.sandbox:
        return  # sandbox runs never send anything outside
    if final.events:
        await fire(session, services, row, final.definition, final.events, now=now)
    if final.status != final.before:
        await update_timers(session, ctx, final, row, now)
