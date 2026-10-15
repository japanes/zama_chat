"""Events of a scenario run. Those named like ActionEvent fire the scenario's actions;
the others (paused, focused) only go to the run's log for the admin."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

PAUSED = "paused"
FOCUSED = "focused"
# a receipt went to a person; the run waits for the decision
RECEIPT_REVIEW = "receipt_review"
# a person decided on a receipt / an external event came (POST /v1/events)
RECEIPT_ACCEPTED = "receipt_accepted"
RECEIPT_REJECTED = "receipt_rejected"
EXTERNAL = "external"
REOPENED = "reopened"
# a question closed without an answer (POST /v1/runs/{id}/skip)
SKIPPED = "skipped"
# an added field closed after too many failed answers, with what the client said
CLOSED = "field_closed"
# the caller added fields to the run (POST /v1/runs/{id}/fields)
FIELDS_ADDED = "fields_added"


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
