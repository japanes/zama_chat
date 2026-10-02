"""Jev request limits (TypeSafe docs; the probe checks the Gateway has the same).

- state + the longest question ≤ 32k tokens;
- all questions of one request together ≤ 64k tokens;
- choice ≤ 255 options, score ≤ 10 steps.

Tokens are estimated, not counted: ~3 characters per token is pessimistic enough for
uk / ru / en, so a request that fits here fits for real.
"""

import json
from dataclasses import dataclass

from app.db.models import DecisionType
from app.decisions.engine import Question
from app.errors import AppError

CONTEXT_TOKENS = 32_000
QUESTIONS_TOKENS = 64_000
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_STEPS = 10
# TODO(review): Jev docs do not name a per-request question count; 100 keeps one request
# fast and matches "up to 100 candidates" of stage 5. Check on the probe.
MAX_QUESTIONS_PER_REQUEST = 100


def estimate_tokens(text: str) -> int:
    return len(text) // 3 + 1


def question_tokens(question: Question) -> int:
    return estimate_tokens(question.key) + estimate_tokens(
        json.dumps(question.to_jev(), ensure_ascii=False)
    )


@dataclass(frozen=True)
class Limits:
    context_tokens: int = CONTEXT_TOKENS
    questions_tokens: int = QUESTIONS_TOKENS
    max_questions: int = MAX_QUESTIONS_PER_REQUEST


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
    """Questions → requests that each fit the limits. Order is kept inside a batch.

    A single question that does not fit next to the state is an error: the state must be
    shortened by state_render, not silently cut here."""
    limits = limits or Limits()
    state_tokens = estimate_tokens(state)
    batches: list[list[Question]] = []
    current: list[Question] = []
    current_tokens = 0
    for question in questions:
        check_question(question)
        tokens = question_tokens(question)
        if state_tokens + tokens > limits.context_tokens:
            raise AppError(
                "decisions.state_too_long",
                {
                    "key": question.key,
                    "tokens": state_tokens + tokens,
                    "max": limits.context_tokens,
                },
            )
        if current and (
            current_tokens + tokens > limits.questions_tokens
            or len(current) >= limits.max_questions
        ):
            batches.append(current)
            current, current_tokens = [], 0
        current.append(question)
        current_tokens += tokens
    if current:
        batches.append(current)
    return batches
