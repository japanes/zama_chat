"""Questions, value coercion, decision rows, FakeDecider strictness, trace view."""

import uuid

import pytest

from app.db.models import DecisionProvider, DecisionReason, DecisionType
from app.decisions.engine import (
    Answer,
    DecideContext,
    DecisionSet,
    Question,
    decision_row,
    question_keys_unique,
    trace_answers,
)
from app.decisions.fake import FakeDecider
from tests.decision_helpers import choice, score, yes_no

CTX = DecideContext(tenant_id=uuid.uuid4(), turn_id=uuid.uuid4())


def test_boolean_needs_true_false_criteria():
    with pytest.raises(ValueError, match="true/false"):
        Question("k", DecisionType.BOOLEAN, "?", {"yes": "y", "no": "n"})


def test_question_needs_criteria():
    with pytest.raises(ValueError, match="no criteria"):
        Question("k", DecisionType.CHOICE, "?", {})


def test_to_jev_format():
    q = choice("service_type", "none", "greeting_only", text="What kind?")
    assert q.to_jev() == {
        "type": "choice",
        "instructions": "What kind?",
        "criteria": {"none": "option none", "greeting_only": "option greeting_only"},
    }
    assert q.options == ["none", "greeting_only"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), (False, False), ("TRUE", True), ("false", False), ("yes", None), (1, None)],
)
def test_coerce_boolean(raw, expected):
    assert yes_no("k").coerce(raw) is expected


def test_coerce_choice_and_score():
    c = choice("c", "a", "b")
    assert c.coerce("a") == "a"
    assert c.coerce("z") is None
    assert c.coerce(None) is None
    s = score("s", 5)
    assert s.coerce(3) == 3
    assert s.coerce("4") == 4
    assert s.coerce(9) is None
    assert s.coerce("x") is None


def test_decision_row_links_answer_and_keeps_raw():
    q = yes_no("wants_human")
    answer = Answer(
        key=q.key,
        type=q.type,
        value=True,
        confidence=0.92,
        provider=DecisionProvider.JEV,
        model="typesafe-ai/jev",
    )
    batch = uuid.uuid4()
    row = decision_row(
        q,
        ctx=CTX,
        batch_id=batch,
        provider=DecisionProvider.JEV,
        model="typesafe-ai/jev",
        answer=answer,
        raw={"probability": 0.92},
        latency_ms=120,
        tokens_in=40,
    )
    assert answer.decision_id == row.id
    assert row.answer == {"value": True, "probability": 0.92}
    assert row.confidence == 0.92
    assert row.provider == "jev"
    assert row.reason == "primary"
    assert row.type == "boolean"
    assert row.question == q.to_jev()
    assert row.batch_id == batch
    assert row.turn_id == CTX.turn_id
    assert row.tenant_id == CTX.tenant_id
    assert row.latency_ms == 120
    assert row.tokens_in == 40


def test_decision_row_without_answer():
    row = decision_row(
        yes_no("k"),
        ctx=CTX,
        batch_id=uuid.uuid4(),
        provider=DecisionProvider.JEV,
        model="typesafe-ai/jev",
        reason=DecisionReason.JEV_ERROR,
        used=False,
        error="502: bad gateway",
    )
    assert row.answer is None
    assert row.confidence is None
    assert row.used is False
    assert row.reason == "jev_error"
    assert row.error == "502: bad gateway"


def test_probabilities_are_stored_when_raw_has_none():
    q = choice("c", "a", "b")
    answer = Answer(
        key="c",
        type=q.type,
        value="a",
        confidence=0.8,
        provider=DecisionProvider.JEV,
        model="m",
        probabilities={"a": 0.8, "b": 0.2},
    )
    row = decision_row(
        q, ctx=CTX, batch_id=uuid.uuid4(), provider=DecisionProvider.JEV, model="m", answer=answer
    )
    assert row.answer == {"value": "a", "probabilities": {"a": 0.8, "b": 0.2}}


def test_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        question_keys_unique([yes_no("a"), yes_no("b"), yes_no("a")])


def test_decision_set_value_and_merge():
    a = DecisionSet()
    a.answers["x"] = Answer("x", DecisionType.BOOLEAN, True, 0.9, DecisionProvider.FAKE, "m")
    b = DecisionSet()
    b.answers["y"] = Answer("y", DecisionType.CHOICE, "a", 0.9, DecisionProvider.FAKE, "m")
    a.merge(b)
    assert a.value("x") is True
    assert a.value("y") == "a"
    assert a.value("z", "none") == "none"


def test_trace_answers_view():
    answers = {
        "wants_human": Answer(
            "wants_human", DecisionType.BOOLEAN, False, 0.987654, DecisionProvider.JEV, "m"
        )
    }
    assert trace_answers(answers) == {
        "wants_human": {"value": False, "confidence": 0.9877, "provider": "jev"}
    }


# --- FakeDecider -------------------------------------------------------------------------


async def test_fake_decider_rules_and_patterns():
    fake = FakeDecider(
        {"service_type": "greeting_only", "field:*": True, "wants_human": lambda q, s: "boss" in s}
    )
    result = await fake.decide(
        "call the boss",
        [
            choice("service_type", "none", "greeting_only"),
            yes_no("field:x.y"),
            yes_no("wants_human"),
        ],
        CTX,
    )
    assert result.value("service_type") == "greeting_only"
    assert result.value("field:x.y") is True
    assert result.value("wants_human") is True
    assert fake.asked() == ["service_type", "field:x.y", "wants_human"]
    assert all(r.provider == "fake" for r in result.rows)
    assert {r.id for r in result.rows} == {a.decision_id for a in result.answers.values()}


async def test_fake_decider_fails_on_question_without_rule():
    fake = FakeDecider({"wants_human": False})
    with pytest.raises(AssertionError, match="no rule"):
        await fake.decide("s", [yes_no("unexpected")], CTX)


async def test_fake_decider_fails_on_value_that_does_not_fit():
    fake = FakeDecider({"service_type": "nope"})
    with pytest.raises(AssertionError, match="does not fit"):
        await fake.decide("s", [choice("service_type", "none")], CTX)
