"""A scenario the caller leads (`completion: by_caller`) over the public API, on the full
pipeline: questions shown only when they apply (`show_if`), the card of the dialogue in
the reply, questions added to a running and to a waiting run (POST /v1/runs/{id}/fields),
said in the bot's own words or as sent, closed after failed answers, a run finished in
silence (/finish), choice options the caller gives at the start, and two scenarios of one
conversation — the added question takes the dialogue and gives it back. Fake LLM (rewrite,
extract, reply in that order), FakeDecider — like tests/integration/test_api_runs.py."""

import json

import pytest
from sqlalchemy import select

from app.core.pipeline import full_pipeline
from app.db.models import LlmRole, Message, MessageRole
from app.decisions.fake import FakeDecider
from app.prompts.seed import seed_prompts
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import ROUTE_DEFAULTS, install_scenario

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT)
LANGUAGES = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}
CONTACT = {"external_id": "story:1", "name": "Olha"}
SAY = {"uk": "Дякую, матеріалу досить.", "en": "Thank you, that is enough material."}
RULE = {"uk": "Не припускай, що подія була.", "en": "Do not assume the event happened."}

STORY = "Я вклала останні 1000 євро у перший бізнес, мабуть гроші на кону"
STAKES = "мабуть гроші на кону"


def facts_data(*fields, **over):
    data = {
        "key": "facts",
        "title": {"uk": "Фактаж", "en": "Facts"},
        "language": "uk",
        "start": {"questions": [{"key": "tells", "ask": {"uk": "Розповідає?", "en": "Tells?"}}]},
        "completion": "by_caller",
        "waiting": {"say": SAY},
        "ask_rules": [RULE],
        "fields": [
            {"key": "story", "question": {"uk": "Розкажіть історію", "en": "Tell the story"}},
            *fields,
        ],
    }
    data.update(over)
    return data


STAKES_FIELD = {
    "key": "stakes",
    "question": {"uk": "Що було на кону?", "en": "What was at stake?"},
    "ask_after": ["story"],
    "filters": [{"key": "named", "ask": {"uk": "Названо, що на кону?", "en": "Named?"}}],
    "show_if": {
        "questions": [{"key": "risked", "ask": {"uk": "Ризикувала?", "en": "Took a risk?"}}]
    },
}
AGE_FIELD = {
    "key": "age",
    "question": {"uk": "Скільки вам було?", "en": "How old were you?"},
    "ask_after": ["story"],
}

TEXT = {
    "key": "text",
    "title": {"uk": "Текст", "en": "Text"},
    "language": "uk",
    "start": {"questions": [{"key": "wants", "ask": {"uk": "Хоче текст?", "en": "Text?"}}]},
    "completion": "by_caller",
    "fields": [
        {
            "key": "angle",
            "type": "choice",
            "question": {"uk": "Який ракурс беремо?", "en": "Which angle do we take?"},
            "options_from": "caller",
        },
        {"key": "goal", "question": {"uk": "Навіщо цей текст?", "en": "What is the text for?"}},
    ],
}
ANGLES = [
    {"key": "husband", "label": "Чоловік був проти — сумнів перед рішенням"},
    {"key": "savings", "label": "Останні 1000 євро — ризик усім"},
]
Q1 = {
    "key": "q1",
    "question": {"uk": "Скільки ви вклали?", "en": "How much did you put in?"},
    "filters": [{"key": "sum", "ask": {"uk": "Названо суму?", "en": "Is a sum named?"}}],
}


async def _setup(services, *scenarios):
    decider = FakeDecider(dict(ROUTE_DEFAULTS))
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    for data in scenarios:
        await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
    return bundle, decider


async def _post(client, bundle, path, **body):
    return await client.post(path, json=body, headers=bundle.auth)


async def _start(client, bundle, scenario="facts", **body):
    resp = await _post(
        client, bundle, "/v1/runs/start", contact=CONTACT, scenario=scenario, language="en", **body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["run"]


async def _say(
    client, bundle, decider, fake_llm, text, *, route, extract, reply, rules=None, scenario="facts"
):
    """A message of the client: rewrite → extract → reply; the decider answers the route,
    the filters and the conditions. The run returned is `scenario`'s (None — the one in the
    focus)."""
    decider.rules = {**ROUTE_DEFAULTS, **route, **(rules or {})}
    fake_llm.push(
        chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False)),
        chat_reply(json.dumps(extract, ensure_ascii=False)),
        chat_reply(reply),
    )
    body = {"scenario": scenario} if scenario else {}
    resp = await _post(client, bundle, "/v1/runs/message", contact=CONTACT, text=text, **body)
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()


