"""Echoes of outgoing Instagram messages (ours / typed in the Instagram app) and reply
variants of operator mode inside a turn: task created, rebuilt, dropped."""

import pytest
from sqlalchemy import select

from app.core.context import TurnPlan
from app.core.ingest import save_client_message
from app.core.turn import run_turn
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
)
from app.jobs.queue import enqueue
from app.jobs.tasks import HANDLERS
from app.jobs.worker import Worker
from app.operator.tasks import upsert_choose_reply
from tests.factories import make_channel, make_tenant
from tests.integration.test_turn import FakeAdapter, Scripted

pytestmark = pytest.mark.db

ACCOUNT = "17841400000000001"


async def _setup(services, *, settings=None):
    bundle = await make_tenant(services.sm)
    channel_id = await make_channel(
        services.sm,
        bundle.tenant_id,
        kind=ChannelKind.INSTAGRAM,
        account_id=ACCOUNT,
        settings=settings,
    )
    adapter = FakeAdapter()
    services.channels[ChannelKind.INSTAGRAM] = adapter
    return bundle.tenant_id, channel_id, adapter


async def _say(services, tenant_id, channel_id, text, *, mid=None):
    async with services.sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=tenant_id,
            channel_id=channel_id,
            contact_external_id="c1",
            text=text,
            external_id=mid,
            now=services.clock.now(),
        )
    return ingested.conversation_id


async def _echo(services, tenant_id, channel_id, mid, text="typed in the app"):
    async with services.sm() as session, session.begin():
        await enqueue(
            session,
            "ig_echo",
            {
                "channel_id": str(channel_id),
                "client_id": "c1",
                "mid": mid,
                "text": text,
                "attachments": [],
                "at": services.clock.now().isoformat(),
            },
            run_at=services.clock.now(),
            tenant_id=tenant_id,
            dedupe_key=f"igecho:{mid}",
            max_attempts=6,
        )


def _worker(services) -> Worker:
    return Worker(services.sm, services.clock, HANDLERS, services=services)


async def _rows(services, model, *where):
    async with services.sm() as session:
        query = select(model).where(*where).order_by(model.created_at)
        return list((await session.execute(query)).scalars())


# --- echoes -----------------------------------------------------------------------------


async def test_echo_of_our_own_reply_is_ignored(services):
    tenant_id, channel_id, _ = await _setup(services)
    conv_id = await _say(services, tenant_id, channel_id, "hi", mid="c-1")
    outcome = await run_turn(
        services, conv_id, owner="t", pipeline=Scripted(TurnPlan(reply_text="hello"))
    )
    assert outcome is not None
    await _worker(services).run_due()  # send_message: the fake adapter returns mid-1
    (reply,) = await _rows(services, Message, Message.role == MessageRole.BOT)
    assert reply.external_id == "mid-1"

    await _echo(services, tenant_id, channel_id, "mid-1")
    await _worker(services).run_due()
    assert await _rows(services, Message, Message.role == MessageRole.OPERATOR) == []


async def test_echo_of_a_later_part_of_a_long_reply_is_ignored(services):
    tenant_id, channel_id, _ = await _setup(services)
    conv_id = await _say(services, tenant_id, channel_id, "hi")
    async with services.sm() as session, session.begin():
        session.add(
            Message(
                tenant_id=tenant_id,
                channel_id=channel_id,
                conversation_id=conv_id,
                role=MessageRole.BOT,
                text="long reply",
                external_id="part-1",
                send_status=SendStatus.SENT,
                meta={"send": {"mids": ["part-1", "part-2"]}},
                created_at=services.clock.now(),
            )
        )
    await _echo(services, tenant_id, channel_id, "part-2")
    await _worker(services).run_due()
    assert await _rows(services, Message, Message.role == MessageRole.OPERATOR) == []


async def test_reply_typed_in_the_instagram_app_is_saved_and_closes_variants(services):
    tenant_id, channel_id, _ = await _setup(services)
    conv_id = await _say(services, tenant_id, channel_id, "Є вікно?")
    async with services.sm() as session, session.begin():
        conv = await session.get(Conversation, conv_id, with_for_update=True)
        task, _ = await upsert_choose_reply(
            session,
            conv,
            turn_id=None,
            variants=["A", "B"],
            input_text="Є вікно?",
            meta={},
            now=services.clock.now(),
        )

    await _echo(services, tenant_id, channel_id, "app-mid-1", text="Так, о 15:00")
    await _worker(services).run_due()

    (msg,) = await _rows(services, Message, Message.role == MessageRole.OPERATOR)
    assert msg.text == "Так, о 15:00"
    assert msg.external_id == "app-mid-1"
    assert msg.meta == {"source": "instagram_app"}
    assert msg.send_status == SendStatus.SENT
    (closed,) = await _rows(services, OperatorTask, OperatorTask.id == task.id)
    assert closed.status == TaskStatus.DONE
    assert closed.result["action"] == "answered_outside"

    # Meta may send the same echo twice
    await _echo(services, tenant_id, channel_id, "app-mid-1", text="Так, о 15:00")
    await _worker(services).run_due()
    assert len(await _rows(services, Message, Message.role == MessageRole.OPERATOR)) == 1


