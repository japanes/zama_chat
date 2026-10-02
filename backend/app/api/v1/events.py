"""POST /v1/events — an external system (CRM, booking calendar, payment service) tells
what happened to a request the bot collected (docs/03 §7).

The run waits (completion: external_event); the scenario's `events` say what each
event does:
- completed / cancelled — the run closes, its actions fire (final JSON, notifications);
- reopen — `reopen_fields` are asked again («this time is taken, pick another»).
`say` of the rule is the bot's message to the client.

Auth: `Authorization: Bearer <api key>` of the tenant, or the endpoint's signature —
`X-Zama-Endpoint: <endpoint id>` + `X-Zama-Signature: sha256=HMAC(secret, body)`, the
same secret our results are signed with.

Idempotent by `event_id`: the same event again returns the run as it is.
"""

import hmac
import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field, ValidationError

from app.api.deps import ServicesDep, api_client
from app.db.models import Endpoint, RunStatus, ScenarioRun
from app.errors import AppError, Conflict, NotFound, Unauthorized
from app.logging import get_logger
from app.results.delivery import sign
from app.scenarios import fields as fl
from app.scenarios.events import EXTERNAL, REOPENED
from app.scenarios.outside import (
    Change,
    back_status,
    confirmed,
    fill_say,
    language_of,
    lock_run,
    under_lease,
    write_run,
)
from app.scenarios.schema import ActionEvent, EventResult
from app.scenarios.store import load_definition

router = APIRouter(prefix="/v1", tags=["events"])
log = get_logger(__name__)

MAX_BODY = 64 * 1024
SEEN_KEEP = 50  # event_ids remembered per run


class EventIn(BaseModel):
    result_id: uuid.UUID
    event: str = Field(min_length=1, max_length=64)
    data: dict[str, Any] = Field(default_factory=dict)
    # the sender's id of this event: sending it again changes nothing
    event_id: str | None = Field(default=None, min_length=1, max_length=200)


async def _tenant_by_signature(
    services: ServicesDep, endpoint_id: str, signature: str, body: bytes
) -> uuid.UUID:
    try:
        eid = uuid.UUID(endpoint_id)
    except ValueError as exc:
        raise Unauthorized("auth.invalid_signature") from exc
    async with services.sm() as session:
        endpoint = await session.get(Endpoint, eid)
    if endpoint is None or not endpoint.is_active or endpoint.secret_enc is None:
        raise Unauthorized("auth.invalid_signature")
    expected = sign(services.crypto.decrypt(endpoint.secret_enc), body)
    if not hmac.compare_digest(expected, signature.strip()):
        raise Unauthorized("auth.invalid_signature")
    return endpoint.tenant_id


@router.post("/events")
async def post_event(
    request: Request,
    services: ServicesDep,
    authorization: Annotated[str | None, Header()] = None,
    x_zama_endpoint: Annotated[str | None, Header()] = None,
    x_zama_signature: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise AppError("event.too_large", {"max": MAX_BODY}, status=413)
    if x_zama_endpoint and x_zama_signature:
        tenant_id = await _tenant_by_signature(services, x_zama_endpoint, x_zama_signature, raw)
    else:
        tenant_id = (await api_client(services, authorization)).tenant_id
    try:
        body = EventIn.model_validate(json.loads(raw or b"{}"))
    except (ValueError, ValidationError) as exc:
        raise AppError("request.invalid", {"fields": []}, status=422) from exc

    async with services.sm() as session:
        found = await session.get(ScenarioRun, body.result_id)
        if found is None or found.tenant_id != tenant_id:
            raise NotFound("result.not_found", {"result_id": str(body.result_id)})
        conversation_id = found.conversation_id

    owner = f"event:{uuid.uuid4()}"
    return await under_lease(
        services,
        conversation_id,
        owner,
        lambda session, conv: _apply(session, services, conv, body, services.clock.now()),
    )


async def _apply(session, services, conv, body: EventIn, now: datetime) -> dict[str, Any]:
    run = await lock_run(session, body.result_id)
    assert run is not None
    data = dict(run.data or {})
    seen = list(data.get("events_seen") or [])
    if body.event_id is not None and body.event_id in seen:
        return _out(run, duplicate=True)
    if run.status != RunStatus.WAITING or data.get("waiting_for") != "event":
        raise Conflict("event.run_not_waiting", {"status": run.status})
    defn = await load_definition(session, run.scenario_id)
    rule = defn.events.get(body.event)
    if rule is None:
        raise AppError("event.unknown", {"event": body.event, "known": sorted(defn.events)})

    states = fl.load_states(defn, run.fields)
    change = Change()
    change.add(EXTERNAL, body.event)
    # the bot's words see the answers as they were (a reopened field is emptied below)
    change.say = fill_say(defn, rule.say, language_of(conv, run, defn), states, body.data)
    data["external"] = {"event": body.event, "data": body.data, "at": now.isoformat()}
    if body.event_id is not None:
        data["events_seen"] = [*seen, body.event_id][-SEEN_KEEP:]
    for key in ("waiting_for", "waiting_since"):
        data.pop(key, None)

    if rule.result == EventResult.COMPLETED:
        run.status = RunStatus.COMPLETED
        run.asking = None
        change.add(ActionEvent.COMPLETED)
    elif rule.result == EventResult.CANCELLED:
        run.status = RunStatus.CANCELLED
        run.asking = None
        change.add(ActionEvent.CANCELLED)
    else:
        for key in rule.reopen_fields:
            fl.reset(states[key], now)
        # changed answers need a new «yes»
        data.pop("confirmed_at", None)
        run.status = await back_status(session, run)
        nxt = fl.next_field(defn, states, confirmed=confirmed(defn, data))
        run.asking = nxt.key if nxt is not None else None
        change.add(REOPENED)

    await write_run(
        session,
        services,
        conv,
        run,
        defn,
        states,
        data,
        change,
        now,
        meta={"event": body.event, "event_id": body.event_id},
    )
    log.info("event.applied", run_id=str(run.id), result_event=body.event, status=run.status)
    return _out(run)


def _out(run: ScenarioRun, *, duplicate: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"result_id": str(run.id), "status": run.status, "asking": run.asking}
    if duplicate:
        out["duplicate"] = True
    return out
