"""The full turn (rewrite → language → route → respond) over POST /v1/messages:
the seeded legacy prompts, the fake LLM and FakeDecider instead of Jev."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.core.pipeline import full_pipeline
from app.core.respond import HANDOVER_RULE
from app.db.models import (
    Conversation,
    Decision,
    DecisionProvider,
    LlmCall,
    LlmRole,
    Turn,
    TurnStatus,
)
from app.decisions.fake import FakeDecider
from app.prompts.seed import seed_prompts
from app.prompts.store import save_version
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant

pytestmark = pytest.mark.db

ROLES = (LlmRole.REPLY, LlmRole.REWRITE, LlmRole.DECIDE)
LANGUAGES = {"languages": ["uk", "en"], "reply_language": {"uk": "uk", "ru": "uk", "*": "en"}}


def _msg(text: str, external_id: str = "c1") -> dict:
    return {"contact": {"external_id": external_id, "name": "Olha"}, "text": text}


def _rewrite(*questions: str, language: str = "uk") -> dict:
    return chat_reply(json.dumps({"question": list(questions), "language": language}))


def _user_json(request: dict) -> dict:
    return json.loads(request["messages"][-1]["content"])


async def _turn(sm, turn_id: str) -> Turn:
    async with sm() as session:
        turn = await session.get(Turn, uuid.UUID(turn_id))
        assert turn is not None
        return turn


async def _conversation(sm, conversation_id: str) -> Conversation:
    async with sm() as session:
        conv = await session.get(Conversation, uuid.UUID(conversation_id))
        assert conv is not None
        return conv


async def _calls(sm, turn_id: str) -> list[LlmCall]:
    async with sm() as session:
        query = select(LlmCall).where(LlmCall.turn_id == uuid.UUID(turn_id))
        return list((await session.execute(query)).scalars())


def _step(turn: Turn, name: str) -> dict:
    return next(s for s in turn.trace["steps"] if s["step"] == name)


@pytest.fixture
def decider(services) -> FakeDecider:
    fake = FakeDecider({"service_type": "none", "wants_human": False})
    services.extra["pipeline"] = full_pipeline
    services.extra["decider"] = fake
    return fake


@pytest.fixture
async def bundle(services, decider):
    async with services.sm() as session, session.begin():
        await seed_prompts(session)
    await make_model(services.sm, "openai/a", roles=ROLES)
    return await make_tenant(services.sm, settings=LANGUAGES)


async def test_normal_reply(client, services, fake_llm, bundle):
    fake_llm.push(_rewrite("Скільки коштує манікюр?"), chat_reply("Манікюр коштує 500 грн."))
    resp = await client.post("/v1/messages", json=_msg("а скільки манік?"), headers=bundle.auth)

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "Манікюр коштує 500 грн."
    assert body["handover"] is False

    turn = await _turn(services.sm, body["turn_id"])
    assert turn.status == TurnStatus.DONE
    assert turn.input_text == "а скільки манік?"
    assert turn.rewritten == "Скільки коштує манікюр?"
    assert turn.route["service_type"] == "none"
    assert [s["step"] for s in turn.trace["steps"]] == ["rewrite", "route", "respond"]

    calls = await _calls(services.sm, body["turn_id"])
    assert sorted((c.role, c.prompt_key) for c in calls) == sorted(
        [(LlmRole.REWRITE, "rewrite"), (LlmRole.REPLY, "user_prompt")]
    )
    assert _step(turn, "rewrite")["llm_call_id"] in {str(c.id) for c in calls}

    # the reply model answers the rewritten question, not the raw text
    rewrite_request, reply_request = fake_llm.requests
    assert rewrite_request["response_format"]["json_schema"]["name"] == "rewrite"
    assert reply_request["messages"][0]["role"] == "system"
    assert _user_json(reply_request)["user_question"] == "Скільки коштує манікюр?"


async def test_greeting_uses_the_service_prompt(client, services, fake_llm, bundle, decider):
    decider.rules["service_type"] = "greeting_only"
    fake_llm.push(_rewrite("Привіт"), chat_reply("Вітаю! Чим можу допомогти?"))
    resp = await client.post("/v1/messages", json=_msg("Привіт!"), headers=bundle.auth)

    body = resp.json()
    assert body["reply"] == "Вітаю! Чим можу допомогти?"
    # a service reply sees the client's own words
    assert fake_llm.requests[1]["messages"][-1] == {"role": "user", "content": "Привіт!"}

    turn = await _turn(services.sm, body["turn_id"])
    assert turn.route["service_type"] == "greeting_only"
    assert _step(turn, "respond")["service"] is True
    calls = await _calls(services.sm, body["turn_id"])
    assert {c.prompt_key for c in calls} == {"rewrite", "service_greeting_only"}


async def test_wants_human_sets_handover(client, services, fake_llm, bundle, decider):
    decider.rules["wants_human"] = True
    fake_llm.push(_rewrite("Покличте менеджера"), chat_reply("Зараз покличу менеджера, зачекайте."))
    resp = await client.post("/v1/messages", json=_msg("покличте живу людину"), headers=bundle.auth)

    body = resp.json()
    assert body["handover"] is True
    assert _user_json(fake_llm.requests[1])["handover_note"] == HANDOVER_RULE

    conv = await _conversation(services.sm, body["conversation_id"])
    assert conv.handover is True
    assert conv.handover_at is not None


async def test_route_sees_only_the_rewritten_text(client, services, fake_llm, bundle, decider):
    decider.rules["service_type"] = "greeting_only"
    fake_llm.push(_rewrite("Привіт"), chat_reply("Вітаю!"))
    await client.post("/v1/messages", json=_msg("Привітики"), headers=bundle.auth)

    decider.rules["service_type"] = "none"
    fake_llm.push(_rewrite("Скільки коштує манікюр?"), chat_reply("500 грн."))
    await client.post("/v1/messages", json=_msg("а скільки манік?"), headers=bundle.auth)

    state, questions = decider.calls[1]
    assert "Скільки коштує манікюр?" in state
    # neither the raw text nor the history goes to the decider
    for text in ("манік?", "Привітики", "Вітаю!"):
        assert text not in state
    assert [q.key for q in questions] == ["service_type", "wants_human"]


@pytest.mark.parametrize(
    ("text", "detected", "client_language", "reply_language", "answer_line"),
    [
        ("Сколько стоит маникюр?", "ru", "ru", "uk", "Give the answer in Ukrainian language"),
        ("Wie viel kostet eine Maniküre?", "de", "de", "en", "Give the answer in English language"),
        ("Скільки коштує манікюр?", "uk", "uk", "uk", "Give the answer in Ukrainian language"),
        ("How much is a manicure?", "en", "en", "en", "Give the answer in English language"),
    ],
)
async def test_reply_language_follows_the_tenant_rule(
    client,
    services,
    fake_llm,
    bundle,
    text,
    detected,
    client_language,
    reply_language,
    answer_line,
):
    fake_llm.push(_rewrite("Price of a manicure?", language=detected), chat_reply("500 UAH"))
    resp = await client.post("/v1/messages", json=_msg(text), headers=bundle.auth)
    body = resp.json()

    conv = await _conversation(services.sm, body["conversation_id"])
    assert (conv.client_language, conv.reply_language) == (client_language, reply_language)
    turn = await _turn(services.sm, body["turn_id"])
    assert (turn.client_language, turn.reply_language) == (client_language, reply_language)
    assert turn.trace["language"] == {
        "detected": detected,
        "client": client_language,
        "reply": reply_language,
    }
    assert _user_json(fake_llm.requests[1])["answer_language"] == answer_line


async def test_short_message_keeps_the_language(client, services, fake_llm, bundle):
    fake_llm.push(_rewrite("Price of a manicure?", language="de"), chat_reply("500 UAH"))
    first = await client.post(
        "/v1/messages", json=_msg("Wie viel kostet eine Maniküre?"), headers=bundle.auth
    )
    # "ok" says nothing about the language, even if the model calls it English
    fake_llm.push(_rewrite("Ok", language="en"), chat_reply("Anything else?"))
    second = await client.post("/v1/messages", json=_msg("ok"), headers=bundle.auth)

    conv = await _conversation(services.sm, second.json()["conversation_id"])
    assert conv.id == uuid.UUID(first.json()["conversation_id"])
    assert (conv.client_language, conv.reply_language) == ("de", "en")
    # the second rewrite already works in the reply language of the first turn
    rewrite_request = fake_llm.requests[2]
    assert json.loads(rewrite_request["messages"][0]["content"])["answer_language"] == (
        "Give the answer in English language"
    )


async def test_decisions_are_logged_with_the_turn(client, services, fake_llm, bundle):
    fake_llm.push(_rewrite("Скільки коштує манікюр?"), chat_reply("500 грн."))
    resp = await client.post("/v1/messages", json=_msg("скільки?"), headers=bundle.auth)
    turn_id = uuid.UUID(resp.json()["turn_id"])

    async with services.sm() as session:
        query = select(Decision).where(Decision.turn_id == turn_id)
        rows = list((await session.execute(query)).scalars())
    assert {r.key for r in rows} == {"service_type", "wants_human"}
    assert {r.provider for r in rows} == {DecisionProvider.FAKE}
    assert len({r.batch_id for r in rows}) == 1

    turn = await _turn(services.sm, str(turn_id))
    route_step = _step(turn, "route")
    assert sorted(route_step["decision_ids"]) == sorted(str(r.id) for r in rows)
    assert route_step["answers"]["service_type"]["value"] == "none"


async def test_tenant_prompt_wins(client, services, fake_llm, bundle):
    async with services.sm() as session, session.begin():
        await save_version(
            session,
            key="system_prompt",
            body="Ти — помічник %COMPANY_TITLE%.",
            tenant_id=bundle.tenant_id,
        )
    fake_llm.push(_rewrite("Скільки коштує манікюр?"), chat_reply("500 грн."))
    await client.post("/v1/messages", json=_msg("скільки?"), headers=bundle.auth)
    assert fake_llm.requests[1]["messages"][0] == {
        "role": "system",
        "content": "Ти — помічник Acme.",
    }


async def test_missing_prompts_fail_the_turn(client, services, fake_llm, decider):
    # no seed-prompts: the turn cannot start
    await make_model(services.sm, "openai/a", roles=ROLES)
    bundle = await make_tenant(services.sm)
    resp = await client.post("/v1/messages", json=_msg("привіт"), headers=bundle.auth)

    assert resp.status_code == 500
    assert resp.json()["error"] == {"code": "prompt.not_found", "params": {"key": "rewrite"}}
    assert fake_llm.requests == []
    assert decider.calls == []

    async with services.sm() as session:
        (turn,) = (await session.execute(select(Turn))).scalars().all()
    assert turn.status == TurnStatus.FAILED
    assert "prompt.not_found" in (turn.error or "")
