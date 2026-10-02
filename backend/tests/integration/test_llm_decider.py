"""LLMDecider (the fallback for Jev) on the fake LLM backend and a real llm_calls log."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db.models import (
    Decision,
    DecisionProvider,
    DecisionReason,
    LlmCall,
    LlmRole,
)
from app.decisions.engine import DecideContext
from app.decisions.llm_decider import PROMPT_KEY, LLMDecider
from app.decisions.log import save_decisions
from app.errors import AppError
from tests.conftest import chat_reply
from tests.decision_helpers import choice, score, yes_no
from tests.factories import make_model

pytestmark = pytest.mark.db

STATE = "Client's latest message:\nHello! Can I talk to a manager?"


def _reply(**answers) -> dict:
    return chat_reply(json.dumps(answers), tokens_in=300, tokens_out=40, cost=0.0002)


@pytest.fixture
async def decider(services):
    await make_model(services.sm, "openai/judge", roles=(LlmRole.DECIDE,))
    return LLMDecider(services.llm)


async def test_answers_are_parsed(decider, fake_llm):
    questions = [
        choice("service_type", "none", "greeting_only"),
        yes_no("wants_human"),
        score("mood", 3),
    ]
    fake_llm.push(
        _reply(
            q1={"value": "greeting_only", "confidence": 0.8},
            q2={"value": True, "confidence": 1.7},  # clamped to 1
            q3={"value": 2, "confidence": "oops"},  # not a number → 0
        )
    )
    result = await decider.decide(STATE, questions, DecideContext(tenant_id=None))

    assert result.value("service_type") == "greeting_only"
    assert result.value("wants_human") is True
    assert result.value("mood") == 2
    assert result.answers["service_type"].confidence == 0.8
    assert result.answers["wants_human"].confidence == 1.0
    assert result.answers["mood"].confidence == 0.0
    assert {a.provider for a in result.answers.values()} == {DecisionProvider.LLM}
    assert {a.reason for a in result.answers.values()} == {DecisionReason.PRIMARY}
    assert {a.model for a in result.answers.values()} == {"openai/judge"}


async def test_request_uses_short_names_and_a_strict_schema(decider, fake_llm):
    questions = [yes_no("start:booking.wants_booking"), choice("service_type", "none", "x")]
    fake_llm.push(
        _reply(q1={"value": False, "confidence": 0.9}, q2={"value": "none", "confidence": 0.9})
    )
    await decider.decide(STATE, questions, DecideContext(tenant_id=None))

    request = fake_llm.requests[0]
    assert request["model"] == "openai/judge"
    schema = request["response_format"]["json_schema"]
    assert schema["name"] == "decisions"
    assert schema["schema"]["required"] == ["q1", "q2"]
    assert schema["schema"]["properties"]["q1"]["properties"]["value"] == {"type": "boolean"}
    assert schema["schema"]["properties"]["q2"]["properties"]["value"] == {
        "type": "string",
        "enum": ["none", "x"],
    }
    user = json.loads(request["messages"][-1]["content"])
    assert user["state"] == STATE
    assert set(user["questions"]) == {"q1", "q2"}
    assert user["questions"]["q1"]["criteria"] == {"true": "yes", "false": "no"}


async def test_rows_carry_cost_once_and_link_the_llm_call(decider, fake_llm, services):
    questions = [yes_no("a"), yes_no("b")]
    fake_llm.push(
        _reply(q1={"value": True, "confidence": 0.9}, q2={"value": False, "confidence": 0.6})
    )
    result = await decider.decide(
        STATE, questions, DecideContext(tenant_id=None), reason=DecisionReason.BELOW_THRESHOLD
    )
    await save_decisions(services.sm, result.rows)

    async with services.sm() as session:
        (call,) = (await session.execute(select(LlmCall))).scalars().all()
        rows = list((await session.execute(select(Decision).order_by(Decision.key))).scalars())
    assert call.role == LlmRole.DECIDE
    assert call.prompt_key == PROMPT_KEY

    assert [r.key for r in rows] == ["a", "b"]
    first, second = rows
    assert first.batch_id == second.batch_id
    assert first.cost == Decimal("0.0002")
    assert second.cost == 0
    assert (first.tokens_in, second.tokens_in) == (300, 0)
    assert {r.llm_call_id for r in rows} == {call.id}
    assert {r.reason for r in rows} == {DecisionReason.BELOW_THRESHOLD}
    assert {r.provider for r in rows} == {DecisionProvider.LLM}
    assert first.answer == {"value": True, "confidence": 0.9}
    assert first.confidence == 0.9
    assert result.answers["a"].decision_id == first.id


async def test_value_outside_the_options_is_an_error(decider, fake_llm):
    fake_llm.push(_reply(q1={"value": "weather", "confidence": 0.9}))
    with pytest.raises(AppError) as err:
        await decider.decide(
            STATE, [choice("service_type", "none", "greeting_only")], DecideContext(tenant_id=None)
        )
    assert err.value.code == "decisions.bad_output"
    assert err.value.params == {"key": "service_type", "value": '"weather"'}


async def test_missing_answer_is_an_error(decider, fake_llm):
    fake_llm.push(_reply(q1={"value": True, "confidence": 0.9}))
    with pytest.raises(AppError) as err:
        await decider.decide(STATE, [yes_no("a"), yes_no("b")], DecideContext(tenant_id=None))
    assert err.value.code == "decisions.bad_output"
    assert err.value.params["key"] == "b"


async def test_broken_json_is_retried(decider, fake_llm):
    fake_llm.push(chat_reply("not json"), _reply(q1={"value": True, "confidence": 0.7}))
    result = await decider.decide(STATE, [yes_no("a")], DecideContext(tenant_id=None))
    assert result.value("a") is True
    assert len(fake_llm.requests) == 2


async def test_broken_json_every_time_is_unavailable(decider, fake_llm):
    fake_llm.default = chat_reply("not json")
    with pytest.raises(AppError) as err:
        await decider.decide(STATE, [yes_no("a")], DecideContext(tenant_id=None))
    assert err.value.code == "llm.unavailable"
    assert len(fake_llm.requests) == 3  # 1 call + 2 retries


async def test_no_questions_no_call(decider, fake_llm):
    result = await decider.decide(STATE, [], DecideContext(tenant_id=None))
    assert result.answers == {}
    assert fake_llm.requests == []
