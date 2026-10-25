"""Field states inside `scenario_runs.fields` and the rules on them (docs/04 §5).

  empty    — no answer yet;
  partial  — an answer, but some filters failed (kept with the failed filter keys); a
             typed field whose value code rejected has the failed check "@<issue>"
             (scenarios/typed.py) and is not sent to Jev;
  filled   — every filter passed;
  review   — a receipt waits for an operator (the run is waiting meanwhile).

`needs_recheck`: the answer came before its turn (ask_after not filled yet) or a field it
depends on was corrected. It is checked again, with context, once ask_after is filled.
A field is complete only when filled and not waiting for a recheck.

A field is done (not asked any more, the run can go on without it) when it is complete,
`hidden` (its show_if said it does not apply, scenarios/show.py) or `closed` (an added field
that failed too many times, closed with what the client said). A hidden field still takes
answers: what the client says about it is kept.

A scenario with `background_answers`: the turn does not read the answers of its text fields
itself. The decider (Jev) only says whether the client's words answer a question — in full
(`answered`) or in part (`answered_partly`) — and the turn is put on the field's `pending`
list; the bot goes on with the other questions, and the job `scenario_answers` writes the
answer (scenarios/answers.py). A pending field is not asked: its answer is on the way.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.scenarios.schema import FieldDef, FieldType, ScenarioDef

EMPTY = "empty"
PARTIAL = "partial"
FILLED = "filled"
REVIEW = "review"

# the same field asked this many times in a row → stuck (seen in the admin, event
# field_stuck); the bot keeps asking — there is no attempt limit
STUCK_AFTER = 3
MERGE_SEPARATOR = "\n"
# background_answers: the decider said the client answered only part of the question, the
# filters found nothing missing — the bot still asks for the rest (the issue text «partly»,
# scenarios/typed.py)
PARTLY = "@partly"


@dataclass
class FieldState:
    state: str = EMPTY
    value: str | None = None
    failed_filters: list[str] = field(default_factory=list)
    needs_recheck: bool = False
    reasks: int = 0  # re-asks in a row of this field
    stuck: bool = False
    message_ids: list[str] = field(default_factory=list)
    updated_at: str | None = None
    # type catalog: external_id of the chosen item (value = its title)
    ref: str | None = None
    # typed fields (stage 6): the machine value — ISO date, E.164 phone, list items…
    norm: Any = None
    # typed fields: why code rejected the value (re-asked without Jev)
    issue: str | None = None
    issue_params: dict[str, Any] = field(default_factory=dict)
    # closed without an answer (POST /v1/runs/{id}/skip): not asked again, no value, in the
    # result it stays among the missing fields
    skipped: bool = False
    # how the bot asked it: the bot's message of the turn before the answer
    asked: str | None = None
    # show_if said the field does not apply: not asked, answers are still taken
    hidden: bool = False
    # the last show_if check: {hidden, answers: {key: true / false / null}, unknown, at};
    # None — not checked yet
    show: dict[str, Any] | None = None
    # an added field closed after too many failed answers, with what the client said
    closed: bool = False
    # background_answers: the decider's last word on the client's message — it answers
    # the question (or adds to the answer it has) / answers it only in part
    answered: bool = False
    answered_partly: bool = False
    # background_answers: turns whose client words answer the field and are not written
    # into it yet (scenarios/answers.py)
    pending: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.state == FILLED and not self.needs_recheck

    @property
    def done(self) -> bool:
        """Not asked any more: complete, does not apply, or closed."""
        return self.complete or self.hidden or self.closed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FieldState":
        data = data or {}
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def load_states(defn: ScenarioDef, raw: dict[str, Any] | None) -> dict[str, FieldState]:
    """States for every field of the definition (missing → empty), in definition order."""
    raw = raw or {}
    return {f.key: FieldState.from_dict(raw.get(f.key)) for f in defn.fields}


def dump_states(states: dict[str, FieldState]) -> dict[str, Any]:
    return {key: fs.to_dict() for key, fs in states.items()}


def background_field(defn: ScenarioDef, f: FieldDef) -> bool:
    """The field's answers are written in the background: a text field of a scenario with
    `background_answers` (typed, choice, catalog fields and files are read in the turn)."""
    return bool(defn.background_answers) and f.type == FieldType.TEXT


def ask_after_ready(f: FieldDef, states: dict[str, FieldState]) -> bool:
    return all(states[ref].complete for ref in f.ask_after)


def next_field(
    defn: ScenarioDef,
    states: dict[str, FieldState],
    *,
    confirmed: bool = True,
    untouched_first: bool = False,
) -> FieldDef | None:
    """The first field, in definition order, that is not complete and whose ask_after
    fields are all complete. Partial fields and fields waiting for a recheck count as
    not complete. A receipt in review is not asked; with `confirm` a receipt is asked only
    after the client confirmed the rest (`confirmed`). A field whose answer is being written
    in the background (`pending`) is not asked. `untouched_first` (the client asked for
    another question): a field with nothing of its answer yet comes before one that has
    part of it."""
    askable = []
    for f in defn.fields:
        fs = states[f.key]
        if fs.done or fs.pending or fs.state == REVIEW or not ask_after_ready(f, states):
            continue
        if f.type == FieldType.RECEIPT and not confirmed:
            continue
        if not untouched_first or fs.value is None:
            return f
        askable.append(f)
    return askable[0] if askable else None


def complete_except_receipts(defn: ScenarioDef, states: dict[str, FieldState]) -> bool:
    """Everything the client confirms is collected (a receipt comes after the «yes»)."""
    return all(states[f.key].done for f in defn.fields if f.type != FieldType.RECEIPT)


def all_complete(states: dict[str, FieldState]) -> bool:
    """Nothing is left to ask: every field is done."""
    return all(fs.done for fs in states.values())


def dependents(defn: ScenarioDef, key: str) -> list[str]:
    """Fields that depend on `key` through ask_after, directly or not, in definition
    order."""
    found: set[str] = set()
    frontier = {key}
    while frontier:
        nxt = {f.key for f in defn.fields if set(f.ask_after) & frontier and f.key not in found}
        found |= nxt
        frontier = nxt
    found.discard(key)
    return [f.key for f in defn.fields if f.key in found]


def merge_value(old: str | None, new: str) -> str:
    """A partial answer + the new one: the new words are added to the old ones."""
    new = new.strip()
    if not old:
        return new
    if new.lower() in old.lower():
        return old
    if old.lower() in new.lower():
        return new
    return f"{old}{MERGE_SEPARATOR}{new}"


def set_answer(
    fs: FieldState,
    value: str,
    *,
    replace: bool,
    early: bool,
    message_ids: list[str],
    now: datetime,
    norm: Any = None,
    issue: str | None = None,
    issue_params: dict[str, Any] | None = None,
    merge: bool = False,
) -> None:
    """A new answer for the field, before the filters. A partial answer is merged with
    the new words unless the client corrects it (`replace`); `merge` — a complete one too
    (the client adds to it, background_answers)."""
    keep = not replace and (fs.state == PARTIAL or merge)
    fs.value = merge_value(fs.value, value) if keep else value.strip()
    if replace:
        # the new words are the whole answer now: what waits in the background is stale
        fs.answered_partly = False
        fs.pending = []
    fs.skipped = False
    fs.norm = norm
    fs.issue = issue
    fs.issue_params = dict(issue_params or {})
    fs.needs_recheck = early
    fs.message_ids = [*fs.message_ids, *(m for m in message_ids if m not in fs.message_ids)]
    fs.updated_at = now.isoformat()


def set_checked(fs: FieldState, failed: list[str], now: datetime) -> bool:
    """The filters' verdict. Returns True when the field became complete."""
    was_complete = fs.complete
    if fs.issue:
        # code rejected the value: it stays partial whatever the filters say
        failed = [f"@{fs.issue}"]
    elif not failed and fs.answered_partly:
        # the decider heard only part of the answer: the bot asks for the rest
        failed = [PARTLY]
    if failed:
        fs.state = PARTIAL
        fs.failed_filters = list(failed)
    else:
        fs.state = FILLED
        fs.failed_filters = []
        fs.reasks = 0
        fs.closed = False
    fs.updated_at = now.isoformat()
    return fs.complete and not was_complete


