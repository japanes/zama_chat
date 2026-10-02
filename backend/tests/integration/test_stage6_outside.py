"""Changes of a run from outside a turn (stage 6): POST /v1/events from the CRM, an
operator approving a scenario action, an operator's verdict on a receipt in review."""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.actions.registry import fire
from app.db.models import (
    Contact,
    Conversation,
    Message,
    MessageRole,
    OperatorTask,
    Receipt,
    ReceiptStatus,
    RunStatus,
    ScenarioRun,
    TaskKind,
    TaskStatus,
    UserRole,
)
from app.results.delivery import sign
from app.scenarios.events import Event
from app.scenarios.fields import REVIEW, FieldState
from tests.admin_helpers import admin_headers
from tests.factories import make_tenant
from tests.scenario_helpers import deliveries_of, install_scenario, make_def, make_endpoint, states
from tests.stage6_helpers import cancel_data, order_data, salon_data

pytestmark = pytest.mark.db

SECRET = "crm-secret"
SALON_DONE = {"service": "манікюр", "date": "2026-10-09", "time": "15:00", "phone": "+380671234567"}


async def _setup(services, data, *, status=RunStatus.WAITING, fields=None, run_data=None):
    bundle = await make_tenant(services.sm, settings={"languages": ["uk", "en"]})
    scenario_id = await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
    endpoint_id = await make_endpoint(
        services, bundle.tenant_id, url="http://test/dev/sink/results", secret=SECRET
    )
    defn = make_def(data)
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        contact = Contact(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            external_id="c1",
            name="Olha",
        )
        session.add(contact)
        await session.flush()
        conv = Conversation(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            contact_id=contact.id,
            reply_language="uk",
            last_client_at=now,
        )
        session.add(conv)
        await session.flush()
        run = ScenarioRun(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            conversation_id=conv.id,
            scenario_id=scenario_id,
            scenario_key=defn.key,
            scenario_version=1,
            status=status,
            fields=fields if fields is not None else states(defn, **SALON_DONE),
            events=[],
            delivery_seq=0,
            client_language="uk",
            reply_language="uk",
            started_at=now,
            last_activity_at=now,
            data=run_data
            if run_data is not None
            else {
                "confirmed_at": now.isoformat(),
                "waiting_for": "event",
                "waiting_since": now.isoformat(),
            },
        )
        session.add(run)
        await session.flush()
        return {
            "bundle": bundle,
            "defn": defn,
            "run_id": run.id,
            "conv_id": conv.id,
            "endpoint_id": endpoint_id,
        }


async def _run(services, run_id) -> ScenarioRun:
    async with services.sm() as session:
        return await session.get(ScenarioRun, run_id)


async def _bot_messages(services, conv_id) -> list[str]:
    async with services.sm() as session:
        rows = await session.execute(
            select(Message.text)
            .where(Message.conversation_id == conv_id, Message.role == MessageRole.BOT)
            .order_by(Message.created_at)
        )
        return list(rows.scalars())


async def _event(client, s, event, *, data=None, event_id=None, result_id=None):
    body = {"result_id": str(result_id or s["run_id"]), "event": event, "data": data or {}}
    if event_id is not None:
        body["event_id"] = event_id
    return await client.post("/v1/events", json=body, headers=s["bundle"].auth)


# --- POST /v1/events --------------------------------------------------------------------


async def test_booked_completes_the_run_and_tells_the_client(services, client):
    s = await _setup(services, salon_data())
    res = await _event(client, s, "booked", data={"booking_id": "B-1"})
    assert res.status_code == 200, res.text
    assert res.json() == {"result_id": str(s["run_id"]), "status": "completed", "asking": None}

    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.COMPLETED
    assert run.finished_at is not None
    assert run.data["external"]["event"] == "booked"
    assert run.data["external"]["data"] == {"booking_id": "B-1"}
    assert "waiting_for" not in run.data
    assert [e["event"] for e in run.events] == ["external", "completed"]
    assert await _bot_messages(services, s["conv_id"]) == ["Записали на 2026-10-09 о 15:00."]
    [delivery] = await deliveries_of(services.sm, s["run_id"])
    assert delivery.event == "completed"


