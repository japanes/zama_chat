"""Step 3 of a turn — scenarios (docs/04 §5). Runs after route, before respond:

  0. runs whose `resume: within_hours` window ran out → abandoned (+ final JSON);
  1. stop        — pause the focus / cancel an open run;
  2. start       — sum of "yes" weights ≥ threshold (start.py); who gets the focus;
  3. extract     — values of the marked fields (one LLM call, the client's own words);
                   a run started in this turn gets all its fields extracted (early answers);
  4. apply       — partial answers merge, a correction replaces the value and marks the
                   fields depending on it for a re-check, an early answer waits for one;
  5. filters     — second Jev batch; then the re-check loop until nothing changes;
  6. completion  — all fields complete → completed (all_fields) or waiting (other modes);
  7. focus       — focus finished / cancelled → the most recent paused run takes it;
                   no focus → a paused run that got data in this turn takes it;
  8. next field  — what the bot asks; the same field asked 3 times in a row → stuck;
  9. RespondPlan — what the reply does (plan.py).

Everything above only changes WorkRuns in memory; `write()` puts them in the DB inside
the turn's write transaction (lease checked), fires actions and moves the timers.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TurnContext, WriteFn
from app.db.models import OPEN_STATUSES, Conversation, RunStatus, ScenarioRun, Turn
from app.decisions.engine import DecisionSet
from app.jobs.queue import cancel, cancel_prefix, enqueue
from app.logging import get_logger
from app.prompts.store import PromptText
from app.scenarios import fields as fl
from app.scenarios.catalog import ask_options, option_label, with_choices
from app.scenarios.checks import CheckItem, check_filters
from app.scenarios.events import FOCUSED, PAUSED
from app.scenarios.extract import Extracted, Target, extract
from app.scenarios.plan import RespondPlan, build_plan
from app.scenarios.questions import (
    CANCEL_PREFIX,
    CHANGE_FIELD,
    CLIENT_QUESTION,
    NONE,
    PAUSE_FOCUS,
    STOP,
    field_key,
    start_question_key,
)
from app.scenarios.schema import ActionEvent, Completion, FieldType
from app.scenarios.start import Candidate, StartResult, Vote, decide_start
from app.scenarios.store import ScenarioView
from app.scenarios.work import WorkRun, focus_of, open_run

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

MAX_CHECK_ROUNDS = 5
CLOSED_EVENTS = {
    RunStatus.COMPLETED: ActionEvent.COMPLETED,
    RunStatus.CANCELLED: ActionEvent.CANCELLED,
    RunStatus.ABANDONED: ActionEvent.ABANDONED,
}


# --- pure steps -------------------------------------------------------------------------


def expire_runs(view: ScenarioView) -> list[WorkRun]:
    out = []
    for run in view.expired:
        work = WorkRun.from_open(run)
        work.status = RunStatus.ABANDONED
        work.asking = None
        work.add(ActionEvent.ABANDONED)
        out.append(work)
    return out


def apply_stop(runs: list[WorkRun], value: Any) -> str | None:
    """Returns what was done: "paused:<key>" / "cancelled:<key>" / None."""
    if not isinstance(value, str) or value == NONE:
        return None
    if value == PAUSE_FOCUS:
        focus = focus_of(runs)
        if focus is None:
            return None
        focus.status = RunStatus.PAUSED
        focus.paused_now = True
        focus.asking = None
        focus.add(PAUSED)
        return f"paused:{focus.key}"
    if value.startswith(CANCEL_PREFIX):
        run = open_run(runs, value[len(CANCEL_PREFIX) :])
        if run is None:
            return None
        run.status = RunStatus.CANCELLED
        run.asking = None
        run.add(ActionEvent.CANCELLED)
        return f"cancelled:{run.key}"
    return None


def start_candidates(
    view: ScenarioView, runs: list[WorkRun], decisions: DecisionSet
) -> list[Candidate]:
    focus = focus_of(runs)
    # scenarios cancelled in this message are not started again by it
    cancelled = {r.key for r in runs if r.status == RunStatus.CANCELLED and not r.is_new}
    out = []
    for scenario in view.channel:
        if (focus is not None and scenario.key == focus.key) or scenario.key in cancelled:
            continue
        run = open_run(runs, scenario.key)
        if run is not None and run.status == RunStatus.WAITING:
            continue
        defn = scenario.definition
        votes = []
        for q in defn.start.questions:
            key = start_question_key(defn.key, q)
            votes.append(Vote(key, q.weight, decisions.value(key) is True))
        out.append(
            Candidate(
                scenario.key, scenario.position, defn.threshold, tuple(votes), run is not None
            )
        )
    return out


def apply_start(
    view: ScenarioView, runs: list[WorkRun], decisions: DecisionSet, now: datetime
) -> StartResult:
    focus = focus_of(runs)
    result = decide_start(start_candidates(view, runs, decisions), focus.key if focus else None)
    by_key = {s.key: s for s in view.channel}
    for key in result.new:
        status = RunStatus.FOCUS if key == result.focus else RunStatus.PAUSED
        run = WorkRun.new(by_key[key], status, now)
        run.add(ActionEvent.STARTED)
        runs.append(run)
    if result.focus_changed and result.focus is not None:
        run = open_run(runs, result.focus)
        if run is not None and run.status == RunStatus.PAUSED:
            run.status = RunStatus.FOCUS
            run.add(FOCUSED)
    return result


def extract_targets(
    runs: list[WorkRun], decisions: DecisionSet, focus_before: WorkRun | None
) -> list[Target]:
    """Fields to extract: marked by `field:` questions; the corrected one; every field of
    a run started in this turn."""
    change = decisions.value(CHANGE_FIELD, NONE)
    targets = []
    for run in runs:
        if not run.takes_data:
            continue
        defn = run.definition
        for f in defn.fields:
            fs = run.states[f.key]
            correction = run is focus_before and change == f.key and fs.value is not None
            marked = decisions.value(field_key(defn.key, f.key)) is True and not fs.complete
            if correction or marked or (run.is_new and not fs.complete):
                targets.append(
                    Target(
                        run.id,
                        defn,
                        f,
                        current=fs.value if (correction or fs.state == fl.PARTIAL) else None,
                        correction=correction,
                    )
                )
    return targets


def apply_values(
    runs: list[WorkRun],
    targets: list[Target],
    extracted: Extracted,
    *,
    message_ids: list[str],
    now: datetime,
) -> list[str]:
    """Returns the fields marked for a re-check by corrections ("scenario.field")."""
    by_id = {r.id: r for r in runs}
    marked: list[str] = []
    for t in targets:
        value = extracted.values.get((t.scenario, t.field.key))
        if value is None:
            continue
        run = by_id[t.run_id]
        fs = run.states[t.field.key]
        early = not fl.ask_after_ready(t.field, run.states)
        if t.field.type == FieldType.CATALOG:
            # the model returned the item's external_id: the value is its title
            fs.ref = value
            value = dict(t.choices).get(value, value)
        fl.set_answer(
            fs,
            value,
            # a catalog item is picked whole, never merged with an earlier answer
            replace=t.correction or t.field.type == FieldType.CATALOG,
            early=early,
            message_ids=message_ids,
            now=now,
        )
        run.answered.append(t.field.key)
        if t.correction:
            marked += [
                f"{run.key}.{k}"
                for k in fl.mark_dependents(run.definition, run.states, t.field.key)
            ]
    return marked


def checks_due(runs: list[WorkRun], first: bool) -> list[tuple[WorkRun, str]]:
    """First round: every answered field + fields waiting for a re-check that are ready.
    Next rounds: re-checks that became ready."""
    due: list[tuple[WorkRun, str]] = []
    for run in runs:
        if not run.takes_data:
            continue
        keys = list(run.answered) if first else []
        keys += [k for k in fl.recheck_ready(run.definition, run.states) if k not in keys]
        due += [(run, k) for k in keys if run.states[k].value is not None]
    return due


def apply_checks(
    due: list[tuple[WorkRun, str]], failed: dict[tuple[str, str], list[str]], now: datetime
) -> None:
    for run, key in due:
        fl.set_checked(run.states[key], failed.get((run.key, key), []), now)
    # after the whole round: a field whose ask_after is complete now was checked in context
    for run, key in due:
        fs = run.states[key]
        if fs.needs_recheck and fl.ask_after_ready(run.definition.field(key), run.states):
            fs.needs_recheck = False


def finish_runs(runs: list[WorkRun]) -> None:
    """field_filled events; all fields complete → completed / waiting."""
    for run in runs:
        if not run.takes_data:
            continue
        for key, fs in run.states.items():
            was_complete, was_value = run.snapshot.get(key, (False, None))
            if fs.complete and (not was_complete or was_value != fs.value):
                run.filled.append(key)
                run.add(ActionEvent.FIELD_FILLED, key)
        if fl.all_complete(run.states):
            run.add(ActionEvent.ALL_FIELDS)
            run.asking = None
            if run.definition.completion == Completion.ALL_FIELDS:
                run.status = RunStatus.COMPLETED
                run.add(ActionEvent.COMPLETED)
            else:
                # TODO(stage 6): explicit_yes / external_event / receipt
                run.status = RunStatus.WAITING


def pass_focus(runs: list[WorkRun]) -> WorkRun | None:
    """Give the free focus to a paused run. Returns the run that got it."""
    if focus_of(runs) is not None:
        return None
    paused = [r for r in runs if r.status == RunStatus.PAUSED and not r.paused_now]
    if not paused:
        return None
    with_data = [r for r in paused if r.answered]
    # the focus finished (completed / cancelled / waiting), not put aside by the client
    lost = any(r.before == RunStatus.FOCUS and r.status != RunStatus.PAUSED for r in runs)
    pool = with_data or (paused if lost else [])
    if not pool:
        return None
    run = max(pool, key=lambda r: (r.last_activity_at, r.started_at))
    run.status = RunStatus.FOCUS
    run.add(FOCUSED)
    return run


def next_questions(runs: list[WorkRun], *, ask: bool = True) -> None:
    """The field the focus asks in this reply; re-asks are counted. `ask=False` (the
    client wants a human): nothing is asked, the counters stay."""
    for run in runs:
        if run.status != RunStatus.FOCUS:
            run.asking = None
            continue
        if not ask:
            continue
        nxt = fl.next_field(run.definition, run.states)
        if nxt is None:
            run.asking = None
            continue
        if fl.register_ask(run.states, run.asking, nxt.key):
            run.add(ActionEvent.FIELD_STUCK, nxt.key)
        run.asking = nxt.key


async def _catalog_options(
    services: "Services", ctx: TurnContext, runs: list[WorkRun]
) -> dict[str, list[str]]:
    """Options for a catalog field the focus asks now; the listed items become the
    conversation's last_presented (core/pipeline.py)."""
    out: dict[str, list[str]] = {}
    for run in runs:
        if run.status != RunStatus.FOCUS or run.asking is None:
            continue
        f = run.definition.field(run.asking)
        if f.type != FieldType.CATALOG or run.states[f.key].value:
            continue
        items = await ask_options(services, ctx, f)
        if items:
            out[f"{run.key}.{f.key}"] = [option_label(n, i) for n, i in enumerate(items, 1)]
            ctx.extra["presented"] = [i.to_presented(n) for n, i in enumerate(items, 1)]
    return out