def mark_dependents(defn: ScenarioDef, states: dict[str, FieldState], key: str) -> list[str]:
    """A corrected field: everything that depends on it and has an answer is checked
    again."""
    marked = []
    for dep in dependents(defn, key):
        fs = states[dep]
        if fs.value is not None and fs.state != EMPTY:
            fs.needs_recheck = True
            marked.append(dep)
    return marked


def recheck_ready(defn: ScenarioDef, states: dict[str, FieldState]) -> list[str]:
    """Fields waiting for a recheck whose ask_after is complete now."""
    return [
        f.key
        for f in defn.fields
        if states[f.key].needs_recheck
        and states[f.key].value is not None
        and ask_after_ready(f, states)
    ]


def register_ask(states: dict[str, FieldState], asking_before: str | None, key: str) -> bool:
    """The bot asks `key` in this reply. Asking the same unfinished field again counts as a
    re-ask; the STUCK_AFTER-th re-ask in a row marks it stuck. Returns True when the field
    has just become stuck."""
    fs = states[key]
    if asking_before == key:
        fs.reasks += 1
    else:
        fs.reasks = 0
    if fs.reasks >= STUCK_AFTER and not fs.stuck:
        fs.stuck = True
        return True
    return False


def reset(fs: FieldState, now: datetime) -> None:
    """The field is asked again from scratch (an external event reopened it)."""
    fs.state = EMPTY
    fs.value = None
    fs.norm = None
    fs.ref = None
    fs.issue = None
    fs.issue_params = {}
    fs.failed_filters = []
    fs.needs_recheck = False
    fs.reasks = 0
    fs.stuck = False
    fs.skipped = False
    fs.closed = False
    fs.asked = None
    fs.answered = False
    fs.answered_partly = False
    fs.pending = []
    fs.updated_at = now.isoformat()


def set_skipped(fs: FieldState, now: datetime) -> None:
    """The question is closed without an answer: whatever was collected for it is dropped."""
    reset(fs, now)
    fs.state = FILLED
    fs.skipped = True


def set_closed(fs: FieldState, now: datetime) -> None:
    """An added field failed too many times: it is not asked again, and unlike
    set_skipped what the client said for it is kept (partial, with the failed checks)."""
    fs.closed = True
    fs.updated_at = now.isoformat()


def answers(states: dict[str, FieldState], *, complete_only: bool = True) -> dict[str, str]:
    """Values of the fields; complete_only — of the complete and the closed ones."""
    return {
        key: fs.value
        for key, fs in states.items()
        if fs.value is not None and (fs.complete or fs.closed or not complete_only)
    }