async def test_slot_taken_reopens_the_fields_and_drops_the_yes(services, client):
    s = await _setup(services, salon_data())
    res = await _event(client, s, "slot_taken")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "focus"
    assert res.json()["asking"] == "date"

    run = await _run(services, s["run_id"])
    assert run.fields["date"]["state"] == "empty"
    assert run.fields["time"]["value"] is None
    assert run.fields["service"]["value"] == "манікюр"  # the rest stays
    assert "confirmed_at" not in run.data
    assert [e["event"] for e in run.events] == ["external", "reopened"]
    assert await _bot_messages(services, s["conv_id"]) == ["Час зайнятий."]


async def test_a_reopened_run_pauses_when_another_has_the_focus(services, client):
    s = await _setup(services, salon_data())
    other = await install_scenario(
        services.sm, s["bundle"].tenant_id, s["bundle"].channel_id, order_data()
    )
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        session.add(
            ScenarioRun(
                tenant_id=s["bundle"].tenant_id,
                channel_id=s["bundle"].channel_id,
                conversation_id=s["conv_id"],
                scenario_id=other,
                scenario_key="order",
                scenario_version=1,
                status=RunStatus.FOCUS,
                fields={},
                events=[],
                delivery_seq=0,
                started_at=now,
                last_activity_at=now,
            )
        )
    res = await _event(client, s, "slot_taken")
    assert res.json()["status"] == "paused"


async def test_failed_cancels_without_a_message(services, client):
    s = await _setup(services, salon_data())
    res = await _event(client, s, "failed")
    assert res.json()["status"] == "cancelled"
    assert await _bot_messages(services, s["conv_id"]) == []


async def test_the_same_event_id_twice_changes_nothing(services, client):
    s = await _setup(services, salon_data())
    first = await _event(client, s, "slot_taken", event_id="crm-1")
    assert first.status_code == 200
    again = await _event(client, s, "slot_taken", event_id="crm-1")
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    run = await _run(services, s["run_id"])
    assert run.data["events_seen"] == ["crm-1"]
    assert len(await _bot_messages(services, s["conv_id"])) == 1


async def test_an_event_for_a_run_not_waiting_is_a_conflict(services, client):
    s = await _setup(services, salon_data(), status=RunStatus.FOCUS, run_data={})
    res = await _event(client, s, "booked")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "event.run_not_waiting"


async def test_an_unknown_event_names_the_known_ones(services, client):
    s = await _setup(services, salon_data())
    res = await _event(client, s, "paid")
    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "event.unknown"
    assert error["params"] == {"event": "paid", "known": ["booked", "failed", "slot_taken"]}


async def test_another_tenant_can_not_send_events(services, client):
    s = await _setup(services, salon_data())
    other = await make_tenant(services.sm, slug="other")
    body = {"result_id": str(s["run_id"]), "event": "booked"}
    res = await client.post("/v1/events", json=body, headers=other.auth)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "result.not_found"


async def test_the_endpoint_signature_is_enough(services, client):
    s = await _setup(services, salon_data())
    raw = json.dumps({"result_id": str(s["run_id"]), "event": "booked"}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Zama-Endpoint": str(s["endpoint_id"]),
        "X-Zama-Signature": sign(SECRET, raw),
    }
    res = await client.post("/v1/events", content=raw, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"


async def test_a_wrong_signature_is_rejected(services, client):
    s = await _setup(services, salon_data())
    raw = json.dumps({"result_id": str(s["run_id"]), "event": "booked"}).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Zama-Endpoint": str(s["endpoint_id"]),
        "X-Zama-Signature": sign("wrong", raw),
    }
    res = await client.post("/v1/events", content=raw, headers=headers)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "auth.invalid_signature"
    res = await client.post(
        "/v1/events", content=raw, headers={**headers, "X-Zama-Endpoint": "not-a-uuid"}
    )
    assert res.status_code == 401


