"""Several scenarios of one conversation led by the caller over the public API, on the full
pipeline: parts of an interview opened paused (`paused: true`) that listen to every message,
the queue (`queue`) that gives the dialogue to the next part in order, the dialogue handed
to a chosen part (POST /v1/runs/{id}/focus), a start with the answers the caller already has
(`answers`, checked by the questions' filters), and scenarios only the caller starts
(`start_by_caller`). Fake LLM (rewrite, extract, reply in that order), FakeDecider — like
tests/integration/test_api_runs_by_caller.py."""

import json

import pytest

from app.core.pipeline import full_pipeline
from app.db.models import LlmRole
from app.decisions.fake import FakeDecider
from app.prompts.seed import seed_prompts
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import ROUTE_DEFAULTS, install_scenario

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT)
LANGUAGES = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}
CONTACT = {"external_id": "new:1", "name": "Olha"}
CONFIRM = {"question": {"uk": "Записала. Все вірно?", "en": "Noted. All correct?"}}

WHO = {"key": "who", "question": {"uk": "Хто ви?", "en": "Who are you?"}}
OFFER = {
    "key": "offer",
    "question": {"uk": "Що ви продаєте?", "en": "What do you sell?"},
    "filters": [{"key": "named", "ask": {"uk": "Названо продукт?", "en": "Named?"}}],
}
PRICE = {"key": "price", "question": {"uk": "Скільки коштує?", "en": "How much is it?"}}
CLIENTS = {"key": "clients", "question": {"uk": "Хто ваші клієнти?", "en": "Who are they?"}}

ABOUT = "Я бариста, 10 років, тепер навчаю власників кав'ярень"
OWNERS = "власники кав'ярень"


def part(key, queue, *fields, **over):
    data = {
        "key": key,
        "title": {"uk": key, "en": key},
        "language": "uk",
        "start": {"questions": [{"key": "wants", "ask": {"uk": "Хоче?", "en": "Wants?"}}]},
        "start_by_caller": True,
        "only_scenario": True,
        "queue": queue,
        "confirm": CONFIRM,
        "fields": list(fields),
    }
    data.update(over)
    return data


PARTS = (part("founder", 1, WHO), part("offer", 2, OFFER, PRICE), part("audience", 3, CLIENTS))


async def _setup(services, *scenarios):
    decider = FakeDecider(dict(ROUTE_DEFAULTS))
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    bundle = await make_tenant(services.sm, settings=LANGUAGES)
    for data in scenarios or PARTS:
        await install_scenario(services.sm, bundle.tenant_id, bundle.channel_id, data)
    return bundle, decider


async def _post(client, bundle, path, **body):
    return await client.post(path, json=body, headers=bundle.auth)


async def _start(client, bundle, scenario, contact=CONTACT, **body):
    resp = await _post(
        client, bundle, "/v1/runs/start", contact=contact, scenario=scenario, language="en", **body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _say(client, bundle, decider, fake_llm, text, *, route, reply, extract=None):
    """A message of the client: rewrite → (extract) → reply; the decider answers the route,
    the filters and the confirmation. Returns the run in the focus after the turn."""
    decider.rules = {**ROUTE_DEFAULTS, **route}
    script = [chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False))]
    if extract is not None:
        script.append(chat_reply(json.dumps(extract, ensure_ascii=False)))
    fake_llm.push(*script, chat_reply(reply))
    resp = await _post(client, bundle, "/v1/runs/message", contact=CONTACT, text=text)
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    return resp.json()["run"]


async def _state(client, bundle, scenario):
    resp = await _post(client, bundle, "/v1/runs/state", contact=CONTACT, scenario=scenario)
    assert resp.status_code == 200, resp.text
    return resp.json()["run"]


def _fields(run: dict) -> dict[str, dict]:
    return {f["key"]: f for f in run["fields"]}


def _step(fake_llm) -> dict:
    """The `scenario_step` of the last reply request."""
    return json.loads(fake_llm.requests[-1]["messages"][-1]["content"])["scenario_step"]


async def _open_parts(client, bundle):
    founder = (await _start(client, bundle, "founder"))["run"]
    offer = (await _start(client, bundle, "offer", paused=True))["run"]
    audience = (await _start(client, bundle, "audience", paused=True))["run"]
    return founder, offer, audience


# --- paused parts and the queue ---------------------------------------------------------------


