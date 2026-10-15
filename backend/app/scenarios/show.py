"""show_if: whether a field applies to this conversation (docs: scenario file, `show_if`).

A field with show_if is asked only when the run's answers say it applies. Its yes/no
questions (`show:<scenario>.<field>.<key>`) go to the decider (Jev) in the second batch of
a turn, together with the filters (scenarios/checks.py), over every answer of the run.
Like `start`: the weights of the «yes» answers are summed and compared with the threshold.

When a field is checked:
- never checked yet (`FieldState.show is None`) → once its ask_after is complete;
- `recheck: every_turn` → again after every turn that changed the run's answers;
- a complete field is not checked: it has its answer, there is nothing to hide.

The verdict goes to FieldState.hidden, the last check itself to FieldState.show (shown in
the admin). A path without the decider (start, skip, added fields — api/v1/runs.py) uses
the last verdict; a field never checked is asked.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.db.models import DecisionType
from app.decisions.engine import DecisionSet, Question
from app.scenarios import fields as fl
from app.scenarios.fields import FieldState
from app.scenarios.questions import show_key, templates, title_of
from app.scenarios.schema import FieldDef, ScenarioDef, ShowIf

if TYPE_CHECKING:
    from app.scenarios.work import WorkRun

EVERY_TURN = "every_turn"
SKIP = "skip"


@dataclass(frozen=True)
class ShowItem:
    definition: ScenarioDef
    field: FieldDef
    states: dict[str, FieldState]  # the run's fields: the card the decider reads


def show_questions(item: ShowItem, language: str) -> list[Question]:
    defn, f = item.definition, item.field
    assert f.show_if is not None
    title = title_of(defn, language)
    out = []
    for q in f.show_if.questions:
        ask = defn.text(q.ask, language)
        t = templates(ask.language)
        out.append(
            Question(
                show_key(defn.key, f.key, q.key),
                DecisionType.BOOLEAN,
                t["show"].format(title=title, ask=ask.text),
                {"true": t["yes"], "false": t["no"]},
            )
        )
    return out


def shows_due(runs: "list[WorkRun]", done: set[tuple[str, str]]) -> "list[tuple[WorkRun, str]]":
    """Fields whose show_if is checked now; `done` — checked already in this turn."""
    out = []
    for run in runs:
        if not run.takes_data:
            continue
        changed = bool(run.values_changed)
        for f in run.definition.fields:
            show = f.show_if
            if show is None or (run.key, f.key) in done:
                continue
            fs = run.states[f.key]
            if fs.complete or not fl.ask_after_ready(f, run.states):
                continue
            if fs.show is None or (show.recheck == EVERY_TURN and changed):
                out.append((run, f.key))
    return out


def verdict(show: ShowIf, answers: dict[str, bool | None]) -> tuple[bool, bool]:
    """(hidden, unknown). «Yes» weights reach the threshold → shown; they could reach it
    with the unanswered questions → on_unknown decides (unknown); else hidden."""
    yes = sum(q.weight for q in show.questions if answers.get(q.key) is True)
    if yes >= show.needed:
        return False, False
    maybe = sum(q.weight for q in show.questions if answers.get(q.key) is None)
    if yes + maybe >= show.needed:
        return show.on_unknown == SKIP, True
    return True, False


def apply_shows(
    due: "list[tuple[WorkRun, str]]", decisions: DecisionSet | None, now: datetime
) -> dict[str, dict[str, Any]]:
    """Puts the verdicts on the fields; returns them for the trace ("scenario.field")."""
    out: dict[str, dict[str, Any]] = {}
    for run, key in due:
        show = run.definition.field(key).show_if
        assert show is not None
        answers: dict[str, bool | None] = {}
        for q in show.questions:
            # unsure or no answer at all → unknown
            value = decisions.value(show_key(run.key, key, q.key)) if decisions else None
            answers[q.key] = value if isinstance(value, bool) else None
        hidden, unknown = verdict(show, answers)
        fs = run.states[key]
        fs.hidden = hidden
        fs.show = {
            "hidden": hidden,
            "answers": answers,
            "unknown": unknown,
            "at": now.isoformat(),
        }
        out[f"{run.key}.{key}"] = fs.show
    return out
