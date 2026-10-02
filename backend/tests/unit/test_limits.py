"""Jev request limits: options / steps, state length, splitting into requests."""

import pytest

from app.decisions.limits import (
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_STEPS,
    Limits,
    estimate_tokens,
    question_tokens,
    split_batches,
)
from app.errors import AppError
from tests.decision_helpers import choice, score, yes_no


def test_estimate_is_pessimistic():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 30) == 11


def test_question_tokens_count_key_and_body():
    short = yes_no("k", "x")
    long = yes_no("k", "x" * 300)
    assert question_tokens(long) > question_tokens(short) + 90


def test_one_batch_when_everything_fits():
    questions = [yes_no(f"q{i}") for i in range(5)]
    assert split_batches("state", questions) == [questions]


def test_split_by_question_tokens_keeps_order():
    questions = [yes_no(f"q{i}", "x" * 90) for i in range(6)]
    per_question = question_tokens(questions[0])
    limits = Limits(questions_tokens=per_question * 2)
    batches = split_batches("state", questions, limits)
    assert [len(b) for b in batches] == [2, 2, 2]
    assert [q.key for b in batches for q in b] == [q.key for q in questions]


def test_split_by_question_count():
    questions = [yes_no(f"q{i}") for i in range(7)]
    batches = split_batches("state", questions, Limits(max_questions=3))
    assert [len(b) for b in batches] == [3, 3, 1]


def test_state_plus_question_over_context_is_an_error():
    question = yes_no("big", "x" * 300)
    limits = Limits(context_tokens=question_tokens(question) + 5)
    with pytest.raises(AppError) as err:
        split_batches("s" * 100, [question], limits)
    assert err.value.code == "decisions.state_too_long"
    assert err.value.params["key"] == "big"
    assert err.value.params["max"] == limits.context_tokens


def test_too_many_choice_options():
    options = [f"o{i}" for i in range(MAX_CHOICE_OPTIONS + 1)]
    with pytest.raises(AppError) as err:
        split_batches("s", [choice("pick", *options)])
    assert err.value.code == "decisions.too_many_options"
    assert err.value.params == {
        "key": "pick",
        "count": MAX_CHOICE_OPTIONS + 1,
        "max": MAX_CHOICE_OPTIONS,
    }


def test_max_choice_options_is_fine():
    options = [f"o{i}" for i in range(MAX_CHOICE_OPTIONS)]
    assert len(split_batches("s", [choice("pick", *options)])) == 1


def test_too_many_score_steps():
    with pytest.raises(AppError) as err:
        split_batches("s", [score("rate", MAX_SCORE_STEPS + 1)])
    assert err.value.code == "decisions.too_many_steps"