# --- the step ---------------------------------------------------------------------------


@dataclass
class ScenarioTurn:
    runs: list[WorkRun]
    plan: RespondPlan | None
    summary: dict[str, Any] = field(default_factory=dict)
    write: WriteFn | None = None


async def run_scenarios(
    services: "Services",
    ctx: TurnContext,
    view: ScenarioView,
    decisions: DecisionSet,
    *,
    prompts: dict[str, PromptText],
    rewritten: str,
    question_language: str,
    client_language: str | None,
    reply_language: str,
    wants_human: bool,
    step: dict[str, Any],
) -> ScenarioTurn:
    now = ctx.now
    runs = [WorkRun.from_open(r) for r in view.runs]
    focus_before = focus_of(runs)
    expired = expire_runs(view)

    stopped = apply_stop(runs, decisions.value(STOP, NONE))
    started = apply_start(view, runs, decisions, now)

    targets = extract_targets(runs, decisions, focus_before)
    targets, no_choices = await with_choices(services, ctx, targets, rewritten)
    extracted = await extract(
        services,
        ctx,
        prompts.get("extract"),
        targets,
        rewritten=rewritten,
        language=reply_language,
    )
    marked = apply_values(
        runs, targets, extracted, message_ids=[str(i) for i in ctx.pending_ids], now=now
    )

    rounds = 0
    due = checks_due(runs, first=True)
    while due and rounds < MAX_CHECK_ROUNDS:
        rounds += 1
        items = [
            CheckItem(r.definition, r.definition.field(k), r.states[k].value or "", r.states)
            for r, k in due
        ]
        failed = await check_filters(services, ctx, items, question_language, trace_step=step)
        apply_checks(due, failed, now)
        due = checks_due(runs, first=False)
    if due:
        log.warning("scenario.recheck_rounds_exceeded", left=[f"{r.key}.{k}" for r, k in due])

    finish_runs(runs)
    passed = pass_focus(runs)
    next_questions(runs, ask=not wants_human)

    catalog_options = await _catalog_options(services, ctx, runs)
    all_runs = [*expired, *runs]
    plan = build_plan(
        all_runs,
        language=reply_language,
        client_question=decisions.value(CLIENT_QUESTION, False) is True,
        wants_human=wants_human,
        catalog_options=catalog_options,
    )
    summary = {
        "stop": stopped,
        "start": started.sums,
        "started": started.new,
        "focus": next((r.key for r in runs if r.status == RunStatus.FOCUS), None),
        "focus_passed_to": passed.key if passed else None,
        "extract_targets": [f"{t.scenario}.{t.field.key}" for t in targets],
        "catalog_no_choices": no_choices,
        "extracted": {f"{s}.{f}": v for (s, f), v in extracted.values.items()},
        "rejected": extracted.rejected,
        "recheck_marked": marked,
        "check_rounds": rounds,
        "runs": {
            r.key: {
                "status": r.status,
                "events": [e.name + (f":{e.field}" if e.field else "") for e in r.events],
                "asking": r.asking,
            }
            for r in all_runs
        },
        "plan": plan.summary() if plan else None,
    }
    if extracted.llm_call_id:
        step["extract_llm_call_id"] = extracted.llm_call_id
    step.update({k: summary[k] for k in ("started", "focus", "rejected", "check_rounds")})
    result = ScenarioTurn(all_runs, plan, summary)
    result.write = make_write(services, ctx, all_runs, client_language, reply_language)
    return result


