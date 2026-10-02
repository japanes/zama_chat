"""Questions for decider tests."""

from app.db.models import DecisionType
from app.decisions.engine import Question


def yes_no(key: str, text: str = "Is it so?") -> Question:
    return Question(
        key=key,
        type=DecisionType.BOOLEAN,
        instructions=text,
        criteria={"true": "yes", "false": "no"},
    )


def choice(key: str, *options: str, text: str = "Which one?") -> Question:
    return Question(
        key=key,
        type=DecisionType.CHOICE,
        instructions=text,
        criteria={o: f"option {o}" for o in (options or ("a", "b"))},
    )


def score(key: str, steps: int = 5, text: str = "How much?") -> Question:
    return Question(
        key=key,
        type=DecisionType.SCORE,
        instructions=text,
        criteria={str(i): f"level {i}" for i in range(1, steps + 1)},
    )
