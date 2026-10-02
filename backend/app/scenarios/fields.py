"""Field states inside `scenario_runs.fields` and the rules on them (docs/04 §5).

  empty    — no answer yet;
  partial  — an answer, but some filters failed (kept with the failed filter keys);
  filled   — every filter passed.

`needs_recheck`: the answer came before its turn (ask_after not filled yet) or a field it
depends on was corrected. It is checked again, with context, once ask_after is filled.
A field is complete only when filled and not waiting for a recheck.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.scenarios.schema import FieldDef, ScenarioDef

EMPTY = "empty"
PARTIAL = "partial"
FILLED = "filled"

# the same field asked this many times in a row → stuck (seen in the admin, event
# field_stuck); the bot keeps asking — there is no attempt limit
STUCK_AFTER = 3
MERGE_SEPARATOR = "\n"


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

    @property
    def complete(self) -> bool:
        return self.state == FILLED and not self.needs_recheck

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


def ask_after_ready(f: FieldDef, states: dict[str, FieldState]) -> bool:
    return all(states[ref].complete for ref in f.ask_after)


def next_field(defn: ScenarioDef, states: dict[str, FieldState]) -> FieldDef | None:
    """The first field, in definition order, that is not complete and whose ask_after
    fields are all complete. Partial fields and fields waiting for a recheck count as
    not complete."""
    for f in defn.fields:
        if not states[f.key].complete and ask_after_ready(f, states):
            return f
    return None


def all_complete(states: dict[str, FieldState]) -> bool:
    return all(fs.complete for fs in states.values())


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
) -> None:
    """A new answer for the field, before the filters. A partial answer is merged with
    the new words unless the client corrects it (`replace`)."""
    fs.value = value.strip() if replace or fs.state != PARTIAL else merge_value(fs.value, value)
    fs.needs_recheck = early
    fs.message_ids = [*fs.message_ids, *(m for m in message_ids if m not in fs.message_ids)]
    fs.updated_at = now.isoformat()


def set_checked(fs: FieldState, failed: list[str], now: datetime) -> bool:
    """The filters' verdict. Returns True when the field became complete."""
    was_complete = fs.complete
    if failed:
        fs.state = PARTIAL
        fs.failed_filters = list(failed)
    else:
        fs.state = FILLED
        fs.failed_filters = []
        fs.reasks = 0
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


def answers(states: dict[str, FieldState], *, complete_only: bool = True) -> dict[str, str]:
    return {
        key: fs.value
        for key, fs in states.items()
        if fs.value is not None and (fs.complete or not complete_only)
    }
