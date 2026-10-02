"""Instagram webhook endpoint: Meta's check, the signature, saving client messages,
the merge pause and queued echoes."""

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from app.db.models import Channel, ChannelKind, ChannelStatus, Job, Message, MessageRole
from app.main import create_app
from app.services import Services
from tests.factories import make_channel, make_tenant

pytestmark = pytest.mark.db

SECRET = "app-secret"
VERIFY = "verify-me"
ACCOUNT = "17841400000000001"
CLIENT = "990000000000001"
URL = "/v1/channels/instagram/webhook"


@pytest.fixture
async def ig_services(clean_db, settings, clock, fake_llm) -> AsyncIterator[Services]:
    configured = settings.model_copy(
        update={
            "meta_app_id": "1234567890",
            "meta_app_secret": SecretStr(SECRET),
            "ig_webhook_verify_token": SecretStr(VERIFY),
        }
    )
    svc = Services.create(
        configured,
        clock=clock,
        backend=fake_llm,
        database_url=configured.test_database_url,
        llm_retry_delay_s=0,
    )
    try:
        yield svc
    finally:
        await svc.close()


@pytest.fixture
async def ig_client(ig_services) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(ig_services))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def item(
    mid: str, text: str, *, sender: str = CLIENT, recipient: str = ACCOUNT, echo: bool = False
) -> dict[str, Any]:
    message: dict[str, Any] = {"mid": mid, "text": text}
    if echo:
        message["is_echo"] = True
    return {
        "sender": {"id": sender},
        "recipient": {"id": recipient},
        "timestamp": 1790942400000,
        "message": message,
    }


def payload(*items: dict[str, Any]) -> dict[str, Any]:
    return {"object": "instagram", "entry": [{"id": ACCOUNT, "time": 1, "messaging": list(items)}]}


async def post(client: httpx.AsyncClient, data: Any, *, secret: str = SECRET) -> httpx.Response:
    body = json.dumps(data).encode() if not isinstance(data, bytes) else data
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        URL,
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )


async def ig_channel(services) -> tuple[Any, Any]:
    bundle = await make_tenant(services.sm)
    channel_id = await make_channel(
        services.sm, bundle.tenant_id, kind=ChannelKind.INSTAGRAM, account_id=ACCOUNT
    )
    return bundle, channel_id


async def messages(services) -> list[Message]:
    async with services.sm() as session:
        rows = await session.execute(select(Message).order_by(Message.created_at, Message.id))
        return list(rows.scalars())


async def jobs(services, kind: str) -> list[Job]:
    async with services.sm() as session:
        rows = await session.execute(select(Job).where(Job.kind == kind).order_by(Job.created_at))
        return list(rows.scalars())


# --- Meta's subscription check ----------------------------------------------------------


