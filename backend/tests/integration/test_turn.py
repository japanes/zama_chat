"""run_turn on a real PostgreSQL with hand-made pipelines: take → think → write,
supersede on new messages, failures, the outbox and the send_message job."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, update

from app.channels.base import PermanentSendError, SendResult
from app.core import lease
from app.core import turn as turn_module
from app.core.context import HistoryItem, TurnContext, TurnPlan
from app.core.ingest import save_client_message
from app.core.turn import run_turn
from app.db.models import (
    ChannelKind,
    Conversation,
    Job,
    JobStatus,
    Message,
    MessageRole,
    SendStatus,
    Turn,
    TurnStatus,
)
from app.errors import AppError, Conflict
from app.jobs.queue import enqueue
from app.jobs.tasks import HANDLERS, run_turn_job
from app.jobs.worker import JobContext, Retry, Worker
from tests.factories import make_channel, make_tenant

pytestmark = pytest.mark.db


class FakeAdapter:
    """Asynchronous channel (like Instagram): replies leave through send_message jobs."""

    synchronous = False

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def can_send(self, conversation, role, now) -> bool:
        return True

    async def send(self, channel, contact, message, conversation) -> SendResult:
        if self.error is not None:
            raise self.error
        self.sent.append((contact.external_id, message.text))
        return SendResult(
            external_id=f"mid-{len(self.sent)}", meta={"recipient": contact.external_id}
        )


class Scripted:
    """Pipeline that records every context and answers with the next plan (the last one
    repeats). An exception in the list is raised instead. `before` runs first — it plays
    the client who writes while the bot is thinking."""

    def __init__(
        self,
        *plans: TurnPlan | Exception,
        before: Callable[[Any, TurnContext], Awaitable[None]] | None = None,
    ) -> None:
        self.plans = list(plans)
        self.before = before
        self.contexts: list[TurnContext] = []

    async def __call__(self, services, ctx: TurnContext) -> TurnPlan:
        self.contexts.append(ctx)
        if self.before is not None:
            await self.before(services, ctx)
        plan = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        if isinstance(plan, Exception):
            raise plan
        return plan


@dataclass
class Setup:
    tenant_id: uuid.UUID
    channel_id: uuid.UUID  # instagram
    http_channel_id: uuid.UUID
    adapter: FakeAdapter


async def _setup(services) -> Setup:
    bundle = await make_tenant(services.sm)
    channel_id = await make_channel(
        services.sm, bundle.tenant_id, kind=ChannelKind.INSTAGRAM, account_id="ig-1"
    )
    adapter = FakeAdapter()
    services.channels[ChannelKind.INSTAGRAM] = adapter
    return Setup(bundle.tenant_id, channel_id, bundle.channel_id, adapter)


async def _say(
    services, setup: Setup, text: str, *, contact: str = "u1", channel_id=None
) -> tuple[uuid.UUID, uuid.UUID]:
    """Save a client message → (conversation_id, message_id)."""
    async with services.sm() as session, session.begin():
        ingested = await save_client_message(
            session,
            tenant_id=setup.tenant_id,
            channel_id=channel_id or setup.channel_id,
            contact_external_id=contact,
            contact_name="Olha",
            text=text,
            now=services.clock.now(),
        )
    return ingested.conversation_id, ingested.message_id


async def _messages(services, conversation_id) -> list[Message]:
    async with services.sm() as session:
        query = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at, Message.id)
        )
        return list((await session.execute(query)).scalars())


async def _turns(services) -> list[Turn]:
    async with services.sm() as session:
        query = select(Turn).order_by(Turn.created_at, Turn.id)
        return list((await session.execute(query)).scalars())


async def _jobs(services, kind: str | None = None) -> list[Job]:
    async with services.sm() as session:
        query = select(Job).order_by(Job.created_at, Job.id)
        if kind is not None:
            query = query.where(Job.kind == kind)
        return list((await session.execute(query)).scalars())


async def _conv(services, conversation_id) -> Conversation:
    async with services.sm() as session:
        conv = await session.get(Conversation, conversation_id)
        assert conv is not None
        return conv


async def _message(services, message_id) -> Message:
    async with services.sm() as session:
        message = await session.get(Message, message_id)
        assert message is not None
        return message


def _worker(services, **kw) -> Worker:
    return Worker(services.sm, services.clock, HANDLERS, services=services, **kw)


# --- the outbox -------------------------------------------------------------------------


async def test_async_channel_reply_goes_to_outbox(services, clock):
    setup = await _setup(services)
    conv_id, msg_id = await _say(services, setup, "hi")
    pipeline = Scripted(TurnPlan(reply_text="hello"))

    outcome = await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    assert outcome is not None
    assert outcome.status == TurnStatus.DONE
    assert outcome.reply_text == "hello"

    reply = await _message(services, outcome.reply_message_id)
    assert reply.role == MessageRole.BOT
    assert reply.send_status == SendStatus.PENDING
    assert reply.sent_at is None
    assert reply.turn_id == outcome.turn_id

    (job,) = await _jobs(services)
    assert job.kind == "send_message"
    assert job.payload == {"message_id": str(reply.id)}
    assert job.dedupe_key == f"send:{reply.id}"
    assert job.tenant_id == setup.tenant_id
    assert job.status == JobStatus.QUEUED
    assert job.run_at == clock.now()

    client_msg = await _message(services, msg_id)
    assert client_msg.turn_id == outcome.turn_id
    assert client_msg.processed_at == clock.now()

    conv = await _conv(services, conv_id)
    assert conv.last_bot_at == clock.now()
    assert conv.state_version == 1
    assert conv.lease_owner is None
    assert conv.lease_until is None


async def test_send_message_job_sends_and_keeps_channel_id(services, clock):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    outcome = await run_turn(
        services, conv_id, owner="t", pipeline=Scripted(TurnPlan(reply_text="hello"))
    )
    assert outcome is not None

    clock.advance(seconds=1)
    assert await _worker(services).run_due() == 1
    assert setup.adapter.sent == [("u1", "hello")]

    reply = await _message(services, outcome.reply_message_id)
    assert reply.send_status == SendStatus.SENT
    assert reply.sent_at == clock.now()
    assert reply.external_id == "mid-1"
    assert reply.meta["send"] == {"recipient": "u1"}
    assert [j.status for j in await _jobs(services)] == [JobStatus.DONE]


async def test_sent_message_is_not_sent_twice(services):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    outcome = await run_turn(
        services, conv_id, owner="t", pipeline=Scripted(TurnPlan(reply_text="hello"))
    )
    assert outcome is not None
    worker = _worker(services)
    await worker.run_due()
    # a second job for the same message (e.g. a retry that raced the first one)
    async with services.sm() as session, session.begin():
        await enqueue(
            session,
            "send_message",
            {"message_id": str(outcome.reply_message_id)},
            run_at=services.clock.now(),
            dedupe_key=f"send:{outcome.reply_message_id}",
        )
    await worker.run_due()
    assert len(setup.adapter.sent) == 1


async def test_permanent_send_error_marks_message_failed(services):
    setup = await _setup(services)
    setup.adapter.error = PermanentSendError("window closed", "instagram.window_closed")
    conv_id, _ = await _say(services, setup, "hi")
    outcome = await run_turn(
        services, conv_id, owner="t", pipeline=Scripted(TurnPlan(reply_text="hello"))
    )
    assert outcome is not None
    await _worker(services).run_due()

    reply = await _message(services, outcome.reply_message_id)
    assert reply.send_status == SendStatus.FAILED
    # a code the admin translates; the text stays in meta
    assert reply.send_error == "instagram.window_closed"
    assert reply.meta["send_error_detail"] == "window closed"
    # no point retrying: the job itself is done
    assert [j.status for j in await _jobs(services)] == [JobStatus.DONE]


async def test_send_error_is_retried_and_fails_on_last_attempt(services, clock):
    setup = await _setup(services)
    setup.adapter.error = RuntimeError("network down")
    conv_id, _ = await _say(services, setup, "hi")
    outcome = await run_turn(
        services, conv_id, owner="t", pipeline=Scripted(TurnPlan(reply_text="hello"))
    )
    assert outcome is not None
    async with services.sm() as session, session.begin():
        await session.execute(update(Job).values(max_attempts=2))

    worker = _worker(services, backoff_s=(1,))
    await worker.run_due()
    reply = await _message(services, outcome.reply_message_id)
    assert reply.send_status == SendStatus.PENDING
    (job,) = await _jobs(services)
    assert job.status == JobStatus.QUEUED
    assert job.attempts == 1

    clock.advance(seconds=1)
    await worker.run_due()
    reply = await _message(services, outcome.reply_message_id)
    assert reply.send_status == SendStatus.FAILED
    assert reply.send_error == "send.failed"
    assert reply.meta["send_error_detail"] == "RuntimeError: network down"
    (job,) = await _jobs(services)
    assert job.status == JobStatus.FAILED


async def test_sandbox_reply_is_sent_without_job(services, clock):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    outcome = await run_turn(
        services,
        conv_id,
        owner="sandbox",
        sandbox=True,
        pipeline=Scripted(TurnPlan(reply_text="hello")),
    )
    assert outcome is not None
    reply = await _message(services, outcome.reply_message_id)
    assert reply.send_status == SendStatus.SENT
    assert reply.sent_at == clock.now()
    assert await _jobs(services) == []
    (turn,) = await _turns(services)
    assert turn.sandbox is True


async def test_empty_reply_writes_no_message(services):
    setup = await _setup(services)
    conv_id, msg_id = await _say(services, setup, "hi")
    outcome = await run_turn(
        services, conv_id, owner="t", pipeline=Scripted(TurnPlan(reply_text=""))
    )
    assert outcome is not None
    assert outcome.status == TurnStatus.DONE
    assert outcome.reply_message_id is None
    assert [m.role for m in await _messages(services, conv_id)] == [MessageRole.CLIENT]
    assert await _jobs(services) == []
    assert (await _message(services, msg_id)).processed_at is not None


# --- supersede ---------------------------------------------------------------------------


async def test_new_message_while_thinking_supersedes_the_reply(services, clock):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "first")

    async def client_writes_once(services, ctx):
        if len(pipeline.contexts) == 1:
            clock.advance(seconds=1)
            await _say(services, setup, "second")

    pipeline = Scripted(
        TurnPlan(reply_text="answer to first", reply_meta={"v": 1}),
        TurnPlan(reply_text="answer to both"),
        before=client_writes_once,
    )
    outcome = await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    assert outcome is not None
    assert outcome.status == TurnStatus.DONE
    assert outcome.reply_text == "answer to both"

    assert [c.input_text for c in pipeline.contexts] == ["first", "first\nsecond"]
    # the dropped reply was never seen by the client: not in the second pass history
    assert pipeline.contexts[1].history == []

    superseded, done = await _turns(services)
    assert superseded.status == TurnStatus.SUPERSEDED
    assert done.status == TurnStatus.DONE
    assert done.id == outcome.turn_id
    assert done.input_text == "first\nsecond"

    messages = await _messages(services, conv_id)
    assert [(m.role, m.text, m.send_status) for m in messages] == [
        (MessageRole.CLIENT, "first", None),
        (MessageRole.CLIENT, "second", None),
        (MessageRole.BOT, "answer to first", SendStatus.DROPPED),
        (MessageRole.BOT, "answer to both", SendStatus.PENDING),
    ]
    dropped = messages[2]
    assert dropped.meta == {"v": 1, "dropped": "new_client_messages"}
    assert dropped.turn_id == superseded.id
    assert all(m.turn_id == done.id for m in messages[:2])
    assert all(m.processed_at == clock.now() for m in messages[:2])

    # only the kept reply is sent
    jobs = await _jobs(services, "send_message")
    assert [j.payload["message_id"] for j in jobs] == [str(outcome.reply_message_id)]

    # the next turn sees both client messages and the kept reply, not the dropped one
    clock.advance(seconds=1)
    await _say(services, setup, "third")
    later = Scripted(TurnPlan(reply_text="ok"))
    await run_turn(services, conv_id, owner="t", pipeline=later)
    assert [h.text for h in later.contexts[0].history] == ["first", "second", "answer to both"]


async def test_restart_limit_keeps_the_reply(services, clock):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "m0")

    async def client_never_stops(services, ctx):
        clock.advance(seconds=1)
        await _say(services, setup, f"m{len(pipeline.contexts)}")

    pipeline = Scripted(TurnPlan(reply_text="reply"), before=client_never_stops)
    outcome = await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    assert outcome is not None
    assert outcome.status == TurnStatus.DONE

    limit = services.settings.turn_restart_limit
    assert len(pipeline.contexts) == limit + 1
    statuses = [t.status for t in await _turns(services)]
    assert statuses == [TurnStatus.SUPERSEDED] * limit + [TurnStatus.DONE]

    # the message written during the last pass waits for the next turn
    unprocessed = [
        m
        for m in await _messages(services, conv_id)
        if m.role == MessageRole.CLIENT and m.turn_id is None
    ]
    assert [m.text for m in unprocessed] == [f"m{limit + 1}"]
    assert (await _conv(services, conv_id)).lease_owner is None


async def test_http_turn_takes_only_its_own_message(services, clock):
    setup = await _setup(services)
    conv_id, first_id = await _say(services, setup, "a", channel_id=setup.http_channel_id)
    _, second_id = await _say(services, setup, "b", channel_id=setup.http_channel_id)

    pipeline = Scripted(TurnPlan(reply_text="answer a"), TurnPlan(reply_text="answer b"))
    first = await run_turn(
        services, conv_id, owner="http:1", only_message_id=first_id, pipeline=pipeline
    )
    assert first is not None
    assert first.status == TurnStatus.DONE
    assert pipeline.contexts[0].input_text == "a"
    assert pipeline.contexts[0].pending_ids == [first_id]
    # the other request's message is neither taken nor shown as history
    assert pipeline.contexts[0].history == []
    assert (await _message(services, second_id)).turn_id is None
    # the HTTP channel answers in the response: sent at once, no job
    assert (await _message(services, first.reply_message_id)).send_status == SendStatus.SENT
    assert await _jobs(services) == []

    second = await run_turn(
        services, conv_id, owner="http:2", only_message_id=second_id, pipeline=pipeline
    )
    assert second is not None
    assert second.reply_text == "answer b"
    assert [(h.role, h.text) for h in pipeline.contexts[1].history] == [
        (MessageRole.CLIENT, "a"),
        (MessageRole.BOT, "answer a"),
    ]


# --- nothing to do, failures, busy ------------------------------------------------------


async def test_nothing_to_answer_returns_none(services):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    pipeline = Scripted(TurnPlan(reply_text="hello"))
    assert await run_turn(services, conv_id, owner="t", pipeline=pipeline) is not None

    assert await run_turn(services, conv_id, owner="t", pipeline=pipeline) is None
    assert len(pipeline.contexts) == 1
    assert len(await _turns(services)) == 1
    assert (await _conv(services, conv_id)).lease_owner is None


async def test_unknown_conversation(services):
    with pytest.raises(AppError) as err:
        await run_turn(
            services, uuid.uuid4(), owner="t", pipeline=Scripted(TurnPlan(reply_text="x"))
        )
    # the lease UPDATE finds no row → busy is reported before the turn starts
    assert err.value.code in {"conversation.busy", "conversation.not_found"}


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (AppError("llm.unavailable", {"role": "reply"}, status=503), "llm.unavailable"),
        (RuntimeError("boom"), "RuntimeError"),
    ],
)
async def test_pipeline_error_fails_the_turn_and_gives_messages_back(
    services, clock, exc, expected
):
    setup = await _setup(services)
    conv_id, msg_id = await _say(services, setup, "hi")

    with pytest.raises(type(exc)):
        await run_turn(services, conv_id, owner="t", pipeline=Scripted(exc))

    (turn,) = await _turns(services)
    assert turn.status == TurnStatus.FAILED
    assert expected in (turn.error or "")
    assert turn.finished_at == clock.now()
    assert turn.input_text == "hi"

    message = await _message(services, msg_id)
    assert message.turn_id is None
    assert message.processed_at is None
    conv = await _conv(services, conv_id)
    assert conv.lease_owner is None
    assert conv.state_version == 0

    # the retry takes the same message again
    retry = Scripted(TurnPlan(reply_text="now it works"))
    outcome = await run_turn(services, conv_id, owner="t", pipeline=retry)
    assert outcome is not None
    assert outcome.status == TurnStatus.DONE
    assert retry.contexts[0].input_text == "hi"


async def test_busy_conversation_is_rejected(services):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    await lease.acquire(services.sm, conv_id, "other", services.clock, seconds=90)

    pipeline = Scripted(TurnPlan(reply_text="hello"))
    with pytest.raises(Conflict) as err:
        await run_turn(services, conv_id, owner="me", pipeline=pipeline)
    assert err.value.code == "conversation.busy"
    assert pipeline.contexts == []
    # the foreign lease is untouched
    assert (await _conv(services, conv_id)).lease_owner == "other"


async def test_run_turn_job_retries_when_busy(services):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    await lease.acquire(services.sm, conv_id, "other", services.clock, seconds=90)
    services.extra["pipeline"] = Scripted(TurnPlan(reply_text="hello"))

    ctx = JobContext(
        uuid.uuid4(),
        "run_turn",
        {"conversation_id": str(conv_id)},
        1,
        5,
        setup.tenant_id,
        services.clock.now(),
        services,
    )
    with pytest.raises(Retry) as err:
        await run_turn_job(ctx)
    assert err.value.delay_s == 2.0


async def test_run_turn_job_answers_and_sends(services):
    """The whole asynchronous path: run_turn job → reply in outbox → send_message job."""
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    services.extra["pipeline"] = Scripted(TurnPlan(reply_text="hello"))
    async with services.sm() as session, session.begin():
        await enqueue(
            session,
            "run_turn",
            {"conversation_id": str(conv_id)},
            run_at=services.clock.now(),
            tenant_id=setup.tenant_id,
            dedupe_key=f"turn:{conv_id}",
        )

    assert await _worker(services).run_due() == 2
    assert setup.adapter.sent == [("u1", "hello")]
    assert [j.status for j in await _jobs(services)] == [JobStatus.DONE, JobStatus.DONE]
    bot = [m for m in await _messages(services, conv_id) if m.role == MessageRole.BOT]
    assert [(m.text, m.send_status, m.external_id) for m in bot] == [
        ("hello", SendStatus.SENT, "mid-1")
    ]


# --- what the pipeline gets and what the plan changes ------------------------------------


async def test_context_has_history_settings_and_contact(services, clock):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    pipeline = Scripted(TurnPlan(reply_text="hello"))
    await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    first_at = clock.now()

    clock.advance(minutes=1)
    _, second_id = await _say(services, setup, "prices?")
    await run_turn(services, conv_id, owner="t", pipeline=pipeline)

    ctx = pipeline.contexts[1]
    assert ctx.conversation_id == conv_id
    assert ctx.tenant_id == setup.tenant_id
    assert ctx.channel_id == setup.channel_id
    assert ctx.channel_kind == ChannelKind.INSTAGRAM
    assert ctx.contact_name == "Olha"
    assert ctx.tenant_title == "Acme"
    assert ctx.settings.company_title == "Acme"
    assert ctx.pending_ids == [second_id]
    assert ctx.input_text == "prices?"
    assert ctx.state_version == 1
    assert ctx.now == clock.now()
    assert ctx.sandbox is False
    assert ctx.history == [
        HistoryItem(MessageRole.CLIENT, "hi", first_at),
        HistoryItem(MessageRole.BOT, "hello", first_at),
    ]


async def test_history_is_limited(services, clock, monkeypatch):
    setup = await _setup(services)
    pipeline = Scripted(TurnPlan(reply_text="ok"))
    conv_id = None
    for i in range(3):
        conv_id, _ = await _say(services, setup, f"q{i}")
        await run_turn(services, conv_id, owner="t", pipeline=pipeline)
        clock.advance(seconds=1)
    assert conv_id is not None

    monkeypatch.setattr(
        services, "settings", services.settings.model_copy(update={"history_messages": 3})
    )
    await _say(services, setup, "last")
    await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    # the newest 3, oldest first
    assert [h.text for h in pipeline.contexts[-1].history] == ["ok", "q2", "ok"]


async def test_channel_operator_mode_reaches_the_context(services):
    setup = await _setup(services)
    channel_id = await make_channel(
        services.sm,
        setup.tenant_id,
        kind=ChannelKind.INSTAGRAM,
        account_id="ig-2",
        settings={"operator_mode": True},
    )
    conv_id, _ = await _say(services, setup, "hi", channel_id=channel_id)
    pipeline = Scripted(TurnPlan(reply_text="hello"))
    await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    assert pipeline.contexts[0].operator_mode is True


async def test_plan_changes_are_written(services, clock):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "позовіть менеджера")
    seen: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def note(session, conv, turn) -> None:
        seen.append((conv.id, turn.id))
        session.add(
            Message(
                tenant_id=conv.tenant_id,
                channel_id=conv.channel_id,
                conversation_id=conv.id,
                role=MessageRole.SYSTEM,
                text="handover requested",
                turn_id=turn.id,
                created_at=clock.now(),
            )
        )

    plan = TurnPlan(
        reply_text="Зараз покличу менеджера",
        rewritten="Клієнт просить менеджера",
        client_language="uk",
        reply_language="uk",
        route={"wants_human": True},
        handover=True,
        state={"last_presented": []},
        writes=[note],
    )
    outcome = await run_turn(services, conv_id, owner="t", pipeline=Scripted(plan))
    assert outcome is not None
    assert outcome.handover is True
    assert seen == [(conv_id, outcome.turn_id)]

    conv = await _conv(services, conv_id)
    assert conv.handover is True
    assert conv.handover_at == clock.now()
    assert conv.client_language == "uk"
    assert conv.reply_language == "uk"
    assert conv.state == {"last_presented": []}
    assert conv.state_version == 1

    (turn,) = await _turns(services)
    assert turn.rewritten == "Клієнт просить менеджера"
    assert turn.client_language == "uk"
    assert turn.reply_language == "uk"
    assert turn.route == {"wants_human": True}

    roles = [m.role for m in await _messages(services, conv_id)]
    assert MessageRole.SYSTEM in roles

    # handover_at keeps the first moment
    first_handover_at = conv.handover_at
    clock.advance(minutes=5)
    await _say(services, setup, "ну де?")
    await run_turn(services, conv_id, owner="t", pipeline=Scripted(plan))
    assert (await _conv(services, conv_id)).handover_at == first_handover_at


async def test_trace_and_extra_costs_are_stored(services, monkeypatch):
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")

    async def decisions_cost(session, turn_id) -> tuple[int, int, Decimal]:
        return 40, 0, Decimal("0.002")

    monkeypatch.setattr(turn_module, "TURN_COST_SOURCES", [decisions_cost])

    async def pipeline(services, ctx: TurnContext) -> TurnPlan:
        with ctx.trace.step("route", questions=2) as step:
            step["answers"] = 2
        ctx.trace.note("route_summary", {"service_type": "none"})
        return TurnPlan(reply_text="hello")

    await run_turn(services, conv_id, owner="t", pipeline=pipeline)
    (turn,) = await _turns(services)
    assert turn.tokens_in == 40
    assert turn.tokens_out == 0
    assert turn.cost == Decimal("0.002")
    (step,) = turn.trace["steps"]
    assert step["step"] == "route"
    assert step["questions"] == 2
    assert step["answers"] == 2
    assert "ms" in step
    assert turn.trace["route_summary"] == {"service_type": "none"}
    assert turn.duration_ms is not None


async def test_conversation_row_is_not_left_locked(services):
    """After a turn another owner can take the lease right away."""
    setup = await _setup(services)
    conv_id, _ = await _say(services, setup, "hi")
    await run_turn(services, conv_id, owner="a", pipeline=Scripted(TurnPlan(reply_text="x")))
    version = await lease.acquire(services.sm, conv_id, "b", services.clock, seconds=90)
    assert version == 1
