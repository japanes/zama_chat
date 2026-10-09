"""Step 3 of a turn — scenarios (docs/04 §5). Runs after route, before respond:

  0. runs whose `resume: within_hours` window ran out → abandoned (+ final JSON);
  1. stop        — pause the focus / cancel an open run;
  2. start       — sum of "yes" weights ≥ threshold (start.py); who gets the focus;
                   a scenario incompatible with an open one does not start (blocked);
  3. extract     — code first: one phone / e-mail in the message fills the only open field
                   of its type; then the values of the marked fields (one LLM call, the
                   client's own words); a run started in this turn gets all its fields
                   extracted (early answers);
  4. apply       — typed fields (date, time, phone…) are normalized by code (typed.py), a
                   value code rejects is re-asked without Jev; partial answers merge, a
                   correction replaces the value and marks the fields depending on it for a
                   re-check, an early answer waits for one;
  5. filters     — second Jev batch; then the re-check loop until nothing changes;
  6. confirm     — the client's «yes» / «no» to «is everything correct?» (confirm:<s>);
  7. files       — attachments of the message fill the attachment / receipt field of the
                   focus; a receipt is read by the vision model and checked (receipts/);
  8. completion  — all fields complete (and confirmed) → completed, or waiting for an
                   event (external_event); a receipt in review → waiting;
  9. focus       — focus finished / cancelled → the most recent paused run takes it;
                   no focus → a paused run that got data in this turn takes it;
 10. next field  — what the bot asks (or «is everything correct?»); the same field asked
                   3 times in a row → stuck;
 11. RespondPlan — what the reply does (plan.py).

Everything above only changes WorkRuns in memory; `write()` puts them in the DB inside
the turn's write transaction (lease checked), fires actions and moves the timers.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import TurnContext, WriteFn
from app.db.models import (
    OPEN_STATUSES,
    Conversation,
    ReceiptStatus,
    RunStatus,
    ScenarioRun,
    Turn,
)
from app.decisions.engine import DecisionSet
from app.jobs.queue import cancel, cancel_prefix, enqueue
from app.logging import get_logger
from app.prompts.builtin import RECEIPT_VISION_KEY
from app.prompts.store import PromptText
from app.receipts.service import ReceiptCheck, check_attachment, expected_for
from app.scenarios import fields as fl
from app.scenarios.catalog import ask_options, option_label, with_choices
from app.scenarios.checks import CheckItem, check_filters
from app.scenarios.events import FOCUSED, PAUSED, RECEIPT_REVIEW
from app.scenarios.extract import Extracted, Target, extract
from app.scenarios.plan import CatalogAsk, RespondPlan, build_plan
from app.scenarios.questions import (
    CANCEL_PREFIX,
    CHANGE_FIELD,
    CLIENT_QUESTION,
    CONFIRM,
    NO,
    NONE,
    PAUSE_FOCUS,
    STOP,
    YES,
    confirm_key,
    field_key,
    start_question_key,
)
from app.scenarios.schema import (
    ATTACHMENT_TYPES,
    ActionEvent,
    AttachmentSettings,
    Completion,
    FieldDef,
    FieldType,
    ScenarioDef,
)
from app.scenarios.start import Candidate, StartResult, Vote, decide_start
from app.scenarios.store import ScenarioView, expires_at
from app.scenarios.typed import DEFAULT_REGION, is_typed, normalize, scan_contacts
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
# attachment types of the channels → the kinds a field allows (schema.AttachmentKind)
ATTACHMENT_KINDS = {
    "image": "image",
    "photo": "image",
    "video": "video",
    "ig_reel": "video",
    "reel": "video",
    "audio": "audio",
    "file": "file",
    "document": "file",
}
# a receipt may come as a photo or as a file (a PDF statement is read as unreadable)
RECEIPT_KINDS = ("image", "file")


def incompatible(a: ScenarioDef, b: ScenarioDef) -> bool:
    """`incompatible_with` works both ways."""
    return a.key != b.key and (b.key in a.incompatible_with or a.key in b.incompatible_with)


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
    view: ScenarioView,
    runs: list[WorkRun],
    decisions: DecisionSet,
    blocked: list[str] | None = None,
) -> list[Candidate]:
    """Scenarios that may start. A scenario incompatible with an open run (after `stop`
    of this message) is left out; when it fired it is added to `blocked`."""
    focus = focus_of(runs)
    # scenarios cancelled in this message are not started again by it
    cancelled = {r.key for r in runs if r.status == RunStatus.CANCELLED and not r.is_new}
    open_defs = [r.definition for r in runs if r.is_open]
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
        cand = Candidate(
            scenario.key, scenario.position, defn.threshold, tuple(votes), run is not None
        )
        if run is None and any(incompatible(defn, other) for other in open_defs):
            if blocked is not None and cand.fired:
                blocked.append(scenario.key)
            continue
        out.append(cand)
    return out


def apply_start(
    view: ScenarioView, runs: list[WorkRun], decisions: DecisionSet, now: datetime
) -> StartResult:
    focus = focus_of(runs)
    blocked: list[str] = []
    candidates = start_candidates(view, runs, decisions, blocked)
    result = decide_start(candidates, focus.key if focus else None)
    by_key = {s.key: s for s in view.channel}
    # two incompatible scenarios fired together: the one getting the focus wins, then
    # the bigger sum, then the one higher in the channel's list
    totals = {c.key: c.total for c in candidates}
    kept: list[str] = []
    for key in sorted(
        result.new, key=lambda k: (k != result.focus, -totals.get(k, 0), by_key[k].position)
    ):
        if any(incompatible(by_key[key].definition, by_key[k].definition) for k in kept):
            blocked.append(key)
            continue
        kept.append(key)
    result.new = [k for k in result.new if k in kept]
    result.blocked = blocked
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
            if f.type in ATTACHMENT_TYPES:
                continue  # filled from the message's files, not from words
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


def prefill_contacts(
    runs: list[WorkRun],
    text: str,
    *,
    region: str = DEFAULT_REGION,
    message_ids: list[str],
    now: datetime,
) -> list[str]:
    """Code before the model: exactly one phone (or e-mail) in the message fills the
    only open phone (e-mail) field of each run that takes data. Two numbers, or two
    phone fields in a scenario (group booking), are left to the extract model.
    Returns the fields filled ("scenario.field")."""
    found = scan_contacts(text, region)
    done: list[str] = []
    for run in runs:
        if not run.takes_data:
            continue
        for ftype, values in found.items():
            if len(values) != 1:
                continue
            open_fields = [
                f
                for f in run.definition.fields
                if f.type == ftype and run.states[f.key].state in (fl.EMPTY, fl.PARTIAL)
            ]
            if len(open_fields) != 1:
                continue
            f = open_fields[0]
            fl.set_answer(
                run.states[f.key],
                values[0],
                replace=True,
                early=not fl.ask_after_ready(f, run.states),
                message_ids=message_ids,
                now=now,
                norm=values[0],
            )
            run.answered.append(f.key)
            done.append(f"{run.key}.{f.key}")
    return done


def apply_values(
    runs: list[WorkRun],
    targets: list[Target],
    extracted: Extracted,
    *,
    message_ids: list[str],
    now: datetime,
    timezone: str = "UTC",
    region: str = DEFAULT_REGION,
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
        if is_typed(t.field):
            # code reads the machine value (ISO date, E.164…); the model's reading is the
            # fallback; a value code rejects becomes an issue the bot asks about
            typed = normalize(
                t.field,
                value,
                extracted.norms.get((t.scenario, t.field.key)),
                now=now,
                timezone=timezone,
                region=region,
            )
            fl.set_answer(
                fs,
                typed.value,
                # the model sees the partial answer and gives the whole value again
                replace=True,
                early=early,
                message_ids=message_ids,
                now=now,
                norm=typed.norm,
                issue=typed.issue,
                issue_params=typed.params,
            )
        else:
            if t.field.type == FieldType.CATALOG:
                # the model returned the item's external_id: the value is its title
                fs.ref = value
                value = dict(t.choices).get(value, value)
            fl.set_answer(
                fs,
                str(value),
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


def apply_confirm(
    focus_before: WorkRun | None, decisions: DecisionSet, now: datetime
) -> str | None:
    """The client's answer to «is everything correct?». «yes» counts only when nothing
    was corrected in the same message (a correction is shown again for a new «yes»).
    Returns yes / no / None."""
    run = focus_before
    if run is None or run.asking != CONFIRM or run.status != RunStatus.FOCUS:
        return None
    answer = decisions.value(confirm_key(run.key), NONE)
    corrected = _corrected(run)
    if answer == YES and not corrected and fl.complete_except_receipts(run.definition, run.states):
        run.data["confirmed_at"] = now.isoformat()
        run.add(ActionEvent.CONFIRMED)
        return YES
    if answer == NO:
        run.confirm_rejected = not corrected
        return NO
    return None


def _corrected(run: WorkRun) -> list[str]:
    """Fields the client changed in this turn (receipts do not need a new «yes»)."""
    return [k for k in run.values_changed if run.definition.field(k).type != FieldType.RECEIPT]


# --- files: attachments and receipts ---------------------------------------------------------


def attachment_kind(item: dict[str, Any]) -> str:
    return ATTACHMENT_KINDS.get(str(item.get("type") or "").lower(), "other")


def attachment_field(run: WorkRun) -> FieldDef | None:
    """Where the client's files go: the attachment / receipt field the bot asks, else the
    first open one whose ask_after is complete. A receipt only once everything else is
    collected (its amount may depend on the answers)."""
    defn = run.definition
    order = list(defn.fields)
    if run.asking not in (None, CONFIRM):
        order.insert(0, defn.field(run.asking))
    for f in order:
        if f.type not in ATTACHMENT_TYPES:
            continue
        fs = run.states[f.key]
        if fs.complete or fs.state == fl.REVIEW or not fl.ask_after_ready(f, run.states):
            continue
        if f.type == FieldType.RECEIPT and not fl.complete_except_receipts(defn, run.states):
            continue
        return f
    return None


def _file_item(item: dict[str, Any]) -> dict[str, Any]:
    out = {"type": attachment_kind(item), "url": item.get("url")}
    if item.get("title"):
        out["title"] = item["title"]
    if item.get("message_id"):
        out["message_id"] = item["message_id"]
    return out


def _files_text(items: list[dict[str, Any]]) -> str:
    return "; ".join(str(i.get("title") or i.get("url") or "") for i in items)


def apply_files(
    run: WorkRun,
    f: FieldDef,
    attachments: list[dict[str, Any]],
    *,
    message_ids: list[str],
    now: datetime,
) -> None:
    """An attachment field: files of the allowed kinds, up to max_items. No allowed file
    → the field asks again (issue attachment_kind)."""
    settings = f.attachment or AttachmentSettings()
    fs = run.states[f.key]
    allowed = [_file_item(a) for a in attachments if attachment_kind(a) in settings.kinds]
    items = allowed[: settings.max_items]
    fl.set_answer(
        fs,
        _files_text(items or [_file_item(a) for a in attachments]),
        replace=True,
        early=False,
        message_ids=message_ids,
        now=now,
        norm=items or None,
        issue=None if items else "attachment_kind",
        issue_params={} if items else {"kinds": list(settings.kinds)},
    )
    fl.set_checked(fs, [], now)
    run.answered.append(f.key)


def apply_receipt(
    run: WorkRun, check: ReceiptCheck, *, message_ids: list[str], now: datetime
) -> None:
    """The verdict on a receipt: accepted → filled; to a person → the field is in review
    and the run waits; not a receipt / unreadable → asked again."""
    fs = run.states[check.field_key]
    retry = check.retry_issue
    fl.set_answer(
        fs,
        check.value,
        replace=True,
        early=False,
        message_ids=message_ids,
        now=now,
        norm=check.norm,
        issue=retry,
    )
    run.answered.append(check.field_key)
    if retry is None and not run.confirmed:
        # TODO(review): a receipt sent while «is everything correct?» waits counts as «yes»
        run.data["confirmed_at"] = now.isoformat()
        run.add(ActionEvent.CONFIRMED)
    if retry is not None or check.status == ReceiptStatus.ACCEPTED:
        fl.set_checked(fs, [], now)  # retry: partial with the issue; else filled
        return
    fs.state = fl.REVIEW
    fs.failed_filters = []
    run.status = RunStatus.WAITING
    run.asking = None
    run.data.update(
        waiting_for="receipt_review", waiting_since=now.isoformat(), receipt_id=str(check.id)
    )
    run.add(RECEIPT_REVIEW, check.field_key)


async def check_receipts(
    services: "Services",
    ctx: TurnContext,
    runs: list[WorkRun],
    prompt: PromptText | None,
) -> list[ReceiptCheck]:
    """The first image of the message, when the focus waits for a receipt."""
    focus = focus_of(runs)
    if focus is None or not ctx.attachments:
        return []
    f = attachment_field(focus)
    if f is None or f.type != FieldType.RECEIPT:
        return []
    files = [a for a in ctx.attachments if attachment_kind(a) in RECEIPT_KINDS]
    if not files:
        return []
    async with services.sm() as session:
        expected = await expected_for(session, ctx.tenant_id, f, focus.states)
    check = ReceiptCheck(focus.id, f.key, files[0], expected)
    await check_attachment(
        services,
        tenant_id=ctx.tenant_id,
        turn_id=ctx.turn_id,
        settings=ctx.settings,
        prompt=prompt,
        check=check,
        now=ctx.now,
    )
    return [check]


def apply_attachments(
    runs: list[WorkRun],
    attachments: list[dict[str, Any]],
    receipts: list[ReceiptCheck],
    *,
    message_ids: list[str],
    now: datetime,
) -> list[str]:
    """Files of the message → the focus. Returns the fields filled ("scenario.field")."""
    focus = focus_of(runs)
    if focus is None or not attachments:
        return []
    if receipts:
        for check in receipts:
            apply_receipt(focus, check, message_ids=message_ids, now=now)
        return [f"{focus.key}.{c.field_key}" for c in receipts]
    f = attachment_field(focus)
    if f is None or f.type != FieldType.ATTACHMENT:
        return []
    apply_files(focus, f, attachments, message_ids=message_ids, now=now)
    return [f"{focus.key}.{f.key}"]


# --- completion ------------------------------------------------------------------------------


def finish_runs(runs: list[WorkRun], now: datetime | None = None) -> None:
    """field_filled events; all fields complete (and confirmed) → completed, or waiting
    for an external event. A correction after the «yes» needs a new «yes»."""
    for run in runs:
        if not run.takes_data:
            continue
        for key, fs in run.states.items():
            was_complete, was_value = run.snapshot.get(key, (False, None))
            if fs.complete and (not was_complete or was_value != fs.value):
                run.filled.append(key)
                run.add(ActionEvent.FIELD_FILLED, key)
        if run.data.get("confirmed_at") and _corrected(run):
            run.data.pop("confirmed_at", None)
        if not fl.all_complete(run.states):
            continue
        if not run.was_all_complete or run.values_changed:
            run.add(ActionEvent.ALL_FIELDS)
        if not run.confirmed:
            continue  # next_questions asks «is everything correct?»
        run.asking = None
        if run.definition.completion == Completion.EXTERNAL_EVENT:
            run.status = RunStatus.WAITING
            run.data.update(
                waiting_for="event", waiting_since=(now or run.last_activity_at).isoformat()
            )
            run.add(ActionEvent.WAITING)
        else:
            # all_fields / explicit_yes (confirmed above) / receipt (the receipt field is
            # complete only once accepted)
            run.status = RunStatus.COMPLETED
            run.add(ActionEvent.COMPLETED)


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
        nxt = fl.next_field(run.definition, run.states, confirmed=run.confirmed)
        if nxt is None:
            collected = fl.complete_except_receipts(run.definition, run.states)
            run.asking = CONFIRM if collected and not run.confirmed else None
            continue
        if fl.register_ask(run.states, run.asking, nxt.key):
            run.add(ActionEvent.FIELD_STUCK, nxt.key)
        run.asking = nxt.key


async def _catalog_options(
    services: "Services", ctx: TurnContext, runs: list[WorkRun], rewritten: str
) -> tuple[dict[str, CatalogAsk], dict[str, Any]]:
    """What to show with a catalog field the focus asks now (+ the same for the trace);
    the listed items become the conversation's last_presented (core/pipeline.py)."""
    out: dict[str, CatalogAsk] = {}
    trace: dict[str, Any] = {}
    for run in runs:
        if run.status != RunStatus.FOCUS or run.asking in (None, CONFIRM):
            continue
        f = run.definition.field(run.asking)
        if f.type != FieldType.CATALOG or run.states[f.key].value:
            continue
        shown = await ask_options(services, ctx, f, rewritten)
        key = f"{run.key}.{f.key}"
        trace[key] = shown.to_trace()
        out[key] = CatalogAsk(
            [option_label(n, i) for n, i in enumerate(shown.items, 1)],
            shown.more,
            shown.categories,
        )
        if shown.items:
            ctx.extra["presented"] = [i.to_presented(n) for n, i in enumerate(shown.items, 1)]
    return out, trace


