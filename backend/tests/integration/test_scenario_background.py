"""background_answers over POST /v1/messages on the full pipeline: a long story answers
several questions at once, the bot asks the next unanswered one at once, and the job
scenario_answers writes the answers after the reply (Worker.run_due()). The fake LLM
gives rewrite, extract and reply in that order; the job takes one extract."""

import json

import pytest
from sqlalchemy import select

from app.db.models import JobStatus, RunStatus, ScenarioRun, Turn, TurnStatus
from app.scenarios.fields import PARTLY
from app.scenarios.questions import FULL, IN_PART, MORE, NONE
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
