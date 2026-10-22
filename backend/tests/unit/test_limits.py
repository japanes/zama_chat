"""Jev request limits: options / steps, state length, splitting into requests."""

import pytest

from app.decisions.limits import (
    MAX_CHOICE_OPTIONS,
    MAX_SCORE_STEPS,
    REQUEST_TOKENS,
    WRAPPER_TOKENS,
    Limits,
    estimate_tokens,
    part_room,
    question_tokens,
    request_tokens,
    split_batches,
    state_room,
)
from app.errors import AppError
from tests.decision_helpers import choice, score, yes_no


def test_estimate_is_pessimistic():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 30) == 10


def test_estimate_is_above_jev_on_the_probe():
    # 8 000 characters of prose a language: Jev counted 1 933 (en), 4 349 (ru), 5 676 (uk)
    assert estimate_tokens("word " * 1600) > 1933
    cyrillic = "слово " * 1333 + "сл"
    assert len(cyrillic) == 8000
    assert estimate_tokens(cyrillic) > 5676


def test_cyrillic_counts_three_times_latin():
    assert estimate_tokens("я" * 100) == 91
    assert estimate_tokens("a" * 100) == 31
    assert estimate_tokens("я" * 50 + "a" * 50) == 61


def test_other_scripts_are_dearest():
    assert estimate_tokens("😀" * 10) == 16


def test_the_whole_request_counts_state_questions_and_wrapper():
    q = yes_no("k", "x")
    assert request_tokens("state", [q]) == WRAPPER_TOKENS + estimate_tokens(
        "state"
    ) + question_tokens(q)
    assert state_room([q]) == REQUEST_TOKENS - WRAPPER_TOKENS - question_tokens(q)


def test_part_room_leaves_the_state_at_least_half_the_window():
    few = [yes_no(f"q{i}") for i in range(3)]
    assert part_room(few) == state_room(few)
    many = [yes_no(f"q{i}", "я" * 3000) for i in range(30)]
    assert state_room(many) < 0
    assert part_room(many) == REQUEST_TOKENS // 2 - WRAPPER_TOKENS


def test_question_tokens_count_key_and_body():
    short = yes_no("k", "x")
    long = yes_no("k", "x" * 300)
    assert question_tokens(long) >= question_tokens(short) + 300 * 0.3 - 1


def test_one_batch_when_everything_fits():
    questions = [yes_no(f"q{i}") for i in range(5)]
    assert split_batches("state", questions) == [questions]


def test_split_by_question_tokens_keeps_order():
    questions = [yes_no(f"q{i}", "x" * 90) for i in range(6)]
    per_question = question_tokens(questions[0])
    state_tokens = WRAPPER_TOKENS + estimate_tokens("state")
    limits = Limits(request_tokens=state_tokens + per_question * 2)
    batches = split_batches("state", questions, limits)
    assert [len(b) for b in batches] == [2, 2, 2]
    assert [q.key for b in batches for q in b] == [q.key for q in questions]


def test_split_by_question_count():
    questions = [yes_no(f"q{i}") for i in range(7)]
    batches = split_batches("state", questions, Limits(max_questions=3))
    assert [len(b) for b in batches] == [3, 3, 1]


def test_every_batch_fits_the_window_with_the_state():
    state = "я" * 20_000
    questions = [yes_no(f"q{i}", "x" * 3000) for i in range(30)]
    batches = split_batches(state, questions)
    assert len(batches) > 1
    assert all(request_tokens(state, b) <= REQUEST_TOKENS for b in batches)
    assert [q.key for b in batches for q in b] == [q.key for q in questions]


def test_state_plus_question_over_the_window_is_an_error():
    question = yes_no("big", "x" * 300)
    limits = Limits(request_tokens=WRAPPER_TOKENS + question_tokens(question) + 5)
    with pytest.raises(AppError) as err:
        split_batches("s" * 100, [question], limits)
    assert err.value.code == "decisions.state_too_long"
    assert err.value.params["key"] == "big"
    assert err.value.params["max"] == limits.request_tokens


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