async def test_verify_returns_the_challenge(ig_client):
    res = await ig_client.get(
        URL,
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert res.status_code == 200
    assert res.text == "1158201444"


async def test_verify_with_a_wrong_token(ig_client):
    res = await ig_client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"}
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "instagram.bad_verify_token"


async def test_not_configured_server(client):
    # the default test settings have no Meta app
    res = await client.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "x"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "instagram.not_configured"
    res = await client.post(URL, content=b"{}")
    assert res.status_code == 503


# --- POST -------------------------------------------------------------------------------


async def test_bad_signature_is_rejected(ig_services, ig_client):
    await ig_channel(ig_services)
    res = await post(ig_client, payload(item("m1", "hi")), secret="other")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "instagram.bad_signature"
    assert await messages(ig_services) == []


async def test_bad_json(ig_client):
    res = await post(ig_client, b"not json")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "request.bad_json"


async def test_client_message_is_saved_and_the_turn_waits_the_pause(ig_services, ig_client, clock):
    bundle, channel_id = await ig_channel(ig_services)
    res = await post(ig_client, payload(item("m1", "Скільки коштує манікюр?")))
    assert res.status_code == 200
    assert res.json() == {"ok": True, "saved": 1, "echoes": 0}

    (msg,) = await messages(ig_services)
    assert msg.role == MessageRole.CLIENT
    assert msg.channel_id == channel_id
    assert msg.tenant_id == bundle.tenant_id
    assert msg.external_id == "m1"
    assert msg.text == "Скільки коштує манікюр?"
    assert msg.meta["source"] == "instagram"

    (turn,) = await jobs(ig_services, "run_turn")
    assert turn.payload == {"conversation_id": str(msg.conversation_id)}
    assert turn.run_at == clock.now() + timedelta(seconds=5)
    # the client's username / name are fetched once, in the background
    (profile,) = await jobs(ig_services, "ig_profile")
    assert profile.run_at == clock.now()


async def test_the_same_mid_is_saved_once(ig_services, ig_client):
    await ig_channel(ig_services)
    data = payload(item("m1", "hi"))
    assert (await post(ig_client, data)).json()["saved"] == 1
    assert (await post(ig_client, data)).json()["saved"] == 0
    assert len(await messages(ig_services)) == 1


async def test_three_quick_messages_make_one_turn(ig_services, ig_client, clock):
    await ig_channel(ig_services)
    start = clock.now()
    await post(ig_client, payload(item("m1", "Привіт")))
    clock.advance(seconds=3)
    await post(ig_client, payload(item("m2", "хочу на манікюр")))
    clock.advance(seconds=3)
    await post(ig_client, payload(item("m3", "в п'ятницю")))

    (turn,) = await jobs(ig_services, "run_turn")
    assert turn.run_at == start + timedelta(seconds=11)
    # one conversation for all three
    assert len({m.conversation_id for m in await messages(ig_services)}) == 1


async def test_a_long_burst_waits_no_more_than_max_wait(ig_services, ig_client, clock):
    await ig_channel(ig_services)
    start = clock.now()
    for i in range(5):
        await post(ig_client, payload(item(f"m{i}", f"part {i}")))
        clock.advance(seconds=4.5)
    (turn,) = await jobs(ig_services, "run_turn")
    # the last message came at +18 s: +23 s would be later than first + 20 s
    assert turn.run_at == start + timedelta(seconds=20)


async def test_channel_merge_settings_are_used(ig_services, ig_client, clock):
    _, channel_id = await ig_channel(ig_services)
    async with ig_services.sm() as session, session.begin():
        await session.execute(
            update(Channel).where(Channel.id == channel_id).values(settings={"merge_pause_s": 2})
        )
    await post(ig_client, payload(item("m1", "hi")))
    (turn,) = await jobs(ig_services, "run_turn")
    assert turn.run_at == clock.now() + timedelta(seconds=2)


async def test_two_clients_get_two_conversations(ig_services, ig_client):
    await ig_channel(ig_services)
    await post(ig_client, payload(item("m1", "hi", sender="c1"), item("m2", "hello", sender="c2")))
    rows = await messages(ig_services)
    assert len({m.conversation_id for m in rows}) == 2
    assert len(await jobs(ig_services, "run_turn")) == 2


async def test_unknown_or_disabled_account_is_ignored(ig_services, ig_client):
    _, channel_id = await ig_channel(ig_services)
    res = await post(ig_client, payload(item("m1", "hi", recipient="17841499999999999")))
    assert res.json()["saved"] == 0

    async with ig_services.sm() as session, session.begin():
        await session.execute(
            update(Channel).where(Channel.id == channel_id).values(status=ChannelStatus.DISABLED)
        )
    res = await post(ig_client, payload(item("m2", "hi")))
    assert res.status_code == 200
    assert res.json()["saved"] == 0
    assert await messages(ig_services) == []


async def test_echo_is_queued_not_saved(ig_services, ig_client, clock):
    _, channel_id = await ig_channel(ig_services)
    res = await post(
        ig_client,
        payload(item("e1", "Manicure is 600", sender=ACCOUNT, recipient=CLIENT, echo=True)),
    )
    assert res.json() == {"ok": True, "saved": 0, "echoes": 1}
    assert await messages(ig_services) == []
    (job,) = await jobs(ig_services, "ig_echo")
    assert job.payload["channel_id"] == str(channel_id)
    assert job.payload["client_id"] == CLIENT
    assert job.payload["mid"] == "e1"
    # later: our reply's mid must be saved first
    assert job.run_at == clock.now() + timedelta(seconds=5)
    assert await jobs(ig_services, "run_turn") == []