async def test_a_paused_part_listens_and_the_queue_gives_the_dialogue_in_order(
    services, client, fake_llm
):
    bundle, decider = await _setup(services)
    founder, offer, audience = await _open_parts(client, bundle)
    assert (founder["status"], founder["asking"]) == ("focus", "who")
    # the other parts open while the founder talks: paused, asking nothing, no model call
    assert (offer["status"], offer["asking"]) == ("paused", None)
    assert (audience["status"], audience["asking"]) == ("paused", None)
    assert fake_llm.calls == 0

    # the client answers the founder and says something about the audience
    run = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        ABOUT,
        route={"field:founder.who": True, "field:audience.clients": True},
        extract={"founder__who": ABOUT, "audience__clients": OWNERS},
        reply="Noted. All correct?",
    )
    assert (run["scenario"], run["asking"]) == ("founder", "@confirm")
    audience = await _state(client, bundle, "audience")
    assert audience["status"] == "paused"
    assert (_fields(audience)["clients"]["state"], _fields(audience)["clients"]["value"]) == (
        "filled",
        OWNERS,
    )
    # paused parts are asked about the message, their start questions never
    assert "field:audience.clients" in decider.asked()
    assert not [k for k in decider.asked() if k.startswith("start:")]

    # «yes»: the founder completes; the dialogue goes to the next part in order — not to the
    # audience, though the client answered it last
    run = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        "Так, вірно",
        route={"confirm:founder": "yes"},
        reply="Saved. What do you sell?",
    )
    assert (run["scenario"], run["status"], run["asking"]) == ("offer", "focus", "offer")
    assert (await _state(client, bundle, "founder"))["status"] == "completed"
    assert (await _state(client, bundle, "audience"))["status"] == "paused"
    assert _step(fake_llm)["ask_next"]["question"] == "What do you sell?"


async def test_a_start_is_refused_while_another_part_talks_unless_paused(services, client):
    bundle, _ = await _setup(services)
    await _start(client, bundle, "founder")
    resp = await _post(
        client, bundle, "/v1/runs/start", contact=CONTACT, scenario="offer", language="en"
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "run.other_in_focus"


# --- the dialogue to a chosen part ------------------------------------------------------------


async def test_a_chosen_part_takes_the_dialogue_and_gives_it_back_to_the_queue(
    services, client, fake_llm
):
    bundle, decider = await _setup(services)
    _, offer, audience = await _open_parts(client, bundle)

    # «go through this part»: the founder is put aside, the offer asks in the bot's words
    fake_llm.push(chat_reply("Let us talk about what you sell. What is it?"))
    resp = await _post(client, bundle, f"/v1/runs/{offer['id']}/focus")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["run"]["status"], body["run"]["asking"]) == ("focus", "offer")
    assert body["usage"] is not None and body["usage"]["calls"][0]["role"] == "reply"
    assert _step(fake_llm)["ask_next"]["question"] == "What do you sell?"
    assert (await _state(client, bundle, "founder"))["status"] == "paused"

    # the run has the dialogue already: nothing changes, no model call
    calls = fake_llm.calls
    again = await _post(client, bundle, f"/v1/runs/{offer['id']}/focus")
    assert (again.status_code, again.json()["usage"]) == (200, None)
    assert fake_llm.calls == calls

    # a part that knows all its answers asks «is everything correct?» at once
    await _say(
        client,
        bundle,
        decider,
        fake_llm,
        OWNERS,
        route={"field:audience.clients": True},
        extract={"audience__clients": OWNERS},
        reply="And what do you sell?",
    )
    fake_llm.push(chat_reply("You teach coffee shop owners. Noted. All correct?"))
    resp = await _post(client, bundle, f"/v1/runs/{audience['id']}/focus")
    assert resp.json()["run"]["asking"] == "@confirm"
    assert _step(fake_llm)["confirm"]
    assert (await _state(client, bundle, "offer"))["status"] == "paused"

    # confirmed: the dialogue goes back to the smallest place left — the founder
    run = await _say(
        client,
        bundle,
        decider,
        fake_llm,
        "Так",
        route={"confirm:audience": "yes"},
        reply="Who are you?",
    )
    assert (run["scenario"], run["asking"]) == ("founder", "who")
    assert (await _state(client, bundle, "audience"))["status"] == "completed"


