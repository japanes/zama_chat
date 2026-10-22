from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core.routing import (
    ROUTE_QUESTIONS,
    SERVICE_TYPES,
    Route,
    route,
    route_from,
    route_questions,
)
from app.core.tenant_settings import TenantSettings
from app.db.models import DecisionProvider, DecisionType
from app.decisions.engine import Answer, DecisionSet
from app.decisions.fake import FakeDecider
from tests.turn_ctx import make_ctx


class FakeSession:
    def __init__(self, saved):
        self.saved = saved

    @asynccontextmanager
    async def begin(self):
        yield

    def add_all(self, rows):
        self.saved.extend(rows)


def fake_services(decider):
    """Just enough of Services for route(): the decider and a sessionmaker for the log."""
    saved = []

    @asynccontextmanager
    async def sm():
        yield FakeSession(saved)

    return SimpleNamespace(extra={"decider": decider}, sm=sm, saved=saved)


def answers(**values) -> DecisionSet:
    out = DecisionSet()
    for key, value in values.items():
        out.answers[key] = Answer(
            key=key,
            type=DecisionType.CHOICE,
            value=value,
            confidence=0.9,
            provider=DecisionProvider.FAKE,
            model="fake",
        )
    return out


def test_route_questions_in_english_by_default():
    questions = route_questions(None)
    assert [q.key for q in questions] == ["service_type", "wants_human"]
    assert questions == [
        ROUTE_QUESTIONS["service_type"]["en"],
        ROUTE_QUESTIONS["wants_human"]["en"],
    ]


def test_route_questions_in_ukrainian():
    questions = route_questions("uk")
    assert questions[0] is ROUTE_QUESTIONS["service_type"]["uk"]
    assert questions[1] is ROUTE_QUESTIONS["wants_human"]["uk"]


def test_unknown_question_language_falls_back_to_english():
    assert route_questions("xx") == route_questions("en")


@pytest.mark.parametrize("language", ["en", "uk"])
def test_service_type_options_are_the_service_types(language):
    question = ROUTE_QUESTIONS["service_type"][language]
    assert question.type == DecisionType.CHOICE
    assert tuple(question.criteria) == SERVICE_TYPES
    assert ROUTE_QUESTIONS["wants_human"][language].type == DecisionType.BOOLEAN


def test_route_from_answers():
    result = route_from(answers(service_type="greeting_only", wants_human=True))
    assert result.service_type == "greeting_only"
    assert result.wants_human is True
    assert result.is_service
    assert result.answers["service_type"] == {
        "value": "greeting_only",
        "confidence": 0.9,
        "provider": "fake",
    }


def test_unknown_service_type_is_none():
    result = route_from(answers(service_type="weather_talk"))
    assert result.service_type == "none"
    assert not result.is_service
    assert result.wants_human is False


def test_no_answers_means_a_normal_message():
    result = route_from(DecisionSet())
    assert result == Route()


def test_route_to_dict():
    assert Route(service_type="not_a_bot", wants_human=True).to_dict() == {
        "service_type": "not_a_bot",
        "wants_human": True,
        "answers": {},
    }


async def test_route_sees_only_the_rewritten_text():
    decider = FakeDecider({"service_type": "none", "wants_human": False})
    services = fake_services(decider)
    ctx = make_ctx(input_text="скільки коштує це?")

    result = await route(services, ctx, "How much is a manicure?")

    state, questions = decider.calls[0]
    assert state == "Client's latest message:\nHow much is a manicure?"
    assert "скільки" not in state
    assert [q.key for q in questions] == ["service_type", "wants_human"]
    assert result == Route(answers=result.answers)


async def test_route_sees_the_whole_message_a_rewrite_has_shortened():
    decider = FakeDecider({"service_type": "none", "wants_human": False})
    story = "Мене звати Таня. " + "Я двадцять років працюю з людьми. " * 1200 + "Кінець історії."
    ctx = make_ctx(input_text=story)

    await route(fake_services(decider), ctx, "Хто ви?")

    state, _ = decider.calls[0]
    assert state.startswith("Client's latest message:\nМене звати Таня.")
    assert "Кінець історії." in state and state.endswith("Хто ви?")
    assert ctx.trace.steps[-1]["own_words"] is True


async def test_route_greeting():
    decider = FakeDecider({"service_type": "greeting_only", "wants_human": False})
    result = await route(fake_services(decider), make_ctx(input_text="привіт"), "Hello")
    assert result.service_type == "greeting_only"
    assert result.is_service


async def test_route_asks_in_the_tenant_question_language():
    decider = FakeDecider({"service_type": "none", "wants_human": True})
    ctx = make_ctx(settings=TenantSettings(languages=["uk"], jev_question_language="uk"))
    result = await route(fake_services(decider), ctx, "Покличте менеджера")
    _, questions = decider.calls[0]
    assert questions[1] is ROUTE_QUESTIONS["wants_human"]["uk"]
    assert result.wants_human is True


async def test_route_trace_and_decisions_log():
    decider = FakeDecider({"service_type": "not_a_bot", "wants_human": False})
    services = fake_services(decider)
    ctx = make_ctx()

    await route(services, ctx, "Are you a bot?")

    step = ctx.trace.steps[-1]
    assert step["step"] == "route"
    assert step["questions"] == ["service_type", "wants_human"]
    assert step["answers"]["service_type"]["value"] == "not_a_bot"
    assert step["answers"]["wants_human"]["value"] is False
    # every answer is logged and linked from the trace
    assert {row.key for row in services.saved} == {"service_type", "wants_human"}
    assert step["decision_ids"] == [str(row.id) for row in services.saved]
    assert all(row.turn_id == ctx.turn_id for row in services.saved)
