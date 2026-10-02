"""Stage 6 over POST /v1/messages on the full pipeline: the client confirms the booking
(the run waits for the CRM), the client sends a payment receipt (the vision model reads
it, code checks it, a person reviews it when the tenant wants)."""

import hashlib
import json

import httpx
import pytest
from sqlalchemy import select

from app.core.pipeline import full_pipeline
from app.db.models import (
    Contact,
    Conversation,
    LlmRole,
    OperatorTask,
    Receipt,
    ReceiptStatus,
    RunStatus,
    ScenarioRun,
    TaskKind,
    TaskStatus,
)
from app.decisions.fake import FakeDecider
from app.prompts.seed import seed_prompts
from app.scenarios.questions import CONFIRM
from app.scenarios.typed import local_now
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import (
    ROUTE_DEFAULTS,
    deliveries_of,
    install_scenario,
    make_def,
    make_endpoint,
    states,
)
from tests.stage6_helpers import order_data, salon_data

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT, LlmRole.VISION)
RECEIPT_URL = "https://receipts.test/r1.png"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
SALON_DONE = {"service": "манікюр", "date": "2026-10-09", "time": "15:00", "phone": "+380671234567"}


async def _setup(services, data, *, settings=None, fields=None, asking=None, run_data=None):
    decider = FakeDecider(dict(ROUTE_DEFAULTS))
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES, supports_vision=True)
    bundle = await make_tenant(
        services.sm, settings={"languages": ["uk", "en"], **(settings or {})}
    )
    scenario_id = await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
    await make_endpoint(services, bundle.tenant_id, url="http://sink.test/results")
    defn = make_def(data)
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        contact = Contact(
            tenant_id=bundle.tenant_id, channel_id=bundle.channel_id, external_id="c1", name="Olha"
        )
        session.add(contact)
        await session.flush()
        conv = Conversation(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            contact_id=contact.id,
            client_language="uk",
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
            status=RunStatus.FOCUS,
            fields=states(defn, **(fields or {})),
            asking=asking,
            events=[],
            delivery_seq=0,
            client_language="uk",
            reply_language="uk",
            started_at=now,
            last_activity_at=now,
            data=run_data or {},
        )
        session.add(run)
        await session.flush()
        run_id = run.id

    return {"bundle": bundle, "decider": decider, "run_id": run_id}


def _cdn(request: httpx.Request) -> httpx.Response:
    if str(request.url) != RECEIPT_URL:
        return httpx.Response(404)
    return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})


@pytest.fixture(autouse=True)
async def cdn(services):
    """The receipt image comes from a fake CDN; deliveries stay queued (no worker here).
    The shared client is put back: `services` lives for the whole test session."""
    old = services.http
    services.http = httpx.AsyncClient(transport=httpx.MockTransport(_cdn))
    yield
    await services.http.aclose()
    services.http = old


async def _say(client, s, fake_llm, text, *, route=None, script=(), attachments=None):
    s["decider"].rules = {**ROUTE_DEFAULTS, **(route or {})}
    rewrite = chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False))
    fake_llm.push(rewrite, *script, chat_reply("Добре."))
    body = {"contact": {"external_id": "c1", "name": "Olha"}, "text": text}
    if attachments:
        body["attachments"] = attachments
    resp = await client.post("/v1/messages", json=body, headers=s["bundle"].auth)
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()


async def _run(services, run_id) -> ScenarioRun:
    async with services.sm() as session:
        return await session.get(ScenarioRun, run_id)


def _step(fake_llm) -> dict:
    return json.loads(fake_llm.requests[-1]["messages"][-1]["content"])["scenario_step"]


# --- confirm ----------------------------------------------------------------------------


async def test_yes_to_the_summary_sends_the_booking_to_the_crm(client, services, fake_llm):
    s = await _setup(services, salon_data(), fields=SALON_DONE, asking=CONFIRM)
    await _say(client, s, fake_llm, "Так, усе вірно", route={"confirm:salon": "yes"})

    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.WAITING
    assert run.asking is None
    assert run.data["confirmed_at"]
    assert run.data["waiting_for"] == "event"
    names = [e["event"] for e in run.events]
    assert {"confirmed", "waiting"} <= set(names)
    [delivery] = await deliveries_of(services.sm, s["run_id"])
    assert delivery.event == "waiting"


async def test_no_to_the_summary_keeps_asking(client, services, fake_llm):
    s = await _setup(services, salon_data(), fields=SALON_DONE, asking=CONFIRM)
    await _say(client, s, fake_llm, "Ні, не так", route={"confirm:salon": "no"})
    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.FOCUS
    assert run.asking == CONFIRM
    assert "confirmed_at" not in run.data
    assert await deliveries_of(services.sm, s["run_id"]) == []


# --- receipts ---------------------------------------------------------------------------


def _vision(services, **over) -> dict:
    today = local_now(services.clock.now(), "Europe/Kyiv").date()
    data = {
        "is_receipt": True,
        "readable": True,
        "amount": 500,
        "currency": "UAH",
        "date": today.isoformat(),
        "recipient": "ФОП Іваненко",
        "recipient_account": None,
        "reference": "R-1",
        "purpose": "Замовлення",
        "status": "success",
    }
    data.update(over)
    return chat_reply(json.dumps(data, ensure_ascii=False))


async def _order(services, **settings):
    values = {"item": "крем", "phone": "+380671234567"}
    confirmed = {"confirmed_at": services.clock.now().isoformat()}
    return await _setup(
        services,
        order_data(),
        settings=settings,
        fields=values,
        asking="payment",
        run_data=confirmed,
    )