async def test_no_auth_at_all_is_rejected(services, client):
    s = await _setup(services, salon_data())
    res = await client.post("/v1/events", json={"result_id": str(s["run_id"]), "event": "booked"})
    assert res.status_code == 401


async def test_a_broken_body_is_422(services, client):
    s = await _setup(services, salon_data())
    res = await client.post("/v1/events", json={"event": "booked"}, headers=s["bundle"].auth)
    assert res.status_code == 422
    res = await _event(client, s, "booked", result_id=uuid.uuid4())
    assert res.status_code == 404


# --- approvals --------------------------------------------------------------------------


async def _approval(services, client):
    s = await _setup(
        services,
        cancel_data(),
        status=RunStatus.COMPLETED,
        fields=states(make_def(cancel_data()), booking="манікюр у п'ятницю"),
        run_data={"confirmed_at": services.clock.now().isoformat()},
    )
    async with services.sm() as session, session.begin():
        run = await session.get(ScenarioRun, s["run_id"])
        ran = await fire(
            session, services, run, s["defn"], [Event("completed")], now=services.clock.now()
        )
    assert ran == 1
    assert await deliveries_of(services.sm, s["run_id"]) == []  # waits for a person
    async with services.sm() as session:
        task = (
            await session.execute(
                select(OperatorTask).where(OperatorTask.conversation_id == s["conv_id"])
            )
        ).scalar_one()
    assert task.kind == TaskKind.APPROVE_ACTION
    assert task.payload["do"] == "http_post"
    assert task.payload["event"] == "completed"
    assert "манікюр у п'ятницю" in task.payload["fields"]
    s["task_id"] = task.id
    s["headers"] = await admin_headers(
        services, "op@example.com", tenant_id=s["bundle"].tenant_id, role=UserRole.OPERATOR
    )
    s["base"] = f"/v1/admin/tenants/{s['bundle'].tenant_id}"
    return s


async def _resolve(client, s, task_id, **body):
    return await client.post(
        f"{s['base']}/operator/tasks/{task_id}/resolve",
        json={"version": 1, **body},
        headers=s["headers"],
    )


async def test_approve_runs_the_action(services, client):
    s = await _approval(services, client)
    res = await _resolve(client, s, s["task_id"], action="approve")
    assert res.status_code == 200, res.text
    assert res.json()["task"]["status"] == TaskStatus.DONE
    assert res.json()["task"]["result"]["ran"] is True
    [delivery] = await deliveries_of(services.sm, s["run_id"])
    assert delivery.event == "completed"


async def test_reject_drops_the_action(services, client):
    s = await _approval(services, client)
    res = await _resolve(client, s, s["task_id"], action="reject")
    assert res.status_code == 200, res.text
    assert res.json()["task"]["status"] == TaskStatus.CANCELLED
    assert await deliveries_of(services.sm, s["run_id"]) == []


async def test_an_approval_can_not_be_accepted_like_a_receipt(services, client):
    s = await _approval(services, client)
    res = await _resolve(client, s, s["task_id"], action="accept")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "operator_task.action_invalid"


# --- a receipt in review ----------------------------------------------------------------