async def test_a_closed_run_does_not_take_the_dialogue(services, client, fake_llm):
    bundle, decider = await _setup(services)
    founder, _, _ = await _open_parts(client, bundle)
    await _say(
        client,
        bundle,
        decider,
        fake_llm,
        ABOUT,
        route={"field:founder.who": True},
        extract={"founder__who": ABOUT},
        reply="Noted. All correct?",
    )
    await _say(
        client, bundle, decider, fake_llm, "Так", route={"confirm:founder": "yes"}, reply="Next."
    )
    resp = await _post(client, bundle, f"/v1/runs/{founder['id']}/focus")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "run.not_open"


# --- a start with answers ---------------------------------------------------------------------


async def test_answers_of_the_start_are_checked_and_the_bot_asks_what_is_missing(
    services, client, fake_llm
):
    bundle, decider = await _setup(services)
    decider.rules = {**ROUTE_DEFAULTS, "filter:offer.offer.named": True}
    fake_llm.push(chat_reply("I remember you teach baristas. How much is the course?"))
    body = await _start(client, bundle, "offer", answers={"offer": "Курси для барист"})

    run = body["run"]
    assert (run["status"], run["asking"]) == ("focus", "price")
    offer = _fields(run)["offer"]
    assert (offer["state"], offer["value"]) == ("filled", "Курси для барист")
    assert decider.asked() == ["filter:offer.offer.named"]
    # the check and the bot turn are paid apart, both come back
    assert body["check_usage"]["calls"][0]["kind"] == "decisions"
    assert body["usage"]["calls"][0]["role"] == "reply"
    step = _step(fake_llm)
    assert step["ask_next"]["question"] == "How much is it?"
    assert {"question": "What do you sell?", "answer": "Курси для барист"}.items() <= (
        step["known"][0].items()
    )


async def test_an_answer_that_fails_its_check_is_kept_and_asked_again(services, client, fake_llm):
    bundle, decider = await _setup(services)
    decider.rules = {**ROUTE_DEFAULTS, "filter:offer.offer.named": False}
    fake_llm.push(chat_reply("You mentioned courses. Which ones exactly?"))
    body = await _start(client, bundle, "offer", answers={"offer": "Щось для кав'ярень"})

    run = body["run"]
    assert (run["status"], run["asking"]) == ("focus", "offer")
    offer = _fields(run)["offer"]
    assert (offer["state"], offer["value"]) == ("partial", "Щось для кав'ярень")
    assert body["usage"] is not None


async def test_answers_with_a_paused_start_are_kept_and_nothing_is_said(services, client, fake_llm):
    bundle, _ = await _setup(services)
    await _start(client, bundle, "founder")
    body = await _start(client, bundle, "audience", paused=True, answers={"clients": OWNERS})
    run = body["run"]
    assert (run["status"], run["asking"], body["usage"]) == ("paused", None, None)
    assert _fields(run)["clients"]["state"] == "filled"
    assert body["check_usage"] is None  # the question has no filters: nothing to ask
    assert fake_llm.calls == 0


async def test_wrong_answers_of_the_start_are_refused(services, client):
    bundle, _ = await _setup(services)
    for answers, skip, code in (
        ({"nope": "x"}, [], "run.unknown_fields"),
        ({"offer": "  "}, [], "check.empty_answer"),
        ({"offer": "x"}, ["offer"], "run.answer_skipped"),
    ):
        resp = await _post(
            client,
            bundle,
            "/v1/runs/start",
            contact=CONTACT,
            scenario="offer",
            answers=answers,
            skip=skip,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["code"] == code


# --- started by the caller alone --------------------------------------------------------------


async def test_a_scenario_started_by_the_caller_never_starts_by_the_clients_words(
    services, client, fake_llm
):
    bundle, decider = await _setup(services, part("founder", 1, WHO, only_scenario=None))
    decider.rules = {**ROUTE_DEFAULTS, "start:*": True}
    fake_llm.push(
        chat_reply(json.dumps({"question": ["Хочу"], "language": "uk"}, ensure_ascii=False)),
        chat_reply("Hello!"),
    )
    resp = await _post(client, bundle, "/v1/runs/message", contact=CONTACT, text="Хочу")
    assert resp.status_code == 200, resp.text
    assert resp.json()["run"] is None
    assert "start:founder.wants" not in decider.asked()