async def _state(client, bundle, scenario):
    resp = await _post(client, bundle, "/v1/runs/state", contact=CONTACT, scenario=scenario)
    assert resp.status_code == 200, resp.text
    return resp.json()["run"]


def _fields(run: dict) -> dict[str, dict]:
    return {f["key"]: f for f in run["fields"]}


def _step(fake_llm) -> dict:
    """The `scenario_step` of the last reply request."""
    return json.loads(fake_llm.requests[-1]["messages"][-1]["content"])["scenario_step"]


async def _bot_texts(services) -> list[str]:
    async with services.sm() as session:
        rows = await session.execute(
            select(Message.text)
            .where(Message.role == MessageRole.BOT)
            .order_by(Message.created_at, Message.id)
        )
        return [text for (text,) in rows]


async def _story_told(client, bundle, decider, fake_llm, reply="Thank you, enough."):
    return await _say(
        client,
        bundle,
        decider,
        fake_llm,
        STORY,
        route={"field:facts.story": True},
        extract={"facts__story": STORY},
        reply=reply,
    )


# --- show_if and the card ---------------------------------------------------------------------


async def test_a_question_that_does_not_apply_is_not_asked_and_keeps_what_was_said(
    services, client, fake_llm
):
    bundle, decider = await _setup(services, facts_data(STAKES_FIELD))
    run = await _start(client, bundle)
    assert (run["status"], run["asking"]) == ("focus", "story")
    assert fake_llm.calls == 0  # the first question goes as written

    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        STORY,
        route={"field:facts.story": True, "field:facts.stakes": True},
        extract={"facts__story": STORY, "facts__stakes": STAKES},
        rules={"filter:facts.stakes.named": False, "show:facts.stakes.risked": False},
        reply="Thank you, that is enough material.",
    )
    assert "show:facts.stakes.risked" in decider.asked()
    run = body["run"]
    assert (run["status"], run["waiting_for"], run["asking"]) == ("waiting", "fields", None)
    stakes = _fields(run)["stakes"]
    assert (stakes["hidden"], stakes["state"], stakes["value"]) == (True, "partial", STAKES)
    assert _fields(run)["story"]["asked"] == "Tell the story"

    step = _step(fake_llm)
    assert step["waiting"] == [{"title": "Facts", "for": "fields", "new": True, "say": SAY["en"]}]
    assert step["known"][0] == {
        "question": "Tell the story",
        "asked": "Tell the story",
        "answer": STORY,
    }
    # nothing to ask: the scenario's rules of wording a question are not there
    assert RULE["en"] not in step["rules"]
    assert await _bot_texts(services) == ["Tell the story", "Thank you, that is enough material."]


async def test_the_condition_is_checked_again_after_a_new_answer(services, client, fake_llm):
    bundle, decider = await _setup(services, facts_data(STAKES_FIELD, AGE_FIELD))
    await _start(client, bundle)

    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        STORY,
        route={"field:facts.story": True},
        extract={"facts__story": STORY},
        rules={"show:facts.stakes.risked": False},
        reply="How old were you then?",
    )
    run = body["run"]
    assert (run["asking"], _fields(run)["stakes"]["hidden"]) == ("age", True)
    # the bot asks: the scenario's rules go to the reply, and only to it
    assert _step(fake_llm)["rules"][-1] == RULE["en"]
    rewrite, extract = fake_llm.requests[-3], fake_llm.requests[-2]
    assert RULE["en"] not in json.dumps(rewrite, ensure_ascii=False)
    assert RULE["en"] not in json.dumps(extract, ensure_ascii=False)
    assert all(RULE["en"] not in state for state, _ in decider.calls)

    # the client mentions a loan: the condition reads the answers again and says yes
    text = "Мені було 27, я взяла кредит"
    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        text,
        route={"field:facts.age": True},
        extract={"facts__age": text},
        rules={"show:facts.stakes.risked": lambda q, state: "кредит" in state},
        reply="What was at stake?",
    )
    run = body["run"]
    assert (run["status"], run["asking"]) == ("focus", "stakes")
    assert _fields(run)["stakes"]["hidden"] is False
    assert _fields(run)["age"]["asked"] == "How old were you then?"
    assert decider.asked().count("show:facts.stakes.risked") == 2
    # the card of the reply: the answers whole, with how the bot asked them
    assert {"question": "How old were you?", "asked": "How old were you then?", "answer": text} in (
        _step(fake_llm)["known"]
    )


