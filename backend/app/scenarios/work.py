"""WorkRun: a scenario run as a turn changes it, before it is written to the DB."""

import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db.base import new_id
from app.db.models import OPEN_STATUSES, RunStatus
from app.scenarios.events import Event
from app.scenarios.fields import FieldState, load_states
from app.scenarios.schema import ScenarioDef
from app.scenarios.store import LoadedScenario, OpenRun


@dataclass
class WorkRun:
    id: uuid.UUID
    scenario: LoadedScenario
    status: str
    before: str | None  # status when the turn started; None = created in this turn
    states: dict[str, FieldState]
    asking: str | None
    started_at: datetime
    last_activity_at: datetime
    events: list[Event] = field(default_factory=list)
    paused_now: bool = False  # put aside by `stop` in this turn
    answered: list[str] = field(default_factory=list)  # got a value in this turn
    filled: list[str] = field(default_factory=list)  # became complete in this turn
    # (complete, value) of every field when the turn started
    snapshot: dict[str, tuple[bool, str | None]] = field(default_factory=dict)
    # scenario_runs.data: confirmed_at, waiting_for, waiting_since, external, events_seen
    data: dict[str, Any] = field(default_factory=dict)
    data_before: dict[str, Any] = field(default_factory=dict)
    # every field was complete when the turn started (all_fields fires once)
    was_all_complete: bool = False
    # the client answered «no» to «is everything correct?» in this turn
    confirm_rejected: bool = False

    @classmethod
    def from_open(cls, run: OpenRun) -> "WorkRun":
        states = load_states(run.scenario.definition, run.fields)
        return cls(
            id=run.id,
            scenario=run.scenario,
            status=run.status,
            before=run.status,
            states=states,
            asking=run.asking,
            started_at=run.started_at,
            last_activity_at=run.last_activity_at,
            snapshot={k: (fs.complete, fs.value) for k, fs in states.items()},
            data=copy.deepcopy(run.data),
            data_before=copy.deepcopy(run.data),
            was_all_complete=all(fs.complete for fs in states.values()),
        )

    @classmethod
    def new(cls, scenario: LoadedScenario, status: str, now: datetime) -> "WorkRun":
        states = load_states(scenario.definition, None)
        return cls(
            id=new_id(),
            scenario=scenario,
            status=status,
            before=None,
            states=states,
            asking=None,
            started_at=now,
            last_activity_at=now,
            snapshot={k: (False, None) for k in states},
        )

    @property
    def key(self) -> str:
        return self.scenario.key

    @property
    def definition(self) -> ScenarioDef:
        return self.scenario.definition

    @property
    def is_new(self) -> bool:
        return self.before is None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def takes_data(self) -> bool:
        return self.status in (RunStatus.FOCUS, RunStatus.PAUSED)

    @property
    def closed_now(self) -> bool:
        return not self.is_open and (self.before is None or self.before in OPEN_STATUSES)

    @property
    def changed(self) -> bool:
        return (
            self.is_new
            or self.status != self.before
            or bool(self.events)
            or bool(self.answered)
            or self.data != self.data_before
            or any((fs.complete, fs.value) != self.snapshot.get(k) for k, fs in self.states.items())
        )

    @property
    def confirmed(self) -> bool:
        """The client confirmed the collected answers (or the scenario does not ask)."""
        return not self.definition.needs_confirm or bool(self.data.get("confirmed_at"))

    @property
    def values_changed(self) -> list[str]:
        """Fields whose value differs from the start of the turn."""
        return [
            k for k, fs in self.states.items() if fs.value != self.snapshot.get(k, (0, None))[1]
        ]

    def add(self, name: str, field_name: str | None = None) -> None:
        self.events.append(Event(name, field_name))

    def event_names(self) -> list[str]:
        return [e.name for e in self.events]


def focus_of(runs: list[WorkRun]) -> WorkRun | None:
    return next((r for r in runs if r.status == RunStatus.FOCUS), None)


def open_run(runs: list[WorkRun], key: str) -> WorkRun | None:
    return next((r for r in runs if r.key == key and r.is_open), None)
