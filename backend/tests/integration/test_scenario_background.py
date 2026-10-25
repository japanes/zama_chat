"""background_answers over POST /v1/messages on the full pipeline: a long story answers
several questions at once, the bot asks the next unanswered one at once, and the job
scenario_answers writes the answers after the reply (Worker.run_due()). The fake LLM
gives rewrite, extract and reply in that order; the job takes one extract."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    JobStatus,
    Message,
    MessageRole,
    RunStatus,
    ScenarioRun,
    Turn,
    TurnStatus,
)
from app.scenarios.fields import PARTLY
from app.scenarios.questions import CONFIRM, FULL, IN_PART, MORE, NONE
from app.scenarios.runtime import ANSWERS_JOB
from tests.conftest import chat_reply
from tests.integration.test_scenario_flow import _scenario_step, _setup
from tests.scenario_helpers import ROUTE_DEFAULTS, interview_data, jobs_like, runs_of

pytestmark = pytest.mark.db

CONTACT = {"external_id": "c1", "name": "Olha"}
FIRST = "Хочу розібратися з позиціонуванням. Я печу торти"
STORY = "Продаю весільні торти, працюю вдвох з дружиною"


def intro_data() -> dict:
    """niche → offer → years → team, every answer written in the background."""
    data = interview_data(background_answers=True)
    niche, offer, _ = data["fields"]
    data["fields"] = [
        niche,
        offer,
        {"key": "years", "question": {"uk": "Скільки років справі?", "en": "How old?"}},
        {"key": "team", "question": {"uk": "Хто у вашій команді?", "en": "Who is on the team?"}},
    ]
    return data


def _extract(**values) -> dict:
    return chat_reply(
        json.dumps({f"interview__{k}": v for k, v in values.items()}, ensure_ascii=False)
    )


async def _say(client, env, fake_llm, text, *, route, extract=None, reply="Добре."):
    # the field questions of a background scenario are choices: «none» by default
    env.decider.rules = {**ROUTE_DEFAULTS, "field:*": NONE, **route}
    script = [chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False))]
    if extract is not None:
        script.append(extract)
    script.append(chat_reply(reply))
    fake_llm.push(*script)
    resp = await client.post(
        "/v1/messages", json={"contact": CONTACT, "text": text}, headers=env.bundle.auth
    )
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()


async def _run(services, conversation_id) -> ScenarioRun:
    [run] = await runs_of(services.sm, conversation_id)
    return run


async def _dialogue(services, run_id) -> list:
    async with services.sm() as session:
        return (
            await session.execute(select(ScenarioRun.dialogue).where(ScenarioRun.id == run_id))
        ).scalar_one() or []


async def _answer_turns(services, run_id) -> list[Turn]:
    async with services.sm() as session:
        rows = await session.execute(
            select(Turn)
            .where(Turn.route["kind"].astext == ANSWERS_JOB)
            .where(Turn.route["run_id"].astext == str(run_id))
            .order_by(Turn.created_at)
        )
        return list(rows.scalars())


async def test_a_story_is_written_in_the_background(client, services, fake_llm):
    env = await _setup(services, intro_data())
    # a new run reads its first answers in the turn, as before
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]
    run = await _run(services, conv)
    assert (run.asking, run.fields["niche"]["value"]) == ("offer", "торти")

    # the story answers «offer» in full and «team» in part: no extract in the turn,
    # the bot asks «years» at once
    route = {
        "field:interview.niche": NONE,
        "field:interview.offer": FULL,
        "field:interview.years": NONE,
        "field:interview.team": IN_PART,
    }
    await _say(client, env, fake_llm, STORY, route=route)
    run = await _run(services, conv)
    assert run.asking == "years"
    assert _scenario_step(fake_llm)["ask_next"] == {"question": "Скільки років справі?"}
    offer, team = run.fields["offer"], run.fields["team"]
    assert offer["value"] is None and len(offer["pending"]) == 1
    assert team["answered_partly"] is True and team["pending"] == offer["pending"]
    # the run's dialogue as it was said, turn by turn
    first, story = await _dialogue(services, run.id)
    assert (first["client"], story["client"]) == (FIRST, STORY)
    assert story["turn"] == offer["pending"][0]
    [job] = await jobs_like(services.sm, f"answers:{run.id}")
    assert job.kind == ANSWERS_JOB

    # the job: one extract for both, with the story's words, then the filters
    fake_llm.push(_extract(offer="весільні торти", team="вдвох з дружиною"))
    await env.worker.run_due()
    assert not fake_llm.script
    sent = fake_llm.requests[-1]["messages"][-1]["content"]
    assert STORY in sent
    run = await _run(services, conv)
    assert run.fields["offer"]["state"] == "filled"
    assert run.fields["offer"]["value"] == "весільні торти"
    assert run.fields["team"]["state"] == "partial"
    assert run.fields["team"]["failed_filters"] == [PARTLY]
    assert run.fields["offer"]["pending"] == run.fields["team"]["pending"] == []
    assert run.asking == "years"  # the turn's question stands
    [written] = await _answer_turns(services, run.id)
    assert written.status == TurnStatus.DONE

    # «years» is answered: the bot comes to «team» and asks only for what is missing
    route = {**route, "field:interview.offer": NONE, "field:interview.years": FULL}
    route["field:interview.team"] = NONE
    await _say(client, env, fake_llm, "Вже п'ять років", route=route)
    run = await _run(services, conv)
    assert run.asking == "team"
    clarify = _scenario_step(fake_llm)["clarify"]
    assert clarify["client_answer"] == "вдвох з дружиною"
    fake_llm.push(_extract(years="п'ять років"))
    await env.worker.run_due()
    run = await _run(services, conv)
    assert run.fields["years"]["value"] == "п'ять років"

    # the last answer: nothing is left to ask, so the turn waits for it — and completes
    route = {**route, "field:interview.years": NONE, "field:interview.team": MORE}
    await _say(
        client,
        env,
        fake_llm,
        "Ще бариста Оля",
        route=route,
        extract=_extract(team="бариста Оля"),
    )
    run = await _run(services, conv)
    assert run.status == RunStatus.COMPLETED
    assert "вдвох з дружиною" in run.fields["team"]["value"]
    assert "бариста Оля" in run.fields["team"]["value"]
    assert run.fields["team"]["state"] == "filled"
    assert _scenario_step(fake_llm)["scenario_completed"] == "Інтерв'ю"
    jobs = await jobs_like(services.sm, f"answers:{run.id}")
    assert all(j.status != JobStatus.QUEUED for j in jobs)

    # the caller sees the background turns and what they cost
    resp = await client.post(
        "/v1/runs/state",
        json={"contact": CONTACT, "scenario": "interview"},
        headers=env.bundle.auth,
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()["run"]
    assert state["answers_pending"] == []
    turns = [str(t.id) for t in await _answer_turns(services, run.id)]
    assert state["answer_turns"] == turns and len(turns) == 2
    resp = await client.post(
        f"/v1/runs/{run.id}/usage", json={"turns": turns}, headers=env.bundle.auth
    )
    assert resp.status_code == 200, resp.text
    usage = resp.json()["usage"]
    assert [u["turn_id"] for u in usage] == turns
    assert all(u["calls"] for u in usage)


async def test_nothing_found_asks_the_question_again(client, services, fake_llm):
    env = await _setup(services, intro_data())
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]
    # the decider heard «years» in the story, the extract finds nothing
    route = {"field:interview.offer": FULL, "field:interview.years": FULL}
    await _say(client, env, fake_llm, "Весільні торти, давно", route=route)
    run = await _run(services, conv)
    assert run.asking == "team"
    fake_llm.push(_extract(offer="весільні торти", years=None))
    await env.worker.run_due()
    run = await _run(services, conv)
    assert run.fields["offer"]["value"] == "весільні торти"
    years = run.fields["years"]
    assert (years["value"], years["pending"], years["answered"]) == (None, [], False)


async def _story_then_skip(client, env, services, fake_llm, *, team=IN_PART):
    """«years» is asked after the story and skipped while «offer» and «team» are being
    written: the run has nothing to ask until the job is done."""
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]
    route = {
        "field:interview.niche": NONE,
        "field:interview.offer": FULL,
        "field:interview.years": NONE,
        "field:interview.team": team,
    }
    await _say(client, env, fake_llm, STORY, route=route)
    run = await _run(services, conv)
    assert run.asking == "years"
    resp = await client.post(
        f"/v1/runs/{run.id}/skip", json={"messages": 10}, headers=env.bundle.auth
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["run"]["asking"] is None
    assert state["run"]["answers_pending"] == ["offer", "team"]
    assert state["messages"][-1]["text"] == "Добре."  # the story's reply: nothing new is said
    return conv, run.id


async def _bot_messages(services, conversation_id) -> list[Message]:
    async with services.sm() as session:
        rows = await session.execute(
            select(Message)
            .where(Message.conversation_id == uuid.UUID(str(conversation_id)))
            .where(Message.role == MessageRole.BOT)
            .order_by(Message.created_at, Message.id)
        )
        return list(rows.scalars())


async def test_a_question_skipped_meanwhile_is_followed_by_the_jobs_own(client, services, fake_llm):
    env = await _setup(services, intro_data())
    conv, run_id = await _story_then_skip(client, env, services, fake_llm)
    before = len(await _bot_messages(services, conv))

    # the job writes the answers, then words the question that is left — the partial
    # «team», with what is said of it — and sends it with the answers
    fake_llm.push(
        _extract(offer="весільні торти", team="вдвох з дружиною"),
        chat_reply("Пам'ятаю, ви працюєте вдвох з дружиною. Хто ще у команді?"),
    )
    await env.worker.run_due()
    assert not fake_llm.script
    clarify = _scenario_step(fake_llm)["clarify"]
    assert clarify["client_answer"] == "вдвох з дружиною"
    run = await _run(services, conv)
    assert run.asking == "team"
    assert run.fields["team"]["state"] == "partial"
    assert run.fields["offer"]["pending"] == run.fields["team"]["pending"] == []
    messages = await _bot_messages(services, conv)
    assert len(messages) == before + 1
    asked = messages[-1]
    assert asked.text == "Пам'ятаю, ви працюєте вдвох з дружиною. Хто ще у команді?"
    assert asked.meta["asked_by"] == ANSWERS_JOB
    # the question is paid in the job's turn: the caller's usage of the run has it
    [written] = await _answer_turns(services, run_id)
    assert asked.turn_id == written.id
    resp = await client.post(
        f"/v1/runs/{run_id}/usage", json={"turns": [str(written.id)]}, headers=env.bundle.auth
    )
    assert resp.status_code == 200, resp.text
    [usage] = resp.json()["usage"]
    assert [c["role"] for c in usage["calls"] if c["kind"] == "llm"] == ["extract", "reply"]


async def test_all_written_after_a_skip_asks_to_confirm(client, services, fake_llm):
    data = intro_data()
    data["completion"] = "explicit_yes"
    env = await _setup(services, data)
    conv, _ = await _story_then_skip(client, env, services, fake_llm, team=FULL)

    # nothing left to ask: «is everything correct?» as the scenario says it, no model
    fake_llm.push(_extract(offer="весільні торти", team="вдвох з дружиною"))
    await env.worker.run_due()
    assert not fake_llm.script
    run = await _run(services, conv)
    assert (run.status, run.asking) == (RunStatus.FOCUS, CONFIRM)
    messages = await _bot_messages(services, conv)
    assert messages[-1].text == "Усе правильно?"
    assert messages[-1].meta["asked_by"] == ANSWERS_JOB


async def test_the_job_asks_nothing_when_the_client_wrote_meanwhile(client, services, fake_llm):
    env = await _setup(services, intro_data())
    conv, _ = await _story_then_skip(client, env, services, fake_llm)
    before = await _bot_messages(services, conv)
    # words that came in after the skip: their turn asks
    async with services.sm() as session, session.begin():
        session.add(
            Message(
                tenant_id=before[-1].tenant_id,
                channel_id=before[-1].channel_id,
                conversation_id=before[-1].conversation_id,
                role=MessageRole.CLIENT,
                text="А ще в нас є кав'ярня",
            )
        )
    fake_llm.push(
        _extract(offer="весільні торти", team="вдвох з дружиною"),
        chat_reply("Хто ще у команді?"),
    )
    await env.worker.run_due()
    run = await _run(services, conv)
    assert run.asking is None
    assert run.fields["offer"]["value"] == "весільні торти"
    assert len(await _bot_messages(services, conv)) == len(before)


async def test_a_correction_is_written_in_the_background(client, services, fake_llm):
    env = await _setup(services, intro_data())
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]

    # «not cakes but pastries»: Jev hears the correction, the turn reads nothing — the bot
    # asks «offer» at once, the old answer stands until the job writes the new one
    await _say(client, env, fake_llm, "Не торти, а тістечка", route={"change_field": "niche"})
    run = await _run(services, conv)
    assert run.asking == "offer"
    niche = run.fields["niche"]
    assert niche["value"] == "торти" and len(niche["pending"]) == 1
    said = (await _dialogue(services, run.id))[-1]
    assert said["corrects"] == ["niche"] and said["turn"] == niche["pending"][0]

    # the job: the new words replace the answer, they are not added to it
    fake_llm.push(_extract(niche="тістечка"))
    await env.worker.run_due()
    assert not fake_llm.script
    assert "current_value" in fake_llm.requests[-1]["messages"][-1]["content"]
    run = await _run(services, conv)
    assert run.fields["niche"]["value"] == "тістечка"
    assert run.fields["niche"]["state"] == "filled"
    assert run.fields["niche"]["pending"] == []
    assert run.asking == "offer"


CITY = {"key": "city", "question": {"uk": "У якому ви місті?", "en": "Which city?"}}


async def _team_said_in_part(client, env, services, fake_llm):
    """niche → offer → team → years → city: the story answers «offer» in full and «team»
    in part, the job writes them; the bot asks «years»."""
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, team=None, years=None, city=None),
    )
    conv = body["conversation_id"]
    route = {"field:interview.offer": FULL, "field:interview.team": IN_PART}
    await _say(client, env, fake_llm, STORY, route=route)
    fake_llm.push(_extract(offer="весільні торти", team="вдвох з дружиною"))
    await env.worker.run_due()
    run = await _run(services, conv)
    assert (run.asking, run.fields["team"]["state"]) == ("years", "partial")
    return conv, run.id


def _partial_first_data() -> dict:
    data = intro_data()
    niche, offer, years, team = data["fields"]
    data["fields"] = [niche, offer, team, years, CITY]
    return data


async def _skip(client, env, run_id, **body):
    resp = await client.post(
        f"/v1/runs/{run_id}/skip", json={"messages": 10, **body}, headers=env.bundle.auth
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_another_question_is_one_not_answered_yet(client, services, fake_llm):
    env = await _setup(services, _partial_first_data())
    _, run_id = await _team_said_in_part(client, env, services, fake_llm)
    calls = fake_llm.calls

    # «team» comes before «city», but part of it is said: «city» is asked first
    state = await _skip(client, env, run_id)
    assert state["run"]["asking"] == "city"
    assert state["messages"][-1]["text"] == "У якому ви місті?"
    # nothing else is left: «team» as the scenario words it, no model call
    state = await _skip(client, env, run_id)
    assert state["run"]["asking"] == "team"
    assert state["messages"][-1]["text"] == "Хто у вашій команді?"
    assert (fake_llm.calls, state["usage"]) == (calls, None)


async def test_a_question_answered_in_part_is_worded_by_the_bot(client, services, fake_llm):
    env = await _setup(services, _partial_first_data())
    _, run_id = await _team_said_in_part(client, env, services, fake_llm)
    calls = fake_llm.calls

    # nothing of «city» is said: its own words, no model call
    state = await _skip(client, env, run_id, word=True)
    assert (state["run"]["asking"], state["usage"]) == ("city", None)
    assert fake_llm.calls == calls

    # «team»: the bot asks for the rest, knowing what is said of it — a paid bot turn
    fake_llm.push(chat_reply("Пам'ятаю, ви працюєте вдвох з дружиною. Хто ще у команді?"))
    state = await _skip(client, env, run_id, word=True)
    assert not fake_llm.script
    assert state["run"]["asking"] == "team"
    assert _scenario_step(fake_llm)["clarify"]["client_answer"] == "вдвох з дружиною"
    assert [m["text"] for m in state["messages"][-2:]] == [
        "У якому ви місті?",
        "Пам'ятаю, ви працюєте вдвох з дружиною. Хто ще у команді?",
    ]
    assert [c["role"] for c in state["usage"]["calls"] if c["kind"] == "llm"] == ["reply"]


def limited_data() -> dict:
    """intro_data with one clarification of a question, then the answer is taken."""
    return {**intro_data(), "clarify_limit": 1}


async def _field_out(client, env, key) -> dict:
    resp = await client.post(
        "/v1/runs/state",
        json={"contact": CONTACT, "scenario": "interview"},
        headers=env.bundle.auth,
    )
    assert resp.status_code == 200, resp.text
    return {f["key"]: f for f in resp.json()["run"]["fields"]}[key]


async def test_a_general_answer_after_one_clarification_is_taken_as_said(
    client, services, fake_llm
):
    env = await _setup(services, limited_data())
    vague = {"filter:interview.niche.concrete": False}
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL, **vague},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]
    run = await _run(services, conv)
    # the niche is too general: the bot asks for the rest — the one clarification
    assert run.asking == "niche"
    assert run.fields["niche"]["state"] == "partial"
    assert run.fields["niche"]["clarified"] == 1

    # «for everyone»: the decider hears no niche in it, yet it is the reply to the question
    await _say(client, env, fake_llm, "Для всіх", route=vague)
    run = await _run(services, conv)
    assert len(run.fields["niche"]["pending"]) == 1
    assert run.asking == "offer"  # not asked a third time
    fake_llm.push(_extract(niche="для всіх"))
    await env.worker.run_due()
    run = await _run(services, conv)
    niche = run.fields["niche"]
    assert niche["state"] == "filled" and "для всіх" in niche["value"]
    assert niche["failed_filters"] == [] and niche["taken_as_said"] is True
    out = await _field_out(client, env, "niche")
    assert (out["taken_as_said"], out["clarified"]) == (True, 1)


async def test_a_question_left_without_a_word_twice_is_closed(client, services, fake_llm):
    env = await _setup(services, limited_data())
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]
    assert (await _run(services, conv)).asking == "offer"

    # «I don't know»: read as the reply, nothing found — asked again later
    await _say(client, env, fake_llm, "Не знаю", route={})
    run = await _run(services, conv)
    assert run.asking == "years"
    fake_llm.push(_extract(offer=None))
    await env.worker.run_due()
    run = await _run(services, conv)
    offer = run.fields["offer"]
    assert (offer["value"], offer["closed"], offer["clarified"]) == (None, False, 0)

    await _say(client, env, fake_llm, "П'ять років", route={"field:interview.years": FULL})
    run = await _run(services, conv)
    assert run.asking == "offer" and run.fields["offer"]["clarified"] == 1
    fake_llm.push(_extract(years="п'ять років"))
    await env.worker.run_due()

    # nothing again after the clarification: the question is closed, the bot goes on
    await _say(client, env, fake_llm, "Та різне", route={})
    assert (await _run(services, conv)).asking == "team"
    fake_llm.push(_extract(offer=None))
    await env.worker.run_due()
    run = await _run(services, conv)
    assert run.fields["offer"]["closed"] is True
    assert run.fields["offer"]["value"] is None


async def test_the_clients_own_question_is_not_a_clarification(client, services, fake_llm):
    env = await _setup(services, limited_data())
    body = await _say(
        client,
        env,
        fake_llm,
        FIRST,
        route={"start:interview.wants_help": True, "field:interview.niche": FULL},
        extract=_extract(niche="торти", offer=None, years=None, team=None),
    )
    conv = body["conversation_id"]
    await _say(client, env, fake_llm, "А скільки коштує?", route={"client_question": True})
    run = await _run(services, conv)
    offer = run.fields["offer"]
    assert run.asking == "offer"
    assert (offer["asks"], offer["clarified"], offer["pending"]) == (1, 0, [])