async def _receipt_review(services):
    defn = make_def(order_data())
    now = services.clock.now()
    review = FieldState(state=REVIEW, value="500 UAH, 2026-10-01", norm={"receipt_id": "x"})
    fields = states(defn, item="крем", phone="+380671234567", payment=review)
    s = await _setup(
        services, order_data(), fields=fields, run_data={"confirmed_at": now.isoformat()}
    )
    async with services.sm() as session, session.begin():
        receipt = Receipt(
            tenant_id=s["bundle"].tenant_id,
            conversation_id=s["conv_id"],
            run_id=s["run_id"],
            field_key="payment",
            url="https://cdn.example/r.jpg",
            extracted={"is_receipt": True, "amount": 500},
            expected={"amount": "500", "currency": "UAH"},
            issues=[],
            status=ReceiptStatus.REVIEW,
            review_reason="always",
        )
        session.add(receipt)
        await session.flush()
        run = await session.get(ScenarioRun, s["run_id"])
        run.data = {
            "confirmed_at": now.isoformat(),
            "waiting_for": "receipt_review",
            "waiting_since": now.isoformat(),
            "receipt_id": str(receipt.id),
        }
        conv = await session.get(Conversation, s["conv_id"])
        task = OperatorTask(
            tenant_id=s["bundle"].tenant_id,
            channel_id=s["bundle"].channel_id,
            conversation_id=s["conv_id"],
            kind=TaskKind.REVIEW_RECEIPT,
            status=TaskStatus.OPEN,
            version=1,
            payload={"receipt_id": str(receipt.id), "run_id": str(s["run_id"])},
            result={},
        )
        session.add(task)
        await session.flush()
        assert conv is not None
        s.update(receipt_id=receipt.id, task_id=task.id)
    s["headers"] = await admin_headers(
        services, "op@example.com", tenant_id=s["bundle"].tenant_id, role=UserRole.OPERATOR
    )
    s["base"] = f"/v1/admin/tenants/{s['bundle'].tenant_id}"
    return s


async def test_an_accepted_receipt_completes_the_order(services, client):
    s = await _receipt_review(services)
    res = await _resolve(client, s, s["task_id"], action="accept", note="ок, бачу в банку")
    assert res.status_code == 200, res.text
    result = res.json()["task"]["result"]
    assert result["receipt_status"] == ReceiptStatus.ACCEPTED
    assert result["run"]["status"] == RunStatus.COMPLETED

    async with services.sm() as session:
        receipt = await session.get(Receipt, s["receipt_id"])
    assert receipt.status == ReceiptStatus.ACCEPTED
    assert receipt.decision_note == "ок, бачу в банку"
    run = await _run(services, s["run_id"])
    assert run.fields["payment"]["state"] == "filled"
    assert "receipt_id" not in run.data
    assert await _bot_messages(services, s["conv_id"]) == ["Дякуємо, оплату підтверджено."]
    [delivery] = await deliveries_of(services.sm, s["run_id"])
    assert delivery.event == "completed"


async def test_a_rejected_receipt_asks_the_client_again(services, client):
    s = await _receipt_review(services)
    res = await _resolve(client, s, s["task_id"], action="reject")
    assert res.status_code == 200, res.text
    assert res.json()["task"]["status"] == TaskStatus.DONE  # a decision, not dropped
    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.FOCUS
    assert run.asking == "payment"
    assert run.fields["payment"]["state"] == "partial"
    assert run.fields["payment"]["failed_filters"] == ["@receipt_rejected"]
    assert "waiting_for" not in run.data
    [text] = await _bot_messages(services, s["conv_id"])
    assert text.startswith("На жаль, ми не змогли підтвердити оплату")


async def test_a_receipt_is_decided_once(services, client):
    s = await _receipt_review(services)
    assert (await _resolve(client, s, s["task_id"], action="accept")).status_code == 200
    res = await _resolve(client, s, s["task_id"], action="reject")
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "operator_task.closed"


async def test_a_receipt_task_can_not_be_dismissed(services, client):
    s = await _receipt_review(services)
    res = await _resolve(client, s, s["task_id"], action="dismiss")
    assert res.status_code == 400


async def test_waiting_too_long_for_the_crm_abandons_the_run(services):
    """waiting.hours: the expire job closes a run nobody answered."""
    from app.scenarios.store import expires_at

    s = await _setup(services, salon_data())
    run = await _run(services, s["run_id"])
    at = expires_at(s["defn"], run.last_activity_at, status=run.status, data=run.data)
    assert at == run.last_activity_at + timedelta(hours=2)
