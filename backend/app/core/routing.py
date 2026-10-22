"""Step 2 — route: one batch of questions to the decider (Jev) about the rewritten text —
and the client's own words, when the rewrite came out shorter (core/state_render.py).

Stage 1 questions: `service_type` (choice) and `wants_human` (boolean). The scenario
questions (start:*, field:*, stop, change_field, client_question — scenarios/questions.py)
go in the same batch, with the scenario sections of the state.

Questions are written in English and Ukrainian; the tenant setting
`jev_question_language` picks one (the Jev probe decides which is more accurate).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.core.state_render import own_words_needed, render_route_state
from app.db.models import DecisionType
from app.decisions.engine import (
    DecisionSet,
    Question,
    run_decisions,
    trace_answers,
)
from app.decisions.limits import CONTEXT_TOKENS, question_tokens

if TYPE_CHECKING:
    from app.services import Services

SERVICE_NONE = "none"
SERVICE_TYPES = ("none", "greeting_only", "goodbye_only", "not_a_bot", "ethical_violation")

# TODO(review): same texts as the Jev probe (scripts/jev_probe.py); tune after the probe
ROUTE_QUESTIONS: dict[str, dict[str, Question]] = {
    "service_type": {
        "en": Question(
            "service_type",
            DecisionType.CHOICE,
            "Classify the client's latest message by its service type.",
            {
                "none": "The message has a question, a request or any information beyond "
                "a bare greeting, farewell or identity question.",
                "greeting_only": "A standalone greeting without any questions or requests "
                "(e.g. 'hi', 'hello', 'good morning').",
                "goodbye_only": "A standalone farewell (e.g. 'bye', 'see you', 'take care').",
                "not_a_bot": "The client asks about your identity, name or job, or whether "
                "you are a real person or a bot.",
                "ethical_violation": "The message is about unethical, illegal or dangerous "
                "things (hacking, fraud, drugs, terrorism, extremism and the like).",
            },
        ),
        "uk": Question(
            "service_type",
            DecisionType.CHOICE,
            "Визнач службовий тип останнього повідомлення клієнта.",
            {
                "none": "У повідомленні є питання, прохання або будь-яка інформація, крім "
                "простого привітання, прощання чи питання про особу.",
                "greeting_only": "Лише привітання без питань і прохань "
                "(наприклад «привіт», «добрий день»).",
                "goodbye_only": "Лише прощання (наприклад «бувай», «до побачення»).",
                "not_a_bot": "Клієнт питає, хто ти, як тебе звати, ким працюєш, або чи ти "
                "жива людина чи бот.",
                "ethical_violation": "Повідомлення про неетичне, незаконне або небезпечне "
                "(злам, шахрайство, наркотики, тероризм, екстремізм тощо).",
            },
        ),
    },
    "wants_human": {
        "en": Question(
            "wants_human",
            DecisionType.BOOLEAN,
            "Does the client ask to talk to a real person "
            "(a manager, an operator, an administrator)?",
            {
                "true": "The client asks for a human, a manager or an operator.",
                "false": "The client does not ask for a human.",
            },
        ),
        "uk": Question(
            "wants_human",
            DecisionType.BOOLEAN,
            "Клієнт просить поговорити з живою людиною (менеджером, оператором, адміністратором)?",
            {
                "true": "Клієнт просить людину, менеджера або оператора.",
                "false": "Клієнт не просить людину.",
            },
        ),
    },
}
DEFAULT_QUESTION_LANGUAGE = "en"


def route_questions(language: str | None) -> list[Question]:
    """The stage 1 route questions in the given language (English when there is none)."""
    out = []
    for variants in ROUTE_QUESTIONS.values():
        out.append(variants.get(language or "") or variants[DEFAULT_QUESTION_LANGUAGE])
    return out


@dataclass
class Route:
    service_type: str = SERVICE_NONE
    wants_human: bool = False
    answers: dict[str, Any] = field(default_factory=dict)  # trace_answers() view
    # all answers of the batch, the scenario ones included (scenarios/runtime.py)
    decisions: DecisionSet = field(default_factory=DecisionSet, compare=False, repr=False)

    @property
    def is_service(self) -> bool:
        return self.service_type != SERVICE_NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "service_type": self.service_type,
            "wants_human": self.wants_human,
            "answers": self.answers,
        }


def route_from(decisions: DecisionSet) -> Route:
    service_type = decisions.value("service_type", SERVICE_NONE)
    if service_type not in SERVICE_TYPES:
        service_type = SERVICE_NONE
    return Route(
        service_type=service_type,
        wants_human=bool(decisions.value("wants_human", False)),
        answers=trace_answers(decisions.answers),
        decisions=decisions,
    )


async def route(
    services: "Services",
    ctx: TurnContext,
    rewritten: str,
    *,
    extra_questions: Iterable[Question] = (),
    sections: Iterable[tuple[str, str]] = (),
    general: bool = True,
) -> Route:
    """`general=False` — only the given questions: no service_type, no wants_human (an open
    scenario with `only_scenario`).

    One batch for every open scenario — the focus and the paused ones alike. The state
    takes Jev's window less the longest question: a long message is seen whole."""
    own = route_questions(ctx.settings.jev_question_language) if general else []
    questions = [*own, *extra_questions]
    room = CONTEXT_TOKENS - max((question_tokens(q) for q in questions), default=0)
    state = render_route_state(
        rewritten, original=ctx.input_text, sections=sections, max_tokens=room
    )
    with ctx.trace.step("route", questions=[q.key for q in questions]) as step:
        if own_words_needed(rewritten, ctx.input_text):
            step["own_words"] = True  # the rewrite was shorter: the client's words went too
        decisions = await run_decisions(
            services,
            state,
            questions,
            ctx.decide_context(),
            trace_step=step,
        )
        result = route_from(decisions)
        step["answers"] = result.answers
    return result