# --- write --------------------------------------------------------------------------------


def _apply(row: ScenarioRun, run: WorkRun, now: datetime, turn_id: str) -> None:
    row.status = run.status
    row.fields = fl.dump_states(run.states)
    row.asking = run.asking
    if run.events:
        row.events = [*(row.events or []), *(e.log_entry(now, turn_id) for e in run.events)]
    if not run.is_open and row.finished_at is None:
        row.finished_at = now
    row.last_activity_at = now


async def _timers(
    session: AsyncSession, ctx: TurnContext, run: WorkRun, row: ScenarioRun, now: datetime
) -> None:
    if not run.is_open:
        await cancel_prefix(session, f"silence:{run.id}:")
        await cancel(session, f"expire:{run.id}")
        return
    defn = run.definition
    # silence: the focus always (the client is in it), other runs when touched now
    if run.status == RunStatus.FOCUS or run.changed:
        for index, action in enumerate(defn.actions):
            if action.on != ActionEvent.SILENCE:
                continue
            minutes = action.after_minutes or ctx.settings.silence_minutes
            await enqueue(
                session,
                "silence_check",
                {"run_id": str(run.id), "action": index},
                run_at=now + timedelta(minutes=minutes),
                tenant_id=ctx.tenant_id,
                dedupe_key=f"silence:{run.id}:{index}",
            )
    if defn.resume.mode == "within_hours" and defn.resume.hours:
        await enqueue(
            session,
            "expire_run",
            {"run_id": str(run.id)},
            run_at=row.last_activity_at + timedelta(hours=defn.resume.hours),
            tenant_id=ctx.tenant_id,
            dedupe_key=f"expire:{run.id}",
        )


