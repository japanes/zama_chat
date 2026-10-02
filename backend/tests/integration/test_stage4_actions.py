"""Scenario actions of stage 4: notify a team chat, write to the client, hand over."""

import pytest
from sqlalchemy import select

from app.actions.registry import fire
from app.db.models import (
    Contact,
    Conversation,
    Job,
    Message,
    MessageRole,
    Notifier,
    OperatorTask,
    RunStatus,
    ScenarioRun,
    TaskKind,
    TaskStatus,
)
from app.scenarios.events import Event
from tests.factories import make_tenant
from tests.scenario_helpers import install_scenario, interview_data, make_def, states

pytestmark = pytest.mark.db

ACTIONS = [
    {"on": "completed", "do": "notify_telegram", "params": {"notifier": "team"}},
    {"on": "completed", "do": "notify_email", "params": {"notifier": "team"}},  # wrong kind
    {
        "on": "completed",
        "do": "message_client",
        "params": {"text": {"uk": "Дякуємо, передали команді!", "en": "Thank you!"}},
    },
    {"on": "cancelled", "do": "handover"},
]


async def _setup(services, *, notifier_events=(), conv_language=None):
    bundle = await make_tenant(services.sm)
    data = interview_data(actions=ACTIONS)
    scenario_id = await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
    defn = make_def(data)
    now = services.clock.now()
    async with services.sm() as session, session.begin():
        contact = Contact(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            external_id="c1",
            username="olha",
            name="Olha",
        )
        session.add(contact)
        await session.flush()
        conv = Conversation(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            contact_id=contact.id,
            reply_language=conv_language,
        )
        session.add(conv)
        session.add(
            Notifier(
                tenant_id=bundle.tenant_id,
                name="team",
                kind="telegram",
                secret_enc=services.crypto.encrypt("123456789:AAE_test_token_for_the_bot_x"),
                config={"chat_ids": ["-100500", "-100600"]},
                events=list(notifier_events),
                language="uk",
            )
        )
        await session.flush()
        run = ScenarioRun(
            tenant_id=bundle.tenant_id,
            channel_id=bundle.channel_id,
            conversation_id=conv.id,
            scenario_id=scenario_id,
            scenario_key="interview",
            scenario_version=1,
            status=RunStatus.COMPLETED,
            fields=states(defn, niche="торти", offer="торти на замовлення"),
            events=[],
            delivery_seq=0,
            client_language="ru",
            reply_language="uk",
            started_at=now,
            finished_at=now,
            last_activity_at=now,
        )
        session.add(run)
        await session.flush()
        return defn, run.id, conv.id


async def _fire(services, defn, run_id, event: str) -> int:
    async with services.sm() as session, session.begin():
        run = await session.get(ScenarioRun, run_id)
        return await fire(session, services, run, defn, [Event(event)], now=services.clock.now())


async def _all(services, model, *where):
    async with services.sm() as session:
        return list((await session.execute(select(model).where(*where))).scalars())


async def test_completed_notifies_the_team_and_thanks_the_client(services):
    defn, run_id, conv_id = await _setup(services)
    assert await _fire(services, defn, run_id, "completed") == 3

    jobs = await _all(services, Job, Job.kind == "notify")
    # one job per chat; the e-mail action names a Telegram notifier → skipped
    assert sorted(j.payload["target"] for j in jobs) == ["-100500", "-100600"]
    values = jobs[0].payload["values"]
    assert jobs[0].payload["template"] == "scenario"
    assert values["scenario"] == "Інтерв'ю"  # the notifier's language
    assert values["event"] == "completed"
    assert "niche: торти" in values["fields"]
    assert "Olha" in values["contact"]

    (msg,) = await _all(services, Message, Message.conversation_id == conv_id)
    assert msg.role == MessageRole.BOT
    assert msg.text == "Дякуємо, передали команді!"  # the run's reply language
    assert msg.meta == {"source": "action", "scenario": "interview", "event": "completed"}


async def test_client_text_follows_the_conversation_language(services):
    defn, run_id, conv_id = await _setup(services, conv_language="en")
    await _fire(services, defn, run_id, "completed")
    (msg,) = await _all(services, Message, Message.conversation_id == conv_id)
    assert msg.text == "Thank you!"


async def test_cancelled_hands_the_conversation_over(services):
    defn, run_id, conv_id = await _setup(services, notifier_events=["handover"])
    assert await _fire(services, defn, run_id, "cancelled") == 1

    (conv,) = await _all(services, Conversation, Conversation.id == conv_id)
    assert conv.handover is True
    (task,) = await _all(services, OperatorTask, OperatorTask.conversation_id == conv_id)
    assert task.kind == TaskKind.HANDOVER
    assert task.status == TaskStatus.OPEN
    assert task.payload["input_text"] == "interview: cancelled"
    # the notifier subscribed to «handover» gets it in both chats
    jobs = await _all(services, Job, Job.kind == "notify")
    assert {j.payload["template"] for j in jobs} == {"handover"}
    assert len(jobs) == 2

    # a second time: already handed over → nothing new
    await _fire(services, defn, run_id, "cancelled")
    assert len(await _all(services, OperatorTask)) == 1
    assert len(await _all(services, Job, Job.kind == "notify")) == 2


async def test_no_message_to_a_client_a_person_already_talks_to(services):
    defn, run_id, conv_id = await _setup(services)
    async with services.sm() as session, session.begin():
        conv = await session.get(Conversation, conv_id)
        conv.handover = True
    await _fire(services, defn, run_id, "completed")
    assert await _all(services, Message, Message.conversation_id == conv_id) == []


async def test_switched_off_notifier_gets_nothing(services):
    defn, run_id, _ = await _setup(services)
    async with services.sm() as session, session.begin():
        (n,) = (await session.execute(select(Notifier))).scalars()
        n.is_active = False
    await _fire(services, defn, run_id, "completed")
    assert await _all(services, Job, Job.kind == "notify") == []
