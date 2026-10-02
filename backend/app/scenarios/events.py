"""Events of a scenario run. Those named like ActionEvent fire the scenario's actions;
the others (paused, focused) only go to the run's log for the admin."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

PAUSED = "paused"
FOCUSED = "focused"


@dataclass(frozen=True)
class Event:
    name: str
    field: str | None = None

    def log_entry(self, at: datetime, turn_id: str | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {"event": self.name, "at": at.isoformat()}
        if self.field is not None:
            entry["field"] = self.field
        if turn_id is not None:
            entry["turn_id"] = turn_id
        return entry
