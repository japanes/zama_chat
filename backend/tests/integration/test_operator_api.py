"""Operator queue in the admin: choose a variant, edit it, dismiss, take over / give back,
write to the client, the 24-hour window, and rebuilt variants."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select, update

from app.core.ingest import save_client_message
from app.db.models import (
    ChannelKind,
    Conversation,
    Job,
    Message,
    MessageRole,
    OperatorTask,
    SendStatus,
    TaskKind,
    TaskStatus,
    UserRole,
)
from app.operator.tasks import upsert_choose_reply
from tests.admin_helpers import admin_headers
from tests.factories import make_channel, make_tenant

pytestmark = pytest.mark.db

VARIANTS = ["Так, є вікно о 15:00.", "Є, о 15:00 — записати?", "Так, о 15:00 вільно."]


async def _setup(services, *, kind: str = ChannelKind.HTTP) -> dict[str, Any]:
    bundle = await make_tenant(services.sm)
    channel_id = bundle.channel_id
    if kind == ChannelKind.INSTAGRAM:
        channel_id = await make_channel(
            services.sm, bundle.tenant_id, kind=kind, account_id="17841400000000001"
        )
    async with services.sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=bundle.tenant_id,
            channel_id=channel_id,
            contact_external_id="c1",
            text="Є вільне вікно в п'ятницю?",
            now=services.clock.now(),
        )
    headers = await admin_headers(
        services, "op@example.com", tenant_id=bundle.tenant_id, role=UserRole.OPERATOR
    )
    base = f"/v1/admin/tenants/{bundle.tenant_id}"
    return {
        "tenant_id": bundle.tenant_id,
        "conv_id": ingested.conversation_id,
        "headers": headers,
        "base": base,
        "conv_path": f"{base}/conversations/{ingested.conversation_id}",
    }


async def _choose_reply(services, conv_id: uuid.UUID, variants=VARIANTS, text="Є вікно?"):
    async with services.sm() as session, session.begin():
        conv = await session.get(Conversation, conv_id, with_for_update=True)
        task, _ = await upsert_choose_reply(
            session,
            conv,
            turn_id=None,
            variants=list(variants),
            input_text=text,
            meta={"reply_language": "uk"},
            now=services.clock.now(),
        )
        return task.id


async def _task(services, task_id) -> OperatorTask:
    async with services.sm() as session:
        task = await session.get(OperatorTask, task_id)
        assert task is not None
        return task


async def _conv(services, conv_id) -> Conversation:
    async with services.sm() as session:
        conv = await session.get(Conversation, conv_id)
        assert conv is not None
        return conv


async def _operator_messages(services, conv_id) -> list[Message]:
    async with services.sm() as session:
        rows = await session.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.role == MessageRole.OPERATOR)
            .order_by(Message.created_at)
        )
        return list(rows.scalars())


async def _resolve(client, s, task_id, **body):
    return await client.post(
        f"{s['base']}/operator/tasks/{task_id}/resolve", json=body, headers=s["headers"]
    )


# --- the queue --------------------------------------------------------------------------


async def test_queue_lists_open_tasks_with_the_client(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    res = await client.get(f"{s['base']}/operator/tasks", headers=s["headers"])
    assert res.status_code == 200
    data = res.json()
    assert data["open"] == {"choose_reply": 1}
    (item,) = data["items"]
    assert item["id"] == str(task_id)
    assert item["payload"]["variants"] == VARIANTS
    assert item["contact"]["external_id"] == "c1"
    assert item["last_client_text"] == "Є вільне вікно в п'ятницю?"


async def test_another_tenant_sees_nothing(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    other = await make_tenant(services.sm, slug="other")
    headers = await admin_headers(
        services, "x@example.com", tenant_id=other.tenant_id, role=UserRole.OPERATOR
    )
    base = f"/v1/admin/tenants/{other.tenant_id}"
    assert (await client.get(f"{base}/operator/tasks", headers=headers)).json()["items"] == []
    res = await client.post(
        f"{base}/operator/tasks/{task_id}/resolve",
        json={"action": "dismiss", "version": 1},
        headers=headers,
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "operator_task.not_found"


# --- choose a reply ---------------------------------------------------------------------


async def test_send_a_variant_as_is(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    res = await _resolve(client, s, task_id, action="send", variant=1, version=1)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["task"]["status"] == TaskStatus.DONE
    assert out["message"]["text"] == VARIANTS[1]
    assert out["message"]["meta"]["variant"] == 1
    assert out["message"]["meta"]["edited"] is False

    (msg,) = await _operator_messages(services, s["conv_id"])
    # the API channel is synchronous: the caller reads it from the history
    assert msg.send_status == SendStatus.SENT
    task = await _task(services, task_id)
    assert task.result["message_id"] == str(msg.id)
    assert task.resolved_by is not None


async def test_send_an_edited_variant(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    res = await _resolve(
        client, s, task_id, action="send", variant=0, text="Так, о 15:00. Записати вас?", version=1
    )
    assert res.status_code == 200
    assert res.json()["message"]["text"] == "Так, о 15:00. Записати вас?"
    assert res.json()["message"]["meta"]["edited"] is True


async def test_send_own_text_without_a_variant(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    res = await _resolve(client, s, task_id, action="send", text="  Свій текст  ", version=1)
    assert res.json()["message"]["text"] == "Свій текст"
    assert res.json()["message"]["meta"]["edited"] is True


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"action": "send", "variant": 7, "version": 1}, "operator_task.variant_invalid"),
        ({"action": "send", "text": "   ", "version": 1}, "operator_task.text_required"),
        ({"action": "release", "version": 1}, "operator_task.action_invalid"),
    ],
)
async def test_bad_resolve_requests(services, client, body, code):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    res = await _resolve(client, s, task_id, **body)
    assert res.json()["error"]["code"] == code
    assert (await _task(services, task_id)).status == TaskStatus.OPEN
    assert await _operator_messages(services, s["conv_id"]) == []


async def test_rebuilt_variants_make_the_old_choice_outdated(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"], text="Є вікно?")
    # the client wrote again: the same task, new variants, version 2
    same_id = await _choose_reply(services, s["conv_id"], variants=["Новий"], text="А в суботу?")
    assert same_id == task_id
    task = await _task(services, task_id)
    assert task.version == 2
    assert task.payload["input_text"] == "Є вікно?\nА в суботу?"

    res = await _resolve(client, s, task_id, action="send", variant=0, version=1)
    assert res.status_code == 409
    assert res.json()["error"] == {"code": "operator_task.outdated", "params": {"version": 2}}
    assert await _operator_messages(services, s["conv_id"]) == []

    res = await _resolve(client, s, task_id, action="send", variant=0, version=2)
    assert res.json()["message"]["text"] == "Новий"


async def test_closed_task_cannot_be_resolved_again(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    assert (await _resolve(client, s, task_id, action="dismiss", version=1)).status_code == 200
    assert (await _task(services, task_id)).status == TaskStatus.CANCELLED
    res = await _resolve(client, s, task_id, action="send", variant=0, version=1)
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "operator_task.closed"
    assert await _operator_messages(services, s["conv_id"]) == []


# --- write directly, take over, give back -----------------------------------------------


async def test_operator_message_closes_the_choose_reply_task(services, client):
    s = await _setup(services)
    task_id = await _choose_reply(services, s["conv_id"])
    res = await client.post(
        f"{s['conv_path']}/messages", json={"text": "Добрий день!"}, headers=s["headers"]
    )
    assert res.status_code == 200, res.text
    (msg,) = await _operator_messages(services, s["conv_id"])
    assert msg.text == "Добрий день!"
    task = await _task(services, task_id)
    assert task.status == TaskStatus.DONE
    assert task.result["action"] == "answered_manually"


async def test_take_over_and_give_back(services, client):
    s = await _setup(services)
    res = await client.post(f"{s['conv_path']}/handover", headers=s["headers"])
    assert res.status_code == 200
    conv = await _conv(services, s["conv_id"])
    assert conv.handover is True
    assert conv.handover_at == services.clock.now()
    tasks = (await client.get(f"{s['base']}/operator/tasks", headers=s["headers"])).json()
    assert tasks["open"] == {"handover": 1}

    res = await client.post(f"{s['conv_path']}/release", headers=s["headers"])
    assert res.status_code == 200
    conv = await _conv(services, s["conv_id"])
    assert conv.handover is False
    tasks = (await client.get(f"{s['base']}/operator/tasks", headers=s["headers"])).json()
    assert tasks["open"] == {}


async def test_release_through_the_handover_task(services, client):
    s = await _setup(services)
    await client.post(f"{s['conv_path']}/handover", headers=s["headers"])
    (item,) = (await client.get(f"{s['base']}/operator/tasks", headers=s["headers"])).json()[
        "items"
    ]
    res = await _resolve(client, s, item["id"], action="release", version=item["version"])
    assert res.status_code == 200
    assert (await _conv(services, s["conv_id"])).handover is False


async def test_operator_mode_off_cancels_waiting_variants(services, client):
    s = await _setup(services)
    res = await client.patch(s["conv_path"], json={"operator_mode": True}, headers=s["headers"])
    assert res.status_code == 200
    task_id = await _choose_reply(services, s["conv_id"])
    res = await client.patch(s["conv_path"], json={"operator_mode": False}, headers=s["headers"])
    assert res.status_code == 200
    assert (await _conv(services, s["conv_id"])).operator_mode is False
    task = await _task(services, task_id)
    assert task.status == TaskStatus.CANCELLED
    assert task.result["action"] == "operator_mode_off"


async def test_sandbox_conversation_gets_no_operator_messages(services, client):
    s = await _setup(services)
    async with services.sm() as session, session.begin():
        await session.execute(
            update(Conversation).where(Conversation.id == s["conv_id"]).values(sandbox=True)
        )
    res = await client.post(f"{s['conv_path']}/messages", json={"text": "x"}, headers=s["headers"])
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "conversation.sandbox"


# --- Instagram: the outbox and the window -----------------------------------------------


async def test_instagram_operator_message_goes_through_the_outbox(services, client):
    s = await _setup(services, kind=ChannelKind.INSTAGRAM)
    res = await client.post(
        f"{s['conv_path']}/messages", json={"text": "Вітаю"}, headers=s["headers"]
    )
    assert res.status_code == 200
    (msg,) = await _operator_messages(services, s["conv_id"])
    assert msg.send_status == SendStatus.PENDING
    async with services.sm() as session:
        (job,) = (await session.execute(select(Job).where(Job.kind == "send_message"))).scalars()
    assert job.payload == {"message_id": str(msg.id)}

    detail = (await client.get(s["conv_path"], headers=s["headers"])).json()
    assert detail["operator_can_write"] is True


async def test_instagram_window_closed_after_24_hours(services, client, clock):
    s = await _setup(services, kind=ChannelKind.INSTAGRAM)
    task_id = await _choose_reply(services, s["conv_id"])
    clock.advance(hours=25)

    detail = (await client.get(s["conv_path"], headers=s["headers"])).json()
    assert detail["operator_can_write"] is False

    res = await client.post(f"{s['conv_path']}/messages", json={"text": "x"}, headers=s["headers"])
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "conversation.window_closed"
    res = await _resolve(client, s, task_id, action="send", variant=0, version=1)
    assert res.json()["error"]["code"] == "conversation.window_closed"
    # nothing written, the task waits (the operator may dismiss it)
    assert await _operator_messages(services, s["conv_id"]) == []
    assert (await _task(services, task_id)).status == TaskStatus.OPEN


async def test_operator_role_is_enough(services, client):
    s = await _setup(services)
    # an operator may act; a user without a membership does not even see the tenant
    stranger = await admin_headers(services, "nobody@example.com")
    res = await client.get(f"{s['base']}/operator/tasks", headers=stranger)
    assert res.status_code == 404
    assert (
        await client.get(f"{s['base']}/operator/tasks", headers=s["headers"])
    ).status_code == 200


async def test_choose_reply_tasks_are_found_by_kind(services, client):
    s = await _setup(services)
    await _choose_reply(services, s["conv_id"])
    await client.post(f"{s['conv_path']}/handover", headers=s["headers"])
    res = await client.get(
        f"{s['base']}/operator/tasks", params={"kind": TaskKind.HANDOVER}, headers=s["headers"]
    )
    assert [i["kind"] for i in res.json()["items"]] == [TaskKind.HANDOVER]
    assert res.json()["open"] == {"choose_reply": 1, "handover": 1}