async def _payment(
    services: "Services", ctx: TurnContext, runs: list[WorkRun]
) -> dict[str, dict[str, Any]]:
    """What to pay, for a receipt field the focus asks now."""
    out: dict[str, dict[str, Any]] = {}
    for run in runs:
        if run.status != RunStatus.FOCUS or run.asking in (None, CONFIRM):
            continue
        f = run.definition.field(run.asking)
        if f.type != FieldType.RECEIPT:
            continue
        async with services.sm() as session:
            expected = await expected_for(session, ctx.tenant_id, f, run.states)
        if expected.amount is not None:
            out[f"{run.key}.{f.key}"] = {
                "amount": str(expected.amount),
                "currency": expected.currency,
            }
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
    message_ids = [str(i) for i in ctx.pending_ids]
    runs = [WorkRun.from_open(r) for r in view.runs]
    focus_before = focus_of(runs)
    expired = expire_runs(view)

    stopped = apply_stop(runs, decisions.value(STOP, NONE))
    started = apply_start(view, runs, decisions, now)

    contacts = prefill_contacts(
        runs, ctx.input_text, region=ctx.settings.phone_region, message_ids=message_ids, now=now
    )
    targets = [
        t
        for t in extract_targets(runs, decisions, focus_before)
        if f"{t.scenario}.{t.field.key}" not in contacts
    ]
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
        runs,
        targets,
        extracted,
        message_ids=message_ids,
        now=now,
        timezone=ctx.settings.timezone,
        region=ctx.settings.phone_region,
    )

    rounds = 0
    due = checks_due(runs, first=True)
    while due and rounds < MAX_CHECK_ROUNDS:
        rounds += 1
        # a value code rejected (typed.py) is asked again without Jev
        items = [
            CheckItem(r.definition, r.definition.field(k), r.states[k].value or "", r.states)
            for r, k in due
            if not r.states[k].issue
        ]
        failed = (
            await check_filters(services, ctx, items, question_language, trace_step=step)
            if items
            else {}
        )
        apply_checks(due, failed, now)
        due = checks_due(runs, first=False)
    if due:
        log.warning("scenario.recheck_rounds_exceeded", left=[f"{r.key}.{k}" for r, k in due])

    confirm = apply_confirm(focus_before, decisions, now)
    receipts = await check_receipts(services, ctx, runs, prompts.get(RECEIPT_VISION_KEY))
    files = apply_attachments(runs, ctx.attachments, receipts, message_ids=message_ids, now=now)

    finish_runs(runs, now)
    passed = pass_focus(runs)
    next_questions(runs, ask=not wants_human)

    catalog_options, catalog_ask = await _catalog_options(services, ctx, runs, rewritten)
    payment = await _payment(services, ctx, runs) if not wants_human else {}
    all_runs = [*expired, *runs]
    plan = build_plan(
        all_runs,
        language=reply_language,
        client_question=decisions.value(CLIENT_QUESTION, False) is True,
        wants_human=wants_human,
        catalog_options=catalog_options,
        payment=payment,
    )
    summary = {
        "stop": stopped,
        "start": started.sums,
        "started": started.new,
        "blocked": started.blocked,
        "focus": next((r.key for r in runs if r.status == RunStatus.FOCUS), None),
        "focus_passed_to": passed.key if passed else None,
        "contacts": contacts,
        "extract_targets": [f"{t.scenario}.{t.field.key}" for t in targets],
        "catalog_no_choices": no_choices,
        "catalog_ask": catalog_ask,
        "extracted": {f"{s}.{f}": v for (s, f), v in extracted.values.items()},
        "rejected": extracted.rejected,
        "issues": {
            f"{r.key}.{k}": fs.issue for r in runs for k, fs in r.states.items() if fs.issue
        },
        "issue_params": {
            f"{r.key}.{k}": fs.issue_params
            for r in runs
            for k, fs in r.states.items()
            if fs.issue and fs.issue_params
        },
        "recheck_marked": marked,
        "check_rounds": rounds,
        "confirm": confirm,
        "files": files,
        "receipts": [c.trace() for c in receipts],
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
    step.update(
        {k: summary[k] for k in ("started", "blocked", "focus", "rejected", "check_rounds")}
    )
    result = ScenarioTurn(all_runs, plan, summary)
    result.write = make_write(
        services, ctx, all_runs, client_language, reply_language, receipts=receipts
    )
    return result


# --- write --------------------------------------------------------------------------------


def _apply(row: ScenarioRun, run: WorkRun, now: datetime, turn_id: str) -> None:
    row.status = run.status
    row.fields = fl.dump_states(run.states)
    row.asking = run.asking
    row.data = dict(run.data)
    if run.events:
        row.events = [*(row.events or []), *(e.log_entry(now, turn_id) for e in run.events)]
    if not run.is_open and row.finished_at is None:
        row.finished_at = now
    row.last_activity_at = now


def _untouched_waiting(run: WorkRun) -> bool:
    """A run that waits for an event and was not touched in this turn is not written:
    its activity time and timers belong to the wait."""
    return (
        not run.is_new
        and run.before == RunStatus.WAITING
        and run.status == RunStatus.WAITING
        and not run.changed
    )


async def _timers(
    session: AsyncSession, ctx: TurnContext, run: WorkRun, row: ScenarioRun, now: datetime
) -> None:
    if not run.is_open:
        await cancel_prefix(session, f"silence:{run.id}:")
        await cancel(session, f"expire:{run.id}")
        return
    defn = run.definition
    if run.status == RunStatus.WAITING:
        # collected: the client owes nothing, no partial JSON on silence
        await cancel_prefix(session, f"silence:{run.id}:")
    elif run.status == RunStatus.FOCUS or run.changed:
        # silence: the focus always (the client is in it), other runs when touched now
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
    # resume: within_hours; waiting.hours for a run waiting for an event
    at = expires_at(defn, row.last_activity_at, status=run.status, data=run.data)
    if at is None:
        await cancel(session, f"expire:{run.id}")
        return
    await enqueue(
        session,
        "expire_run",
        {"run_id": str(run.id)},
        run_at=at,
        tenant_id=ctx.tenant_id,
        dedupe_key=f"expire:{run.id}",
    )


async def _write_receipts(
    session: AsyncSession,
    services: "Services",
    ctx: TurnContext,
    conv: Conversation,
    turn: Turn,
    receipts: list[ReceiptCheck],
    runs: dict[Any, WorkRun],
    rows: dict[Any, ScenarioRun],
    now: datetime,
) -> None:
    """The receipts rows; one in review → a review_receipt task + a notification."""
    from app.actions.registry import fields_text
    from app.notify.service import conversation_values, notify_event
    from app.receipts.service import issues_text, open_review, receipt_row, review_payload
    from app.scenarios.questions import title_of

    for check in receipts:
        row = rows.get(check.run_id)
        run = runs.get(check.run_id)
        if row is None or run is None:
            continue  # the run changed meanwhile: nothing to attach the receipt to
        session.add(receipt_row(check, tenant_id=ctx.tenant_id, conversation_id=conv.id, now=now))
        await session.flush()
        if check.status != ReceiptStatus.REVIEW:
            continue
        title = title_of(run.definition, conv.reply_language or run.definition.language)
        await open_review(
            session,
            conv,
            turn_id=turn.id,
            payload=review_payload(
                check,
                scenario=run.key,
                scenario_title=title,
                fields=fields_text(row, run.definition),
            ),
        )
        if ctx.sandbox:
            continue
        values = await conversation_values(session, services, conv, text=issues_text(check.issues))
        values["scenario"] = title
        await notify_event(
            session, tenant_id=ctx.tenant_id, event="review_receipt", values=values, now=now
        )


def make_write(
    services: "Services",
    ctx: TurnContext,
    runs: list[WorkRun],
    client_language: str | None,
    reply_language: str | None,
    *,
    receipts: list[ReceiptCheck] | None = None,
) -> WriteFn:
    async def write(session: AsyncSession, conv: Conversation, turn: Turn) -> None:
        from app.actions.registry import fire

        now = services.clock.now()
        turn_id = str(turn.id)
        rows: dict[Any, ScenarioRun] = {}
        # partial unique indexes (one focus, one open run per scenario) are checked per
        # statement: first the runs that leave the focus or close, then the focus, then
        # the new runs
        existing = [r for r in runs if not r.is_new and not _untouched_waiting(r)]
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
                data={},
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

        if receipts:
            await _write_receipts(
                session,
                services,
                ctx,
                conv,
                turn,
                receipts,
                {r.id: r for r in runs},
                rows,
                now,
            )

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
