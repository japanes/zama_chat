"""The result JSON (docs/03 §7.3): only the collected fields — no conversation, no
conclusions. One run = one `result_id` for all its versions; every sending has its own
`delivery_id` and a growing `seq`, so the receiver keeps the newest version.
"""

from datetime import datetime
from typing import Any

from app.db.models import Channel, Contact, RunStatus, ScenarioRun
from app.scenarios.fields import FILLED, PARTIAL, REVIEW, load_states
from app.scenarios.schema import ActionEvent, ScenarioDef

# result status by the event that sends it
EVENT_STATUS = {
    ActionEvent.COMPLETED: "completed",
    ActionEvent.CANCELLED: "cancelled",
    ActionEvent.ABANDONED: "abandoned",
    ActionEvent.SILENCE: "incomplete",
    ActionEvent.WAITING: "waiting",
    ActionEvent.CONFIRMED: "confirmed",
}
RUN_STATUS = {
    RunStatus.FOCUS: "in_progress",
    RunStatus.PAUSED: "in_progress",
    RunStatus.WAITING: "waiting",
    RunStatus.COMPLETED: "completed",
    RunStatus.CANCELLED: "cancelled",
    RunStatus.ABANDONED: "abandoned",
}


def result_status(event: str, run_status: str, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    return EVENT_STATUS.get(event) or RUN_STATUS.get(run_status, run_status)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def collected(defn: ScenarioDef, raw: dict[str, Any] | None) -> dict[str, Any]:
    """fields (accepted answers, the client's words), partial (incomplete or not
    re-checked yet), missing; refs — catalog items' external_id; normalized — the machine
    values of accepted typed fields (ISO date, HH:MM, E.164 phone, e-mail, number, list
    items, files, the receipt's amount / date / reference)."""
    states = load_states(defn, raw)
    fields: dict[str, str] = {}
    partial: dict[str, str] = {}
    missing: list[str] = []
    refs: dict[str, str] = {}
    normalized: dict[str, Any] = {}
    for f in defn.fields:
        fs = states[f.key]
        if fs.ref and fs.value is not None:
            # catalog field: the item's external_id next to its title
            refs[f.key] = fs.ref
        if fs.complete and fs.value is not None:
            fields[f.key] = fs.value
            if fs.norm is not None:
                normalized[f.key] = fs.norm
            continue
        if fs.value is not None and fs.state in (PARTIAL, FILLED, REVIEW):
            partial[f.key] = fs.value
        missing.append(f.key)
    return {
        "fields": fields,
        "partial": partial,
        "missing": missing,
        "refs": refs,
        "normalized": normalized,
    }


def build_payload(
    run: ScenarioRun,
    defn: ScenarioDef,
    *,
    contact: Contact | None,
    channel: Channel | None,
    event: str,
    status: str,
    delivery_id: str,
    seq: int,
) -> dict[str, Any]:
    data = collected(defn, run.fields)
    return {
        "event": f"scenario.{event}",
        "result_id": str(run.id),
        "delivery_id": delivery_id,
        "seq": seq,
        "scenario": run.scenario_key,
        "scenario_version": run.scenario_version,
        "status": status,
        "client": {
            "channel": channel.kind if channel is not None else None,
            "channel_id": str(run.channel_id),
            "id": contact.external_id if contact is not None else None,
            "username": contact.username if contact is not None else None,
            "name": contact.name if contact is not None else None,
        },
        "fields": data["fields"],
        "partial": data["partial"],
        "missing": data["missing"],
        **({"refs": data["refs"]} if data["refs"] else {}),
        **({"normalized": data["normalized"]} if data["normalized"] else {}),
        # an external event the run got (POST /v1/events): name and data
        **({"external": run.data["external"]} if (run.data or {}).get("external") else {}),
        "client_language": run.client_language,
        "reply_language": run.reply_language,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
    }
