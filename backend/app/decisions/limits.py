"""Jev request limits.

- the whole request — state, every question and Jev's own wrapper — ≤ 32k tokens: the
  window of the Jev model. The Gateway takes a longer request and answers it (a route of
  a 39 000-character story went through as 36 751 tokens), so the limit is ours to keep;
- choice ≤ 255 options, score ≤ 10 steps.

Tokens are estimated, not counted. The rates are per character, by script, measured on
Jev (scripts/jev_tokens.py, 8 000 characters of prose a language):

| text | real tokens | characters a token |
|---|---|---|
| English | 1 933 | 4.1 |
| Russian | 4 349 | 1.8 |
| Ukrainian | 5 676 | 1.4 |

A Cyrillic letter is taken as 0.9 token, any other ASCII character as 0.3, anything else
as 1.5 (not measured: emoji and other scripts are rare here, so a wide margin costs
little). The estimate is above the real count on every probe: by 7 % on Ukrainian, by
19–24 % on the real requests of a dialogue (42 route questions; a 39 000-character story).
A state that does not fit next to its questions is split by the caller
(core/state_render.split_to_tokens), not cut.
"""

import json
import math
import re
from dataclasses import dataclass

from app.db.models import DecisionType
from app.decisions.engine import Question
from app.errors import AppError

REQUEST_TOKENS = 32_000
# Jev's own part of a request: a state of one character with one tiny question took 292
# tokens, the question itself about 25
WRAPPER_TOKENS = 300
# how Jev lays out a question around its JSON is not seen: a margin a question
QUESTION_OVERHEAD_TOKENS = 20
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_STEPS = 10
# TODO(review): Jev docs do not name a per-request question count; 100 keeps one request
# fast and matches "up to 100 candidates" of stage 5. Check on the probe.
MAX_QUESTIONS_PER_REQUEST = 100

CYRILLIC_TOKENS = 0.9
ASCII_TOKENS = 0.3
OTHER_TOKENS = 1.5
_CYRILLIC = re.compile("[Ѐ-ӿ]")


def estimate_tokens(text: str) -> int:
    if text.isascii():
        return math.ceil(len(text) * ASCII_TOKENS) + 1
    cyrillic = len(_CYRILLIC.findall(text))
    ascii_ = len(text.encode("ascii", "ignore"))
    other = len(text) - cyrillic - ascii_
    return math.ceil(cyrillic * CYRILLIC_TOKENS + ascii_ * ASCII_TOKENS + other * OTHER_TOKENS) + 1


def question_tokens(question: Question) -> int:
    return (
        estimate_tokens(question.key)
        + estimate_tokens(json.dumps(question.to_jev(), ensure_ascii=False))
        + QUESTION_OVERHEAD_TOKENS
    )


@dataclass(frozen=True)
class Limits:
    request_tokens: int = REQUEST_TOKENS
    max_questions: int = MAX_QUESTIONS_PER_REQUEST


def request_tokens(state: str, questions: list[Question]) -> int:
    """The whole request, estimated."""
    return WRAPPER_TOKENS + estimate_tokens(state) + sum(question_tokens(q) for q in questions)


def state_room(questions: list[Question], limits: Limits | None = None) -> int:
    """How long a state may be for all the questions to go in one request with it."""
    limits = limits or Limits()
    return limits.request_tokens - WRAPPER_TOKENS - sum(question_tokens(q) for q in questions)


def part_room(questions: list[Question], limits: Limits | None = None) -> int:
    """How long one part of a split state may be: what the questions leave of the window,
    and never less than half of it — questions that take more go in several requests
    (split_batches) rather than squeeze the state into crumbs."""
    limits = limits or Limits()
    return max(state_room(questions, limits), limits.request_tokens // 2 - WRAPPER_TOKENS)


def check_question(question: Question) -> None:
    if question.type == DecisionType.CHOICE and len(question.criteria) > MAX_CHOICE_OPTIONS:
        raise AppError(
            "decisions.too_many_options",
            {"key": question.key, "count": len(question.criteria), "max": MAX_CHOICE_OPTIONS},
        )
    if question.type == DecisionType.SCORE and len(question.criteria) > MAX_SCORE_STEPS:
        raise AppError(
            "decisions.too_many_steps",
            {"key": question.key, "count": len(question.criteria), "max": MAX_SCORE_STEPS},
        )


def split_batches(
    state: str, questions: list[Question], limits: Limits | None = None
) -> list[list[Question]]:
    """Questions → requests that each fit the window with the state. Order is kept.

    A single question that does not fit next to the state is an error: the state must be
    split by the caller, not silently cut here."""
    limits = limits or Limits()
    state_tokens = WRAPPER_TOKENS + estimate_tokens(state)
    batches: list[list[Question]] = []
    current: list[Question] = []
    current_tokens = 0
    for question in questions:
        check_question(question)
        tokens = question_tokens(question)
        if state_tokens + tokens > limits.request_tokens:
            raise AppError(
                "decisions.state_too_long",
                {
                    "key": question.key,
                    "tokens": state_tokens + tokens,
                    "max": limits.request_tokens,
                },
            )
        if current and (
            state_tokens + current_tokens + tokens > limits.request_tokens
            or len(current) >= limits.max_questions
        ):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(question)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches
