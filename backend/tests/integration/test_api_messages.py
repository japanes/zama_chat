"""POST /v1/messages and /v1/conversations/history over the ASGI app (fake LLM)."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.db.models import (
    ApiKey,
    Channel,
    ChannelStatus,
    LlmCall,
    Message,
    MessageRole,
    SendStatus,
    Tenant,
    Turn,
    TurnStatus,
)
from app.llm.openai_client import LLMTransportError
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant

pytestmark = pytest.mark.db


def _msg(text: str, external_id: str = "c1", **extra) -> dict:
    return {"contact": {"external_id": external_id, "name": "Olha"}, "text": text, **extra}


async def _messages(sm, conversation_id=None) -> list[Message]:
    async with sm() as session:
        query = select(Message).order_by(Message.created_at, Message.id)
        if conversation_id is not None:
            query = query.where(Message.conversation_id == uuid.UUID(str(conversation_id)))
        return list((await session.execute(query)).scalars())


async def _turns(sm) -> list[Turn]:
    async with sm() as session:
        return list((await session.execute(select(Turn).order_by(Turn.created_at))).scalars())


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize("header", [None, "Token abc", "Bearer ", "Bearer zk_unknown"])
async def test_bad_api_key_is_rejected(client, services, fake_llm, header):
    await make_tenant(services.sm)
    headers = {"Authorization": header} if header is not None else {}
    resp = await client.post("/v1/messages", json=_msg("hi"), headers=headers)
    assert resp.status_code == 401
    assert resp.json() == {"error": {"code": "auth.invalid_api_key", "params": {}}}
    assert fake_llm.calls == 0


@pytest.mark.parametrize("blocked", ["revoked", "tenant_inactive", "channel_disabled"])
async def test_blocked_key_is_rejected(client, services, fake_llm, clock, blocked):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    async with services.sm() as session, session.begin():
        if blocked == "revoked":
            await session.execute(
                update(ApiKey).where(ApiKey.id == bundle.api_key_id).values(revoked_at=clock.now())
            )
        elif blocked == "tenant_inactive":
            await session.execute(
                update(Tenant).where(Tenant.id == bundle.tenant_id).values(is_active=False)
            )
        else:
            await session.execute(
                update(Channel)
                .where(Channel.id == bundle.channel_id)
                .values(status=ChannelStatus.DISABLED)
            )
    resp = await client.post("/v1/messages", json=_msg("hi"), headers=bundle.auth)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "auth.invalid_api_key"
    assert fake_llm.calls == 0


async def test_reply_is_returned_and_everything_is_stored(client, services, fake_llm, clock):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm, price_input="0.10", price_output="0.50")
    fake_llm.push(chat_reply("Hello, Olha!", tokens_in=1000, tokens_out=100))

    resp = await client.post("/v1/messages", json=_msg("hi"), headers=bundle.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "Hello, Olha!"
    assert body["handover"] is False

    messages = await _messages(services.sm, body["conversation_id"])
    assert [(m.role, m.text) for m in messages] == [
        (MessageRole.CLIENT, "hi"),
        (MessageRole.BOT, "Hello, Olha!"),
    ]
    client_msg, bot_msg = messages
    assert str(client_msg.turn_id) == body["turn_id"]
    assert client_msg.processed_at == clock.now()
    assert bot_msg.send_status == SendStatus.SENT
    assert bot_msg.sent_at == clock.now()
    assert str(bot_msg.id) == body["message_id"]

    (turn,) = await _turns(services.sm)
    assert str(turn.id) == body["turn_id"]
    assert turn.status == TurnStatus.DONE
    assert turn.input_text == "hi"
    assert turn.tokens_in == 1000
    assert turn.tokens_out == 100
    # (1000 × 0.10 + 100 × 0.50) / 1M
    assert turn.cost == Decimal("0.00015")
    assert [s["step"] for s in turn.trace["steps"]] == ["respond"]
    assert turn.finished_at == clock.now()

    async with services.sm() as session:
        (call,) = (await session.execute(select(LlmCall))).scalars().all()
        key = await session.get(ApiKey, bundle.api_key_id)
    assert call.turn_id == turn.id
    assert call.prompt_key == "simple_reply"
    assert key is not None and key.last_used_at == clock.now()

    request = fake_llm.requests[0]
    assert request["messages"][-1] == {"role": "user", "content": "hi"}
    assert request["messages"][0]["role"] == "system"
    assert "Acme" in request["messages"][0]["content"]


async def test_second_message_sees_history(client, services, fake_llm, clock):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    fake_llm.push(chat_reply("first answer"), chat_reply("second answer"))

    first = await client.post("/v1/messages", json=_msg("hi"), headers=bundle.auth)
    clock.advance(seconds=10)
    second = await client.post("/v1/messages", json=_msg("and prices?"), headers=bundle.auth)
    assert second.status_code == 200
    assert second.json()["reply"] == "second answer"
    assert first.json()["conversation_id"] == second.json()["conversation_id"]

    assert fake_llm.requests[1]["messages"][1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "and prices?"},
    ]


async def test_repeated_client_message_id_returns_the_same_reply(client, services, fake_llm):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    fake_llm.push(chat_reply("only once"))

    payload = _msg("hi", client_message_id="m-1")
    first = await client.post("/v1/messages", json=payload, headers=bundle.auth)
    again = await client.post("/v1/messages", json=payload, headers=bundle.auth)
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert fake_llm.calls == 1

    messages = await _messages(services.sm)
    assert [m.role for m in messages].count(MessageRole.CLIENT) == 1


async def test_failed_turn_is_answered_on_retry(client, services, fake_llm):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    fake_llm.push(*(LLMTransportError("timeout") for _ in range(3)))

    payload = _msg("hi", client_message_id="m-1")
    failed = await client.post("/v1/messages", json=payload, headers=bundle.auth)
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "llm.unavailable"

    fake_llm.push(chat_reply("now it works"))
    retried = await client.post("/v1/messages", json=payload, headers=bundle.auth)
    assert retried.status_code == 200, retried.text
    assert retried.json()["reply"] == "now it works"

    turns = await _turns(services.sm)
    assert sorted(t.status for t in turns) == [TurnStatus.DONE, TurnStatus.FAILED]
    failed_turn = next(t for t in turns if t.status == TurnStatus.FAILED)
    assert "llm.unavailable" in (failed_turn.error or "")


async def test_history_by_contact_with_limit(client, services, fake_llm, clock):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    fake_llm.push(chat_reply("a1"), chat_reply("a2"))
    await client.post("/v1/messages", json=_msg("q1"), headers=bundle.auth)
    clock.advance(seconds=5)
    await client.post("/v1/messages", json=_msg("q2"), headers=bundle.auth)

    resp = await client.post(
        "/v1/conversations/history",
        json={"contact": {"external_id": "c1"}, "limit": 2},
        headers=bundle.auth,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] is not None
    assert [(m["role"], m["text"]) for m in body["messages"]] == [
        ("client", "q2"),
        ("bot", "a2"),
    ]
    assert body["messages"][1]["send_status"] == "sent"

    full = await client.post(
        "/v1/conversations/history",
        json={"conversation_id": body["conversation_id"]},
        headers=bundle.auth,
    )
    assert [m["text"] for m in full.json()["messages"]] == ["q1", "a1", "q2", "a2"]


async def test_history_lookup_errors(client, services):
    bundle = await make_tenant(services.sm)

    unknown_contact = await client.post(
        "/v1/conversations/history",
        json={"contact": {"external_id": "nobody"}},
        headers=bundle.auth,
    )
    assert unknown_contact.status_code == 200
    assert unknown_contact.json() == {"conversation_id": None, "messages": []}

    unknown_conv = await client.post(
        "/v1/conversations/history",
        json={"conversation_id": str(uuid.uuid4())},
        headers=bundle.auth,
    )
    assert unknown_conv.status_code == 404
    assert unknown_conv.json()["error"]["code"] == "conversation.not_found"

    neither = await client.post("/v1/conversations/history", json={}, headers=bundle.auth)
    assert neither.status_code == 400
    assert neither.json()["error"]["code"] == "request.contact_or_conversation_required"


async def test_other_tenant_cannot_read_the_conversation(client, services, fake_llm):
    acme = await make_tenant(services.sm, slug="acme")
    other = await make_tenant(services.sm, slug="other", title="Other")
    await make_model(services.sm)
    fake_llm.push(chat_reply("hello"))
    sent = await client.post("/v1/messages", json=_msg("hi"), headers=acme.auth)
    conversation_id = sent.json()["conversation_id"]

    resp = await client.post(
        "/v1/conversations/history",
        json={"conversation_id": conversation_id},
        headers=other.auth,
    )
    assert resp.status_code == 404
    by_contact = await client.post(
        "/v1/conversations/history",
        json={"contact": {"external_id": "c1"}},
        headers=other.auth,
    )
    assert by_contact.json() == {"conversation_id": None, "messages": []}


async def test_history_hides_system_and_dropped(client, services, fake_llm, clock):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    fake_llm.push(chat_reply("visible"))
    sent = await client.post("/v1/messages", json=_msg("hi"), headers=bundle.auth)
    conversation_id = uuid.UUID(sent.json()["conversation_id"])

    clock.advance(seconds=1)
    async with services.sm() as session, session.begin():
        for role, status, text in [
            (MessageRole.SYSTEM, None, "internal note"),
            (MessageRole.BOT, SendStatus.DROPPED, "stale reply"),
        ]:
            session.add(
                Message(
                    tenant_id=bundle.tenant_id,
                    channel_id=bundle.channel_id,
                    conversation_id=conversation_id,
                    role=role,
                    text=text,
                    send_status=status,
                    created_at=clock.now(),
                )
            )

    resp = await client.post(
        "/v1/conversations/history",
        json={"conversation_id": str(conversation_id)},
        headers=bundle.auth,
    )
    assert [m["text"] for m in resp.json()["messages"]] == ["hi", "visible"]


async def test_empty_text_is_a_validation_error(client, services):
    bundle = await make_tenant(services.sm)
    resp = await client.post("/v1/messages", json=_msg(""), headers=bundle.auth)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "request.invalid"
    assert any(f["loc"][-1] == "text" for f in error["params"]["fields"])


async def test_different_contacts_get_different_conversations(client, services, fake_llm):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm)
    fake_llm.default = chat_reply("ok")
    a = await client.post("/v1/messages", json=_msg("hi", "c1"), headers=bundle.auth)
    b = await client.post("/v1/messages", json=_msg("hi", "c2"), headers=bundle.auth)
    assert a.json()["conversation_id"] != b.json()["conversation_id"]
    # the second contact's turn does not see the first one's messages
    assert fake_llm.requests[1]["messages"][1:] == [{"role": "user", "content": "hi"}]