def make_write(
    services: "Services",
    ctx: TurnContext,
    runs: list[WorkRun],
    client_language: str | None,
    reply_language: str | None,
) -> WriteFn:
    async def write(session: AsyncSession, conv: Conversation, turn: Turn) -> None:
        from app.actions.registry import fire

        now = services.clock.now()
        turn_id = str(turn.id)
        rows: dict[Any, ScenarioRun] = {}
        # partial unique indexes (one focus, one open run per scenario) are checked per
        # statement: first the runs that leave the focus or close, then the focus, then
        # the new runs
        existing = [r for r in runs if not r.is_new]
        groups = [
            [r for r in existing if r.status != RunStatus.FOCUS],
            [r for r in existing if r.status == RunStatus.FOCUS],
        ]
        for group in groups:
            for run in group:
                row = await session.get(ScenarioRun, run.id, with_for_update=True)
                if row is None or row.status not in OPEN_STATUSES:
                    log.warning("scenario.run_changed_meanwhile", run_id=str(run.id))
                    continue
                _apply(row, run, now, turn_id)
                rows[run.id] = row
            await session.flush()
        new_runs = sorted((r for r in runs if r.is_new), key=lambda r: r.status == "focus")
        for run in new_runs:
            row = ScenarioRun(
                id=run.id,
                tenant_id=ctx.tenant_id,
                channel_id=ctx.channel_id,
                conversation_id=conv.id,
                scenario_id=run.scenario.id,
                scenario_key=run.key,
                scenario_version=run.scenario.version,
                status=run.status,
                fields={},
                events=[],
                delivery_seq=0,
                client_language=client_language,
                reply_language=reply_language,
                started_turn_id=turn.id,
                started_at=now,
                last_activity_at=now,
            )
            _apply(row, run, now, turn_id)
            session.add(row)
            await session.flush()
            rows[run.id] = row

        # languages of the dialogue as this turn decided them (the TurnPlan languages are
        # put on the conversation only after the writes)
        for row in rows.values():
            row.client_language = client_language or row.client_language
            row.reply_language = reply_language or row.reply_language

        if ctx.sandbox:
            return  # sandbox turns change runs but never send anything outside
        for run in runs:
            row = rows.get(run.id)
            if row is None:
                continue
            if run.events:
                await fire(session, services, row, run.definition, run.events, now=now)
            await _timers(session, ctx, run, row, now)

    return write