async def _receipts(services) -> list[Receipt]:
    async with services.sm() as session:
        return list((await session.execute(select(Receipt))).scalars())


async def _tasks(services) -> list[OperatorTask]:
    async with services.sm() as session:
        return list((await session.execute(select(OperatorTask))).scalars())


@pytest.fixture
def fetch_private(services, monkeypatch):
    # receipts.test does not resolve: let the fake CDN through the private-address check
    monkeypatch.setattr(services.settings, "receipt_fetch_private", True)


async def test_a_good_receipt_closes_the_order(client, services, fake_llm, fetch_private):
    s = await _order(services, receipt_review="issues_only")
    files = [{"type": "image", "url": RECEIPT_URL}]
    await _say(client, s, fake_llm, "Оплатила", script=[_vision(services)], attachments=files)

    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.COMPLETED
    assert run.fields["payment"]["state"] == "filled"
    assert run.fields["payment"]["norm"]["amount"] == "500"
    [receipt] = await _receipts(services)
    assert receipt.status == ReceiptStatus.ACCEPTED
    assert receipt.review_reason is None
    assert receipt.sha256
    assert receipt.issues == []
    assert await _tasks(services) == []
    [delivery] = await deliveries_of(services.sm, s["run_id"])
    assert delivery.event == "completed"
    # the vision model got the image itself
    vision = fake_llm.requests[1]["messages"][-1]["content"]
    assert any(part.get("type") == "image_url" for part in vision)


async def test_by_default_a_person_checks_every_receipt(client, services, fake_llm, fetch_private):
    s = await _order(services)  # receipt_review: always
    files = [{"type": "image", "url": RECEIPT_URL}]
    await _say(client, s, fake_llm, "Оплатила", script=[_vision(services)], attachments=files)

    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.WAITING
    assert run.data["waiting_for"] == "receipt_review"
    assert run.fields["payment"]["state"] == "review"
    [receipt] = await _receipts(services)
    assert (receipt.status, receipt.review_reason) == (ReceiptStatus.REVIEW, "always")
    assert run.data["receipt_id"] == str(receipt.id)
    [task] = await _tasks(services)
    assert (task.kind, task.status) == (TaskKind.REVIEW_RECEIPT, TaskStatus.OPEN)
    assert task.payload["receipt_id"] == str(receipt.id)
    assert await deliveries_of(services.sm, s["run_id"]) == []
    assert _step(fake_llm)["waiting"]


async def test_a_wrong_amount_goes_to_a_person(client, services, fake_llm, fetch_private):
    s = await _order(services, receipt_review="issues_only")
    files = [{"type": "image", "url": RECEIPT_URL}]
    vision = _vision(services, amount=50)
    await _say(client, s, fake_llm, "Оплатила", script=[vision], attachments=files)
    [receipt] = await _receipts(services)
    assert receipt.status == ReceiptStatus.REVIEW
    assert receipt.review_reason == "issues"
    assert [i["code"] for i in receipt.issues] == ["amount_mismatch"]


async def test_not_a_receipt_is_asked_again(client, services, fake_llm, fetch_private):
    s = await _order(services, receipt_review="issues_only")
    files = [{"type": "image", "url": RECEIPT_URL}]
    vision = _vision(services, is_receipt=False)
    await _say(client, s, fake_llm, "Ось", script=[vision], attachments=files)
    run = await _run(services, s["run_id"])
    assert run.status == RunStatus.FOCUS
    assert run.asking == "payment"
    assert run.fields["payment"]["failed_filters"] == ["@receipt_not_receipt"]
    [receipt] = await _receipts(services)
    assert receipt.status == ReceiptStatus.REJECTED
    assert await _tasks(services) == []


async def test_an_image_already_paid_with_is_a_duplicate(client, services, fake_llm, fetch_private):
    s = await _order(services, receipt_review="issues_only")
    async with services.sm() as session, session.begin():
        run = await session.get(ScenarioRun, s["run_id"])
        session.add(
            Receipt(
                tenant_id=run.tenant_id,
                conversation_id=run.conversation_id,
                run_id=run.id,
                field_key="payment",
                url="https://receipts.test/old.png",
                sha256=hashlib.sha256(PNG).hexdigest(),
                extracted={},
                reference="R-0",
                expected={},
                issues=[],
                status=ReceiptStatus.ACCEPTED,
            )
        )
    files = [{"type": "image", "url": RECEIPT_URL}]
    await _say(client, s, fake_llm, "Ось", script=[_vision(services)], attachments=files)
    new = [r for r in await _receipts(services) if r.url == RECEIPT_URL]
    assert len(new) == 1
    assert [i["code"] for i in new[0].issues] == ["duplicate_image"]
    assert (new[0].status, new[0].review_reason) == (ReceiptStatus.REVIEW, "issues")


async def test_a_private_address_is_not_fetched(client, services, fake_llm):
    s = await _order(services, receipt_review="issues_only")
    files = [{"type": "image", "url": "http://127.0.0.1/r.png"}]
    await _say(client, s, fake_llm, "Ось", attachments=files)  # no vision call
    [receipt] = await _receipts(services)
    assert [i["code"] for i in receipt.issues] == ["fetch_failed"]
    run = await _run(services, s["run_id"])
    assert run.fields["payment"]["failed_filters"] == ["@receipt_unreadable"]


async def test_asking_for_the_receipt_tells_what_to_pay(client, services, fake_llm):
    s = await _order(services)
    await _say(client, s, fake_llm, "А куди платити?", route={"client_question": True})
    step = _step(fake_llm)
    assert step["answer_client_question_first"] is True
    assert step["ask_next"]["payment"] == {"amount": "500", "currency": "UAH"}
    assert step["ask_next"]["attachment"] is True