async def test_echo_waits_while_our_reply_is_being_sent(services, clock):
    tenant_id, channel_id, _ = await _setup(services)
    conv_id = await _say(services, tenant_id, channel_id, "hi")
    async with services.sm() as session, session.begin():
        session.add(
            Message(
                tenant_id=tenant_id,
                channel_id=channel_id,
                conversation_id=conv_id,
                role=MessageRole.BOT,
                text="on its way",
                send_status=SendStatus.PENDING,
                created_at=clock.now(),
            )
        )
    await _echo(services, tenant_id, channel_id, "unknown-yet")
    await _worker(services).run_due()
    # not saved as an operator message: it may be our reply whose mid is not saved yet
    assert await _rows(services, Message, Message.role == MessageRole.OPERATOR) == []
    (job,) = await _rows(services, Job, Job.kind == "ig_echo")
    assert job.run_at > clock.now()


# --- operator mode inside a turn ---------------------------------------------------------


async def test_variants_go_to_an_operator_task_not_to_the_client(services):
    tenant_id, channel_id, adapter = await _setup(services, settings={"operator_mode": True})
    conv_id = await _say(services, tenant_id, channel_id, "Є вікно в п'ятницю?")
    plan = TurnPlan(variants=["Так, о 15:00.", "Є, о 15:00 — записати?"])
    outcome = await run_turn(services, conv_id, owner="t", pipeline=Scripted(plan))
    assert outcome is not None
    assert outcome.waiting_operator is True
    assert outcome.reply_message_id is None

    (task,) = await _rows(services, OperatorTask, OperatorTask.conversation_id == conv_id)
    assert task.kind == TaskKind.CHOOSE_REPLY
    assert task.status == TaskStatus.OPEN
    assert task.version == 1
    assert task.payload["variants"] == plan.variants
    assert task.payload["input_text"] == "Є вікно в п'ятницю?"
    assert task.turn_id == outcome.turn_id
    # nothing goes to the client by itself
    assert await _rows(services, Message, Message.role == MessageRole.BOT) == []
    assert await _rows(services, Job, Job.kind == "send_message") == []
    assert adapter.sent == []


async def test_new_client_message_rebuilds_the_same_task(services, clock):
    tenant_id, channel_id, _ = await _setup(services, settings={"operator_mode": True})
    conv_id = await _say(services, tenant_id, channel_id, "Є вікно в п'ятницю?")
    await run_turn(services, conv_id, owner="t", pipeline=Scripted(TurnPlan(variants=["A"])))
    clock.advance(seconds=30)
    await _say(services, tenant_id, channel_id, "А в суботу?")
    await run_turn(services, conv_id, owner="t", pipeline=Scripted(TurnPlan(variants=["B", "C"])))

    (task,) = await _rows(services, OperatorTask, OperatorTask.conversation_id == conv_id)
    assert task.version == 2
    assert task.payload["variants"] == ["B", "C"]
    assert task.payload["input_text"] == "Є вікно в п'ятницю?\nА в суботу?"


async def test_variants_are_dropped_when_an_operator_already_answered(services, clock):
    tenant_id, channel_id, _ = await _setup(services, settings={"operator_mode": True})
    conv_id = await _say(services, tenant_id, channel_id, "Є вікно?")

    async def operator_writes(services_, ctx):
        # while the bot thinks, a person answers in the admin
        clock.advance(seconds=1)
        async with services.sm() as session, session.begin():
            session.add(
                Message(
                    tenant_id=tenant_id,
                    channel_id=channel_id,
                    conversation_id=conv_id,
                    role=MessageRole.OPERATOR,
                    text="Так, є",
                    send_status=SendStatus.SENT,
                    created_at=clock.now(),
                )
            )

    pipeline = Scripted(TurnPlan(variants=["A", "B"]), before=operator_writes)
    outcome = await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    assert outcome is not None
    assert outcome.waiting_operator is False
    assert await _rows(services, OperatorTask, OperatorTask.conversation_id == conv_id) == []


async def test_sandbox_turn_never_creates_tasks(services):
    tenant_id, channel_id, _ = await _setup(services, settings={"operator_mode": True})
    conv_id = await _say(services, tenant_id, channel_id, "hi")
    outcome = await run_turn(
        services,
        conv_id,
        owner="sandbox",
        sandbox=True,
        pipeline=Scripted(TurnPlan(variants=["A", "B"])),
    )
    assert outcome is not None
    assert await _rows(services, OperatorTask) == []