# --- added questions --------------------------------------------------------------------------


async def test_a_question_added_to_a_waiting_run_is_asked_in_the_bots_own_words(
    services, client, fake_llm
):
    bundle, decider = await _setup(services, facts_data())
    run = await _start(client, bundle)
    run = (await _story_told(client, bundle, decider, fake_llm))["run"]
    assert (run["status"], run["waiting_for"]) == ("waiting", "fields")
    calls = fake_llm.calls

    fake_llm.push(chat_reply("And how much did you put in, all of it?"))
    resp = await _post(client, bundle, f"/v1/runs/{run['id']}/fields", fields=[Q1])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["run"]["status"], body["run"]["asking"]) == ("focus", "q1")
    assert body["run"]["waiting_for"] is None
    q1 = _fields(body["run"])["q1"]
    assert (q1["question"], q1["added_at"] is not None) == ("How much did you put in?", True)
    # one bot turn, paid like any turn
    assert fake_llm.calls == calls + 1
    assert [c["role"] for c in body["usage"]["calls"]] == ["reply"]
    assert body["usage"]["calls"][0]["effort"] is None  # the role asks no thinking level
    step = _step(fake_llm)
    assert step["ask_next"]["question"] == "How much did you put in?"
    assert step["known"][0]["answer"] == STORY
    texts = await _bot_texts(services)
    assert texts[-1] == "And how much did you put in, all of it?"

    # sent again: nothing changes, no model call
    resp = await _post(client, bundle, f"/v1/runs/{run['id']}/fields", fields=[Q1])
    assert (resp.status_code, resp.json()["usage"]) == (200, None)
    assert fake_llm.calls == calls + 1
    # the same key with another text, or a key of the scenario: refused
    other = {**Q1, "question": "Інше питання?"}
    for fields in ([other], [{"key": "story", "question": "Ще?"}]):
        resp = await _post(client, bundle, f"/v1/runs/{run['id']}/fields", fields=fields)
        assert (resp.status_code, resp.json()["error"]["code"]) == (409, "run.field_exists")

    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        "Усі 1000",
        route={"field:facts.q1": True},
        extract={"facts__q1": "Усі 1000"},
        rules={"filter:facts.q1.sum": True},
        reply="Thank you, that is enough material.",
    )
    run = body["run"]
    assert (run["status"], run["waiting_for"]) == ("waiting", "fields")
    q1 = _fields(run)["q1"]
    assert (q1["value"], q1["asked"]) == ("Усі 1000", "And how much did you put in, all of it?")

    # no more questions: completed in silence
    count = len(await _bot_texts(services))
    calls = fake_llm.calls
    for _ in range(2):  # the second time nothing changes
        resp = await _post(client, bundle, f"/v1/runs/{run['id']}/finish")
        assert resp.status_code == 200, resp.text
        assert resp.json()["run"]["status"] == "completed"
    assert (len(await _bot_texts(services)), fake_llm.calls) == (count, calls)
    resp = await _post(client, bundle, f"/v1/runs/{run['id']}/fields", fields=[{**Q1, "key": "q2"}])
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "run.not_open")


async def test_a_question_added_as_sent_is_said_word_for_word(services, client, fake_llm):
    bundle, decider = await _setup(services, facts_data(added={"wording": "as_sent"}))
    await _start(client, bundle)
    run = (await _story_told(client, bundle, decider, fake_llm))["run"]
    calls = fake_llm.calls
    resp = await _post(client, bundle, f"/v1/runs/{run['id']}/fields", fields=[Q1])
    assert resp.status_code == 200, resp.text
    assert (resp.json()["run"]["asking"], resp.json()["usage"]) == ("q1", None)
    assert fake_llm.calls == calls
    assert (await _bot_texts(services))[-1] == "How much did you put in?"


