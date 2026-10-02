"""Notification channels: admin CRUD with checks, the test button, queued events.
Telegram is a mock transport — no network."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Job, JobStatus, Notifier
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.main import create_app
from app.notify.service import notify_event
from app.services import Services
from tests.admin_helpers import admin_headers
from tests.factories import make_tenant

pytestmark = pytest.mark.db

TOKEN = "123456789:AAE_test_token_for_the_bot_xxxxxxxx"
GOOD_CHAT = "-1001234567890"


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        body = json.loads(request.content)
        if str(body["chat_id"]) != GOOD_CHAT:
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: chat not found"}
            )
        self.sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()


@pytest.fixture
async def tg_services(clean_db, settings, clock, fake_llm, telegram) -> AsyncIterator[Services]:
    svc = Services.create(
        settings,
        clock=clock,
        backend=fake_llm,
        http=httpx.AsyncClient(transport=httpx.MockTransport(telegram)),
        database_url=settings.test_database_url,
        llm_retry_delay_s=0,
    )
    try:
        yield svc
    finally:
        await svc.close()


@pytest.fixture
async def tg_client(tg_services) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(tg_services))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _admin(services) -> tuple[str, dict[str, str], Any]:
    bundle = await make_tenant(services.sm)
    headers = await admin_headers(services, tenant_id=bundle.tenant_id)
    return f"/v1/admin/tenants/{bundle.tenant_id}", headers, bundle.tenant_id


def telegram_body(**over: Any) -> dict[str, Any]:
    body = {
        "name": "team",
        "kind": "telegram",
        "targets": [GOOD_CHAT],
        "secret": TOKEN,
        "events": ["handover", "choose_reply"],
        "language": "uk",
    }
    body.update(over)
    return body


# --- CRUD -------------------------------------------------------------------------------


async def test_create_telegram_notifier(tg_services, tg_client):
    base, headers, tenant_id = await _admin(tg_services)
    res = await tg_client.post(f"{base}/notifiers", json=telegram_body(), headers=headers)
    assert res.status_code == 200, res.text
    out = res.json()["notifier"]
    assert out["targets"] == [GOOD_CHAT]
    assert out["has_secret"] is True
    assert TOKEN not in res.text  # the token is never sent back

    async with tg_services.sm() as session:
        row = (await session.execute(select(Notifier))).scalar_one()
    assert row.tenant_id == tenant_id
    assert row.secret_enc is not None and TOKEN.encode() not in row.secret_enc
    assert tg_services.crypto.decrypt(row.secret_enc) == TOKEN

    listed = (await tg_client.get(f"{base}/notifiers", headers=headers)).json()
    assert [n["name"] for n in listed["items"]] == ["team"]
    assert "handover" in listed["events"]


@pytest.mark.parametrize(
    ("over", "code"),
    [
        ({"secret": None}, "notifier.token_required"),
        ({"targets": []}, "notifier.targets_required"),
        ({"targets": ["not a chat"]}, "notifier.target_invalid"),
        ({"targets": [str(-i) for i in range(1, 30)]}, "notifier.too_many_targets"),
    ],
)
async def test_bad_telegram_notifiers(tg_services, tg_client, over, code):
    base, headers, _ = await _admin(tg_services)
    res = await tg_client.post(f"{base}/notifiers", json=telegram_body(**over), headers=headers)
    assert res.status_code >= 400
    assert res.json()["error"]["code"] == code


async def test_email_notifier_and_name_taken(tg_services, tg_client):
    base, headers, _ = await _admin(tg_services)
    body = {"name": "sales", "kind": "email", "targets": ["Boss@Example.com", "  "], "events": []}
    res = await tg_client.post(f"{base}/notifiers", json=body, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["notifier"]["targets"] == ["boss@example.com"]
    assert res.json()["notifier"]["has_secret"] is False

    res = await tg_client.post(f"{base}/notifiers", json=body, headers=headers)
    assert res.status_code == 409
    assert res.json()["error"] == {"code": "notifier.name_taken", "params": {"name": "sales"}}

    bad = {**body, "name": "other", "targets": ["not-an-email"]}
    res = await tg_client.post(f"{base}/notifiers", json=bad, headers=headers)
    assert res.json()["error"]["code"] == "notifier.target_invalid"


async def test_update_keeps_the_token_and_delete(tg_services, tg_client):
    base, headers, _ = await _admin(tg_services)
    created = (
        await tg_client.post(f"{base}/notifiers", json=telegram_body(), headers=headers)
    ).json()["notifier"]
    res = await tg_client.patch(
        f"{base}/notifiers/{created['id']}",
        json={"targets": [GOOD_CHAT, "@zama_team"], "is_active": False},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    updated = res.json()["notifier"]
    assert updated["targets"] == [GOOD_CHAT, "@zama_team"]
    assert updated["is_active"] is False
    assert updated["has_secret"] is True

    res = await tg_client.delete(f"{base}/notifiers/{created['id']}", headers=headers)
    assert res.status_code == 200
    assert (await tg_client.get(f"{base}/notifiers", headers=headers)).json()["items"] == []


async def test_operator_cannot_manage_notifiers(tg_services, tg_client):
    bundle = await make_tenant(tg_services.sm)
    headers = await admin_headers(
        tg_services, "op@example.com", tenant_id=bundle.tenant_id, role="operator"
    )
    res = await tg_client.get(f"/v1/admin/tenants/{bundle.tenant_id}/notifiers", headers=headers)
    assert res.status_code == 403


# --- the test button --------------------------------------------------------------------


async def test_test_button_reports_each_chat(tg_services, tg_client, telegram):
    base, headers, _ = await _admin(tg_services)
    created = (
        await tg_client.post(
            f"{base}/notifiers", json=telegram_body(targets=[GOOD_CHAT, "-42"]), headers=headers
        )
    ).json()["notifier"]
    res = await tg_client.post(f"{base}/notifiers/{created['id']}/test", headers=headers)
    assert res.status_code == 200
    ok, failed = res.json()["results"]
    assert ok == {"target": GOOD_CHAT, "ok": True}
    assert failed["target"] == "-42"
    assert failed["ok"] is False
    assert "chat not found" in failed["error"]
    # the token is in Telegram's URL, never in what the admin sees
    assert TOKEN not in res.text
    assert all(f"/bot{TOKEN}/sendMessage" in url for url in telegram.urls)
    (sent,) = telegram.sent
    assert "Zama Chat" in sent["text"]


# --- events through the queue -----------------------------------------------------------


async def test_event_goes_to_subscribed_active_notifiers(tg_services, tg_client, telegram):
    base, headers, tenant_id = await _admin(tg_services)
    await tg_client.post(f"{base}/notifiers", json=telegram_body(), headers=headers)
    # subscribed to another event only
    await tg_client.post(
        f"{base}/notifiers",
        json=telegram_body(name="errors", events=["channel_error"]),
        headers=headers,
    )

    values = {"contact": "Olha (@olha)", "channel": "Zama Salon", "text": "Покличте людину"}
    async with tg_services.sm() as session, session.begin():
        queued = await notify_event(
            session,
            tenant_id=tenant_id,
            event="handover",
            values=values,
            now=tg_services.clock.now(),
        )
    assert queued == 1

    worker = Worker(tg_services.sm, tg_services.clock, HANDLERS, services=tg_services)
    assert await worker.run_due() == 1
    (sent,) = telegram.sent
    assert sent["chat_id"] == GOOD_CHAT
    assert "Olha (@olha)" in sent["text"]
    assert "Покличте людину" in sent["text"]


async def test_wrong_chat_fails_without_retries(tg_services, tg_client):
    base, headers, tenant_id = await _admin(tg_services)
    await tg_client.post(f"{base}/notifiers", json=telegram_body(targets=["-42"]), headers=headers)
    async with tg_services.sm() as session, session.begin():
        await notify_event(
            session, tenant_id=tenant_id, event="handover", values={}, now=tg_services.clock.now()
        )
    worker = Worker(tg_services.sm, tg_services.clock, HANDLERS, services=tg_services)
    await worker.run_due()
    async with tg_services.sm() as session:
        (job,) = (await session.execute(select(Job).where(Job.kind == "notify"))).scalars()
    # a chat the bot is not in: retrying will not help
    assert job.status == JobStatus.FAILED
    assert job.attempts == 1
