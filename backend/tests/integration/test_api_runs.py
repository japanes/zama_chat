"""Scenario runs over the public API (POST /v1/runs/…): start a given scenario, a message
with the run and the turn's cost in the answer, state, skip, confirm, the pinned reply
language; answers collected outside a dialogue checked by the filters of their questions
(POST /v1/scenarios/{key}/check). Fake LLM, FakeDecider — like
tests/integration/test_scenario_flow.py."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.api.v1.runs import CHECK_ANSWER_MAX, _usage
from app.core.language import REPLY_LANGUAGE_PIN
from app.core.pipeline import full_pipeline
from app.db.models import (
    Contact,
    Conversation,
    Decision,
    DecisionProvider,
    LlmRole,
    Message,
    MessageRole,
    RunStatus,
    ScenarioRun,
)
from app.decisions.engine import DecisionSet
from app.decisions.fake import FakeDecider
from app.decisions.limits import REQUEST_TOKENS, request_tokens
from app.prompts.seed import seed_prompts
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant
from tests.scenario_helpers import (
    ROUTE_DEFAULTS,
    deliveries_of,
    install_scenario,
    interview_data,
    make_endpoint,
    runs_of,
)

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE, LlmRole.EXTRACT)
LANGUAGES = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}
CONTACT = {"external_id": "dna:1", "name": "Olha"}
ALL = ["niche", "offer", "positioning"]


async def _setup(services, data=None, **tenant):
    bundle = await make_tenant(services.sm, settings=LANGUAGES, **tenant)
    await install_scenario(
        services.sm, bundle.tenant_id, bundle.channel_id, data or interview_data()
    )
    await make_endpoint(services, bundle.tenant_id, url="http://test/dev/sink/results")
    return bundle


async def _post(client, bundle, path, **body):
    return await client.post(path, json=body, headers=bundle.auth)


async def _start(client, bundle, **body):
    resp = await _post(
        client, bundle, "/v1/runs/start", contact=CONTACT, scenario="interview", **body
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _fields(run: dict) -> dict[str, dict]:
    return {f["key"]: f for f in run["fields"]}


async def _bot_texts(sm, conversation_id) -> list[str]:
    async with sm() as session:
        rows = await session.execute(
            select(Message.text)
            .where(Message.conversation_id == conversation_id, Message.role == MessageRole.BOT)
            .order_by(Message.created_at, Message.id)
        )
        return [text for (text,) in rows]


# --- the scenario's questions -----------------------------------------------------------------


async def test_scenario_lists_its_questions_before_a_run_exists(services, client, fake_llm):
    bundle = await _setup(services)
    resp = await client.get("/v1/scenarios/interview", headers=bundle.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["key"], body["language"], body["title"]) == ("interview", "uk", "Інтерв'ю")
    assert [f["key"] for f in body["fields"]] == ALL
    assert body["fields"][0] == {
        "key": "niche",
        "type": "text",
        "title": None,
        "question": "Яка у вас ніша?",
        "description": None,
        "filters": [{"key": "concrete", "ask": "Ніша конкретна?"}],
    }
    assert body["fields"][1]["filters"] == []
    assert (body["needs_confirm"], body["confirm_question"]) == (False, None)

    resp = await client.get("/v1/scenarios/interview?language=en", headers=bundle.auth)
    assert resp.json()["fields"][0]["question"] == "What is your niche?"
    assert resp.json()["fields"][0]["filters"] == [
        {"key": "concrete", "ask": "Is the niche specific?"}
    ]
    assert fake_llm.calls == 0
    async with services.sm() as session:
        assert (await session.execute(select(Conversation))).first() is None


async def test_scenario_unknown_key_and_language(services, client):
    bundle = await _setup(services)
    resp = await client.get("/v1/scenarios/nope", headers=bundle.auth)
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "scenario.not_found")
    resp = await client.get("/v1/scenarios/interview?language=de", headers=bundle.auth)
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "run.language_not_supported")


# --- answers collected outside a dialogue ------------------------------------------------------

NICHE = "filter:interview.niche.concrete"
FITS = "filter:interview.positioning.fits"


def _decider(services, rules, **kwargs):
    decider = FakeDecider(rules, **kwargs)
    services.extra["decider"] = decider
    return decider


async def _check(client, bundle, answers, **body):
    return await _post(
        client,
        bundle,
        "/v1/scenarios/interview/check",
        answers=[{"field": k, "value": v} for k, v in answers.items()],
        **body,
    )


async def test_check_says_which_answers_pass_the_filters_of_their_questions(
    services, client, fake_llm
):
    bundle = await _setup(services)
    decider = _decider(services, {NICHE: True, FITS: False})
    resp = await _check(
        client,
        bundle,
        {"niche": "торти на замовлення", "positioning": "ми найкращі", "offer": "торти"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["scenario"], body["version"], body["language"]) == ("interview", 1, "uk")
    by_field = {r["field"]: r for r in body["results"]}
    assert [r["field"] for r in body["results"]] == ["niche", "positioning", "offer"]
    assert by_field["niche"] == {
        "field": "niche",
        "passed": True,
        "failed": [],
        "unanswered": [],
        "filters": 1,
    }
    assert by_field["positioning"]["passed"] is False
    assert by_field["positioning"]["failed"] == [
        {"key": "fits", "ask": "Відмінність пасує?", "no": None}
    ]
    # a question without filters: nothing to ask, the answer is taken as it is
    assert by_field["offer"] == {
        "field": "offer",
        "passed": True,
        "failed": [],
        "unanswered": [],
        "filters": 0,
    }
    # one batch for all the answers, the same questions a turn asks
    assert decider.asked() == [NICHE, FITS]
    assert fake_llm.calls == 0

    # the decisions are in the log without a turn; nothing of a dialogue appeared
    async with services.sm() as session:
        rows = (await session.execute(select(Decision))).scalars().all()
        assert {(r.key, r.turn_id, str(r.batch_id)) for r in rows} == {
            (NICHE, None, body["id"]),
            (FITS, None, body["id"]),
        }
        assert {r.tenant_id for r in rows} == {bundle.tenant_id}
        for model in (Contact, Conversation, ScenarioRun):
            assert (await session.execute(select(model))).first() is None
    usage = body["usage"]
    assert [(c["kind"], c["role"], c["model"]) for c in usage["calls"]] == [
        ("decisions", "decide", "fake/decider")
    ]
    assert (usage["tokens_in"], usage["tokens_out"], usage["cost"]) == (0, 0, 0)


async def test_check_shows_the_decider_the_whole_answer_and_the_accepted_ones(services, client):
    bundle = await _setup(services)
    decider = _decider(services, {FITS: True})
    long_answer = "Ми печемо без цукру. " * 150  # longer than a turn shows (1000 characters)
    resp = await _check(
        client,
        bundle,
        {"positioning": long_answer},
        accepted={"niche": "торти на замовлення", "offer": "  "},
        language="en",
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["language"] == "en"
    state, questions = decider.calls[0]
    assert " ".join(long_answer.split()) in state
    assert "«What is your niche?»: «торти на замовлення»" in state
    assert "What do you sell?" not in state  # an empty accepted answer is no context
    assert "Does it fit?" in questions[0].instructions


async def test_check_an_answer_under_check_is_not_its_own_context(services, client):
    bundle = await _setup(services)
    decider = _decider(services, {NICHE: True})
    resp = await _check(client, bundle, {"niche": "нова ніша"}, accepted={"niche": "стара ніша"})
    assert resp.status_code == 200, resp.text
    assert "стара ніша" not in decider.calls[0][0]


async def test_check_an_unsure_answer_fails(services, client):
    bundle = await _setup(services)
    _decider(services, {NICHE: True}, unsure={NICHE: "ask_client"})
    resp = await _check(client, bundle, {"niche": "щось для людей"})
    assert resp.json()["results"][0]["passed"] is False
    assert [f["key"] for f in resp.json()["results"][0]["failed"]] == ["concrete"]


class SilentDecider:
    """Answers nothing: what is left of a batch when the decider dropped a question."""

    async def decide(self, state, questions, ctx):
        return DecisionSet()


async def test_check_names_the_filters_nobody_answered(services, client):
    bundle = await _setup(services)
    services.extra["decider"] = SilentDecider()
    resp = await _check(client, bundle, {"niche": "торти"})
    assert resp.status_code == 200, resp.text
    # a turn lets such an answer through; here it is "not checked", and that is not a pass
    assert resp.json()["results"][0] == {
        "field": "niche",
        "passed": False,
        "failed": [],
        "unanswered": ["concrete"],
        "filters": 1,
    }


async def test_check_gives_the_scenario_wording_of_a_failed_filter(services, client):
    data = interview_data()
    data["fields"][0]["filters"][0]["no"] = {"uk": "загальні слова", "en": "general words"}
    bundle = await _setup(services, data)
    _decider(services, {NICHE: False})
    resp = await _check(client, bundle, {"niche": "усе для всіх"}, language="en")
    assert resp.json()["results"][0]["failed"] == [
        {"key": "concrete", "ask": "Is the niche specific?", "no": "general words"}
    ]


async def test_check_without_filters_asks_nobody(services, client):
    bundle = await _setup(services)
    decider = _decider(services, {})
    resp = await _check(client, bundle, {"offer": "торти"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["usage"] is None
    assert resp.json()["results"][0]["passed"] is True
    assert decider.calls == []


# questions enough for their full answers (CHECK_ANSWER_MAX Cyrillic characters, about
# 9 000 tokens each) to overflow one request to the decider
_MANY = ["niche", "offer", "positioning"] + [f"extra_{i}" for i in range(4)]


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"answers": [{"field": "nope", "value": "x"}]}, 422, "run.unknown_fields"),
        (
            {"answers": [{"field": "niche", "value": "x"}], "accepted": {"nope": "x"}},
            422,
            "run.unknown_fields",
        ),
        (
            {"answers": [{"field": "niche", "value": "a"}, {"field": "niche", "value": "b"}]},
            422,
            "check.field_twice",
        ),
        ({"answers": [{"field": "niche", "value": "   "}]}, 422, "check.empty_answer"),
        (
            {"answers": [{"field": "niche", "value": "x"}], "language": "de"},
            422,
            "run.language_not_supported",
        ),
    ],
)
async def test_check_refuses_what_it_cannot_check(services, client, body, status, code):
    data = interview_data()
    data["fields"].extend(
        {"key": key, "question": {"uk": "Ще щось?", "en": "More?"}}
        for key in _MANY
        if key not in {"niche", "offer", "positioning"}
    )
    bundle = await _setup(services, data)
    decider = _decider(services, {"filter:*": True})
    resp = await _post(client, bundle, "/v1/scenarios/interview/check", **body)
    assert (resp.status_code, resp.json()["error"]["code"]) == (status, code), resp.text
    assert decider.calls == []


async def test_answers_longer_than_a_request_are_checked_in_several(services, client):
    data = interview_data()
    data["fields"].extend(
        {
            "key": key,
            "question": {"uk": "Ще щось?", "en": "More?"},
            "filters": [{"key": "ok", "ask": {"uk": "Відповідь по суті?", "en": "To the point?"}}],
        }
        for key in _MANY
        if key not in {"niche", "offer", "positioning"}
    )
    bundle = await _setup(services, data)
    # the last answer fails: its verdict must come back to its own question
    decider = _decider(
        services, {"filter:*": lambda q, state: not q.key.startswith("filter:interview.extra_3.")}
    )
    answers = {key: "я" * CHECK_ANSWER_MAX for key in _MANY}
    resp = await _check(client, bundle, answers)
    assert resp.status_code == 200, resp.text
    assert len(decider.calls) > 1
    assert all(request_tokens(state, qs) <= REQUEST_TOKENS for state, qs in decider.calls)
    # every answer is checked once, whole, next to its own filters
    asked = decider.asked()
    assert sorted(asked) == sorted(set(asked)) and len(asked) == len(_MANY) - 1
    for state, questions in decider.calls:
        for q in questions:
            field = q.key.split(".")[1]
            assert f"[interview.{field}]" in state
            assert "я" * CHECK_ANSWER_MAX in state
    results = {r["field"]: r["passed"] for r in resp.json()["results"]}
    assert results == {key: key != "extra_3" for key in _MANY}
    assert resp.json()["usage"] is not None


async def test_check_only_text_questions(services, client):
    data = interview_data()
    data["fields"].append(
        {"key": "phone", "type": "phone", "question": {"uk": "Телефон?", "en": "Phone?"}}
    )
    bundle = await _setup(services, data)
    resp = await _check(client, bundle, {"phone": "+380501112233"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "check.not_a_text_field")


async def test_check_of_a_scenario_of_another_tenant_is_not_found(services, client):
    await _setup(services)
    other = await make_tenant(services.sm, slug="other", title="Other", settings=LANGUAGES)
    resp = await _check(client, other, {"niche": "торти"})
    assert (resp.status_code, resp.json()["error"]["code"]) == (404, "scenario.not_found")


# --- start ------------------------------------------------------------------------------------


async def test_start_creates_the_run_and_asks_the_first_question(services, client, fake_llm):
    bundle = await _setup(services)
    state = await _start(client, bundle, language="en", messages=10)
    run = state["run"]
    assert (run["scenario"], run["status"], run["asking"]) == ("interview", "focus", "niche")
    assert (run["language"], run["title"]) == ("en", "Interview")
    assert (run["needs_confirm"], run["confirmed"], run["confirm_question"]) == (False, True, None)
    assert [f["key"] for f in run["fields"]] == ALL
    assert _fields(run)["niche"] == {
        "key": "niche",
        "title": None,
        "question": "What is your niche?",
        "state": "empty",
        "value": None,
        "complete": False,
        "skipped": False,
        "reasks": 0,
        "stuck": False,
        # not answered yet: how the bot asked it is kept with the answer
        "asked": None,
        "hidden": False,
        "closed": False,
        "taken_as_said": False,
        "clarified": 0,
        "options": None,
        "added_at": None,
    }
    assert [(m["role"], m["text"]) for m in state["messages"]] == [("bot", "What is your niche?")]
    assert fake_llm.calls == 0

    [row] = await runs_of(services.sm, state["conversation_id"])
    assert (row.status, row.asking, row.reply_language) == (RunStatus.FOCUS, "niche", "en")
    assert [e["event"] for e in row.events] == ["started"]
    async with services.sm() as session:
        conv = await session.get(Conversation, row.conversation_id)
    assert conv.state[REPLY_LANGUAGE_PIN] == "en"
    assert conv.reply_language == "en"


@pytest.mark.parametrize(
    ("language", "speaks", "first"),
    [
        # the tenant's rules: ru → uk, uk → uk, any other → en
        ("ru", "uk", "Яка у вас ніша?"),
        ("uk", "uk", "Яка у вас ніша?"),
        ("de", "en", "What is your niche?"),
    ],
)
async def test_start_speaks_what_the_tenant_rule_gives_for_the_client_language(
    services, client, language, speaks, first
):
    bundle = await _setup(services)
    state = await _start(client, bundle, language=language, messages=10)
    assert state["run"]["language"] == speaks
    assert state["messages"][0]["text"] == first
    async with services.sm() as session:
        conv = await session.get(Conversation, state["conversation_id"])
    assert conv.state[REPLY_LANGUAGE_PIN] == speaks


async def test_start_again_returns_the_run_as_it_is(services, client):
    bundle = await _setup(services)
    first = await _start(client, bundle)
    again = await _start(client, bundle, skip=["niche"], messages=10)
    assert again["run"]["id"] == first["run"]["id"]
    assert again["run"]["asking"] == "niche"
    assert len(again["messages"]) == 1
    assert len(await runs_of(services.sm, first["conversation_id"])) == 1


async def test_start_without_a_language_speaks_the_scenario_language(services, client):
    bundle = await _setup(services)
    state = await _start(client, bundle, messages=10)
    assert state["run"]["language"] == "uk"
    assert state["messages"][0]["text"] == "Яка у вас ніша?"
    async with services.sm() as session:
        conv = await session.get(Conversation, state["conversation_id"])
    assert REPLY_LANGUAGE_PIN not in (conv.state or {})


async def test_start_closes_the_questions_the_caller_has_answers_to(services, client):
    bundle = await _setup(services)
    state = await _start(client, bundle, skip=["niche"], messages=10)
    run = state["run"]
    assert run["asking"] == "offer"
    niche = _fields(run)["niche"]
    assert (niche["state"], niche["value"], niche["complete"], niche["skipped"]) == (
        "filled",
        None,
        True,
        True,
    )
    assert state["messages"][0]["text"] == "Що ви продаєте?"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"scenario": "nope"}, 404, "scenario.not_found"),
        ({"scenario": "interview", "skip": ["nope"]}, 422, "run.unknown_fields"),
        ({"scenario": "interview", "language": "x"}, 422, "run.language_not_supported"),
    ],
)
async def test_start_refuses_what_it_does_not_know(services, client, body, status, code):
    bundle = await _setup(services)
    resp = await _post(client, bundle, "/v1/runs/start", contact=CONTACT, **body)
    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code
    async with services.sm() as session:
        assert (await session.execute(select(Conversation.id))).first() is None


async def test_a_scenario_switched_off_in_the_channel_does_not_start(services, client):
    bundle = await _setup(services)
    other = await make_tenant(services.sm, slug="other", title="Other", settings=LANGUAGES)
    resp = await _post(client, other, "/v1/runs/start", contact=CONTACT, scenario="interview")
    assert resp.status_code == 404
    assert bundle.tenant_id != other.tenant_id


# --- state ------------------------------------------------------------------------------------


async def test_state_of_an_unknown_contact_is_empty(services, client):
    bundle = await _setup(services)
    resp = await _post(client, bundle, "/v1/runs/state", contact=CONTACT, scenario="interview")
    assert resp.status_code == 200
    assert resp.json() == {"conversation_id": None, "run": None, "messages": []}


async def test_state_returns_the_run_and_its_messages(services, client):
    bundle = await _setup(services)
    started = await _start(client, bundle)
    resp = await _post(
        client, bundle, "/v1/runs/state", contact=CONTACT, scenario="interview", messages=5
    )
    state = resp.json()
    assert state["run"]["id"] == started["run"]["id"]
    assert [m["text"] for m in state["messages"]] == ["Яка у вас ніша?"]
    silent = await _post(client, bundle, "/v1/runs/state", contact=CONTACT, scenario="interview")
    assert silent.json()["messages"] == []


# --- skip -------------------------------------------------------------------------------------


async def test_skip_closes_the_asked_question_and_asks_the_next(services, client, fake_llm):
    bundle = await _setup(services)
    run_id = (await _start(client, bundle))["run"]["id"]
    resp = await _post(client, bundle, f"/v1/runs/{run_id}/skip", messages=10)
    assert resp.status_code == 200, resp.text
    state = resp.json()
    assert state["run"]["asking"] == "offer"
    assert _fields(state["run"])["niche"]["skipped"] is True
    assert [m["text"] for m in state["messages"]] == ["Яка у вас ніша?", "Що ви продаєте?"]
    assert fake_llm.calls == 0
    [row] = await runs_of(services.sm, state["conversation_id"])
    assert [(e["event"], e.get("field")) for e in row.events] == [
        ("started", None),
        ("skipped", "niche"),
    ]


async def test_skipping_another_question_leaves_the_asked_one_as_it_is(services, client):
    bundle = await _setup(services)
    run_id = (await _start(client, bundle))["run"]["id"]
    resp = await _post(client, bundle, f"/v1/runs/{run_id}/skip", field="offer", messages=10)
    assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    assert run["asking"] == "niche"
    assert _fields(run)["offer"]["skipped"] is True
    assert _fields(run)["niche"]["reasks"] == 0
    assert [m["text"] for m in resp.json()["messages"]] == ["Яка у вас ніша?"]


async def test_skipping_the_same_question_twice_changes_nothing(services, client):
    bundle = await _setup(services)
    run_id = (await _start(client, bundle))["run"]["id"]
    await _post(client, bundle, f"/v1/runs/{run_id}/skip", field="niche")
    resp = await _post(client, bundle, f"/v1/runs/{run_id}/skip", field="niche", messages=10)
    assert resp.status_code == 200
    assert resp.json()["run"]["asking"] == "offer"
    assert len(resp.json()["messages"]) == 2


async def test_skipping_every_question_completes_a_run_without_confirm(services, client):
    bundle = await _setup(services)
    state = await _start(client, bundle)
    run_id = state["run"]["id"]
    for _ in ALL:
        resp = await _post(client, bundle, f"/v1/runs/{run_id}/skip")
        assert resp.status_code == 200, resp.text
    run = resp.json()["run"]
    assert (run["status"], run["asking"]) == ("completed", None)
    assert run["finished_at"] is not None
    [row] = await runs_of(services.sm, state["conversation_id"])
    [delivery] = await deliveries_of(services.sm, row.id)
    assert delivery.payload["fields"] == {}
    assert delivery.payload["missing"] == ALL

    again = await _post(client, bundle, f"/v1/runs/{run_id}/skip")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "run.not_open"


async def test_skip_of_an_unknown_question_is_refused(services, client):
    bundle = await _setup(services)
    run_id = (await _start(client, bundle))["run"]["id"]
    resp = await _post(client, bundle, f"/v1/runs/{run_id}/skip", field="nope")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "run.unknown_fields"


async def test_another_tenant_does_not_see_the_run(services, client):
    bundle = await _setup(services)
    run_id = (await _start(client, bundle))["run"]["id"]
    other = await make_tenant(services.sm, slug="other", title="Other", settings=LANGUAGES)
    for action in ("skip", "confirm"):
        resp = await _post(client, other, f"/v1/runs/{run_id}/{action}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "run.not_found"


# --- confirm ----------------------------------------------------------------------------------

WITH_CONFIRM = {"confirm": {"question": {"uk": "Усе так?", "en": "All correct?"}}}


async def test_everything_collected_asks_to_confirm_and_confirm_completes(services, client):
    bundle = await _setup(services, interview_data(**WITH_CONFIRM))
    state = await _start(client, bundle, language="en", skip=ALL, messages=10)
    run = state["run"]
    assert (run["status"], run["asking"]) == ("focus", "@confirm")
    assert (run["needs_confirm"], run["confirmed"]) == (True, False)
    assert run["confirm_question"] == "All correct?"
    assert [m["text"] for m in state["messages"]] == ["All correct?"]

    resp = await _post(client, bundle, f"/v1/runs/{run['id']}/confirm")
    assert resp.status_code == 200, resp.text
    done = resp.json()["run"]
    assert (done["status"], done["asking"], done["confirmed"]) == ("completed", None, True)
    [row] = await runs_of(services.sm, state["conversation_id"])
    assert [e["event"] for e in row.events][-2:] == ["confirmed", "completed"]
    assert len(await deliveries_of(services.sm, row.id)) == 1

    again = await _post(client, bundle, f"/v1/runs/{run['id']}/confirm")
    assert again.status_code == 200
    assert len(await deliveries_of(services.sm, row.id)) == 1


async def test_confirm_before_every_question_is_closed_is_refused(services, client):
    bundle = await _setup(services, interview_data(**WITH_CONFIRM))
    state = await _start(client, bundle, skip=["niche"])
    resp = await _post(client, bundle, f"/v1/runs/{state['run']['id']}/confirm")
    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "run.not_collected",
        "params": {"missing": ["offer", "positioning"]},
    }
    [row] = await runs_of(services.sm, state["conversation_id"])
    assert row.status == RunStatus.FOCUS
    assert "confirmed_at" not in row.data


# --- a message --------------------------------------------------------------------------------


async def _pipeline(services):
    decider = FakeDecider(dict(ROUTE_DEFAULTS))
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = decider
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    return decider


async def test_a_message_returns_the_run_and_what_the_turn_cost(services, client, fake_llm):
    decider = await _pipeline(services)
    bundle = await _setup(services)
    await _start(client, bundle, language="en")

    text = "Я печу торти на замовлення"
    decider.rules = {**ROUTE_DEFAULTS, "field:interview.niche": True}
    fake_llm.push(
        # the client writes in Ukrainian; the tenant rule would answer in Ukrainian too
        chat_reply(json.dumps({"question": [text], "language": "uk"}, ensure_ascii=False)),
        chat_reply(json.dumps({"interview__niche": "торти на замовлення"}, ensure_ascii=False)),
        chat_reply("Got it. What do you sell?"),
    )
    resp = await _post(
        client, bundle, "/v1/runs/message", contact=CONTACT, text=text, scenario="interview"
    )
    assert resp.status_code == 200, resp.text
    assert not fake_llm.script, "unused LLM answers"
    body = resp.json()
    assert body["reply"] == "Got it. What do you sell?"
    run = body["run"]
    assert (run["status"], run["asking"], run["language"]) == ("focus", "offer", "en")
    assert _fields(run)["niche"]["value"] == "торти на замовлення"

    usage = body["usage"]
    assert usage["turn_id"] == body["turn_id"]
    assert [c["kind"] for c in usage["calls"]] == ["llm", "llm", "llm"]
    assert {c["role"] for c in usage["calls"]} == {"rewrite", "extract", "reply"}
    assert {c["model"] for c in usage["calls"]} == {"openai/a"}
    assert usage["tokens_in"] == sum(c["tokens_in"] for c in usage["calls"])
    assert usage["cost"] == pytest.approx(sum(c["cost"] for c in usage["calls"]))

    # the pinned language won over the tenant rule (uk → uk)
    async with services.sm() as session:
        conv = await session.get(Conversation, body["conversation_id"])
    assert (conv.client_language, conv.reply_language) == ("uk", "en")
    assert conv.state[REPLY_LANGUAGE_PIN] == "en"
    assert await _bot_texts(services.sm, conv.id) == [
        "What is your niche?",
        "Got it. What do you sell?",
    ]


async def test_the_usage_of_a_turn_says_where_its_time_went(services, client, fake_llm):
    """Its steps as the trace has them, when it began and ended, and the decision service's
    time: its requests one after another, each as long as the longest row of its batch."""
    decider = await _pipeline(services)
    bundle = await _setup(services)
    await _start(client, bundle, language="en")

    decider.rules = {**ROUTE_DEFAULTS, "field:interview.niche": True}
    fake_llm.push(
        chat_reply(json.dumps({"question": ["I bake cakes"], "language": "en"})),
        chat_reply(json.dumps({"interview__niche": "cakes"})),
        chat_reply("Got it. What do you sell?"),
    )
    resp = await _post(
        client,
        bundle,
        "/v1/runs/message",
        contact=CONTACT,
        text="I bake cakes",
        scenario="interview",
    )
    assert resp.status_code == 200, resp.text
    usage = resp.json()["usage"]
    steps = [s["step"] for s in usage["steps"]]
    assert steps[0] == "rewrite" and steps[-1] == "respond" and "route" in steps
    assert all(isinstance(s["ms"], int) and s["skipped"] is None for s in usage["steps"])
    assert usage["started_at"] and usage["finished_at"]

    turn_id = uuid.UUID(usage["turn_id"])
    one, two = uuid.uuid4(), uuid.uuid4()
    async with services.sm() as session, session.begin():
        for batch, key, ms in ((one, "a", 800), (one, "b", 800), (two, "c", 300)):
            session.add(
                Decision(
                    tenant_id=bundle.tenant_id,
                    turn_id=turn_id,
                    batch_id=batch,
                    key=key,
                    type="boolean",
                    provider=DecisionProvider.JEV,
                    model="typesafe-ai/jev",
                    latency_ms=ms,
                )
            )
    async with services.sm() as session:
        out = await _usage(session, turn_id)
    [jev] = [c for c in out.calls if c.kind == "decisions"]
    assert (jev.model, jev.latency_ms) == ("typesafe-ai/jev", 1100)


async def test_a_message_for_a_dialogue_that_is_not_running_is_refused(services, client, fake_llm):
    await _pipeline(services)
    bundle = await _setup(services)
    resp = await _post(
        client, bundle, "/v1/runs/message", contact=CONTACT, text="hi", scenario="interview"
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == {"code": "run.not_open", "params": {"status": None}}

    run_id = (await _start(client, bundle))["run"]["id"]
    for _ in ALL:
        await _post(client, bundle, f"/v1/runs/{run_id}/skip")
    resp = await _post(
        client, bundle, "/v1/runs/message", contact=CONTACT, text="hi", scenario="interview"
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["params"] == {"status": "completed"}
    assert fake_llm.calls == 0
    async with services.sm() as session:
        clients = await session.execute(
            select(Message.id).where(Message.role == MessageRole.CLIENT)
        )
        assert clients.first() is None