async def test_an_added_question_is_closed_after_failed_answers_with_what_was_said(
    services, client, fake_llm
):
    bundle, decider = await _setup(services, facts_data(added={"close_after": 2}))
    await _start(client, bundle)
    run = (await _story_told(client, bundle, decider, fake_llm))["run"]
    fake_llm.push(chat_reply("How much was it?"))
    await _post(client, bundle, f"/v1/runs/{run['id']}/fields", fields=[Q1])

    for text in ("Багато", "Дуже багато"):
        body = await _say(
            client,
            bundle,
            decider,
            fake_llm,
            text,
            route={"field:facts.q1": True},
            extract={"facts__q1": text},
            rules={"filter:facts.q1.sum": False},
            reply="Could you name the sum?",
        )
    run = body["run"]
    q1 = _fields(run)["q1"]
    assert (q1["closed"], q1["state"]) == (True, "partial")
    assert "Дуже багато" in q1["value"]
    assert (run["status"], run["waiting_for"]) == ("waiting", "fields")


async def test_added_questions_are_limited_by_the_scenario(services, client, fake_llm):
    bundle, _ = await _setup(services, facts_data(added={"max_fields": 1, "wording": "as_sent"}))
    run = await _start(client, bundle)
    resp = await _post(
        client, bundle, f"/v1/runs/{run['id']}/fields", fields=[Q1, {**Q1, "key": "q2"}]
    )
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "run.too_many_fields")


async def test_only_a_by_caller_scenario_takes_added_questions(services, client, fake_llm):
    data = facts_data(completion="all_fields")
    del data["waiting"]
    bundle, _ = await _setup(services, data)
    run = await _start(client, bundle)
    for path, body in (("fields", {"fields": [Q1]}), ("finish", {})):
        resp = await _post(client, bundle, f"/v1/runs/{run['id']}/{path}", **body)
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == f"run.{path}_not_allowed"


# --- the caller's options ---------------------------------------------------------------------


async def test_choice_options_come_with_the_start(services, client, fake_llm):
    bundle, decider = await _setup(services, TEXT)
    resp = await _post(client, bundle, "/v1/runs/start", contact=CONTACT, scenario="text")
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "run.options_missing")

    run = await _start(client, bundle, scenario="text", options={"angle": ANGLES})
    assert _fields(run)["angle"]["options"] == ANGLES

    # an option that is not among the given ones is not taken
    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        "Давайте про бабусю",
        route={"field:text.angle": True},
        extract={"text__angle": "grandma"},
        reply="Which of the two angles?",
        scenario="text",
    )
    assert _fields(body["run"])["angle"]["value"] is None
    # the bot reads the labels when it asks
    assert "Чоловік був проти — сумнів перед рішенням" in json.dumps(
        _step(fake_llm), ensure_ascii=False
    )

    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        "Давайте про чоловіка",
        route={"field:text.angle": True},
        extract={"text__angle": "husband"},
        reply="What is the text for?",
        scenario="text",
    )
    run = body["run"]
    assert (_fields(run)["angle"]["value"], run["asking"]) == ("husband", "goal")


async def test_questions_are_added_one_after_another_to_a_run_with_the_callers_options(
    services, client, fake_llm
):
    bundle, _ = await _setup(services, TEXT, facts_data())
    for scenario, options, keys in (
        ("text", {"angle": ANGLES}, ["angle", "goal"]),
        ("facts", None, ["story"]),
    ):
        body = {"options": options} if options else {}
        run = await _start(client, bundle, scenario=scenario, **body)
        for key in ("q1", "q2"):
            resp = await _post(
                client, bundle, f"/v1/runs/{run['id']}/fields", fields=[{**Q1, "key": key}]
            )
            assert resp.status_code == 200, resp.text
        run = resp.json()["run"]
        assert [f["key"] for f in run["fields"]] == [*keys, "q1", "q2"]
        if options:
            assert _fields(run)["angle"]["options"] == ANGLES
        await _post(client, bundle, f"/v1/runs/{run['id']}/finish")


# --- buttons pressed (MessageIn.pick) ---------------------------------------------------------


async def _press(client, bundle, decider, fake_llm, text, pick, *, reply, route=None):
    """A button pressed: the route says the message answers nothing (`field:*` — no), so
    only the pick can fill the step; no extract call — rewrite and reply only."""
    decider.rules = {**ROUTE_DEFAULTS, **(route or {})}
    fake_llm.push(
        chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False)),
        chat_reply(reply),
    )
    resp = await _post(
        client, bundle, "/v1/runs/message", contact=CONTACT, text=text, pick=pick, scenario="text"
    )
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()["run"]


async def test_a_pressed_button_answers_the_question_asked_without_the_models(
    services, client, fake_llm
):
    bundle, decider = await _setup(services, TEXT)
    await _start(client, bundle, scenario="text", options={"angle": ANGLES})

    run = await _press(
        client,
        bundle,
        decider,
        fake_llm,
        ANGLES[1]["label"],
        {"field": "angle", "option": "savings"},
        reply="What is the text for?",
    )
    assert (_fields(run)["angle"]["value"], run["asking"]) == ("savings", "goal")

    # a text step takes the button's words; a button of a step not asked is left alone
    run = await _press(
        client,
        bundle,
        decider,
        fake_llm,
        "Довіра",
        {"field": "goal"},
        reply="Thank you.",
    )
    assert _fields(run)["goal"]["value"] == "Довіра"
    async with services.sm() as session:
        meta = (
            await session.execute(select(Message.meta).where(Message.text == "Довіра"))
        ).scalar_one()
    assert meta == {"pick": {"field": "goal"}}


async def test_a_pressed_button_answers_no_other_step(services, client, fake_llm):
    """The route takes the label of the angle for the goal as well: the button answers its
    own question only, no extract call fills the goal with it."""
    bundle, decider = await _setup(services, TEXT)
    await _start(client, bundle, scenario="text", options={"angle": ANGLES})
    run = await _press(
        client,
        bundle,
        decider,
        fake_llm,
        ANGLES[1]["label"],
        {"field": "angle", "option": "savings"},
        reply="What is the text for?",
        route={"field:text.goal": True},
    )
    assert (_fields(run)["angle"]["value"], _fields(run)["goal"]["value"]) == ("savings", None)
    assert run["asking"] == "goal"


async def test_a_button_of_another_step_or_option_is_not_taken(services, client, fake_llm):
    bundle, decider = await _setup(services, TEXT)
    await _start(client, bundle, scenario="text", options={"angle": ANGLES})
    for pick in ({"field": "goal"}, {"field": "angle", "option": "grandma"}):
        run = await _press(
            client, bundle, decider, fake_llm, "Бабуся", pick, reply="Which of the two?"
        )
        assert _fields(run)["angle"]["value"] is None and _fields(run)["goal"]["value"] is None
        assert run["asking"] == "angle"


# --- two scenarios of one conversation --------------------------------------------------------


async def test_an_added_question_takes_the_dialogue_and_gives_it_back(services, client, fake_llm):
    bundle, decider = await _setup(services, facts_data(), TEXT)
    facts = await _start(client, bundle)
    await _story_told(client, bundle, decider, fake_llm)

    # the facts wait for questions: the text starts in the same conversation
    text = await _start(client, bundle, scenario="text", options={"angle": ANGLES})
    assert (text["status"], text["asking"]) == ("focus", "angle")

    # a fact is missing: the facts take the dialogue for one question
    fake_llm.push(chat_reply("Before we go on: how much did you put in?"))
    resp = await _post(client, bundle, f"/v1/runs/{facts['id']}/fields", fields=[Q1], focus=True)
    assert resp.status_code == 200, resp.text
    assert (resp.json()["run"]["status"], resp.json()["run"]["asking"]) == ("focus", "q1")
    assert (await _state(client, bundle, "text"))["status"] == "paused"

    body = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        "Усі 1000",
        route={"field:facts.q1": True},
        extract={"facts__q1": "Усі 1000"},
        rules={"filter:facts.q1.sum": True},
        reply="Got it. So, which angle do we take?",
        scenario=None,
    )
    # the facts wait again, the text has the dialogue back and asks its question
    assert (await _state(client, bundle, "facts"))["status"] == "waiting"
    run = body["run"]
    assert (run["scenario"], run["status"], run["asking"]) == ("text", "focus", "angle")
    assert _step(fake_llm)["ask_next"]["question"] == "Which angle do we take?"
