"""Filters of fields: the second batch to the decider (Jev) in a turn (docs/04 §5).

Every filter is a boolean question `filter:<scenario>.<field>.<filter>`; "true" = the
answer passes. All fields checked in one round go in one batch. The state lists the
answers being checked and the answers already accepted (the context a re-check needs).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.core.state_render import trim_to_tokens
from app.db.models import DecisionType
from app.decisions.engine import DecideContext, DecisionSet, Question, run_decisions
from app.decisions.limits import estimate_tokens
from app.logging import get_logger
from app.scenarios.fields import FieldState
from app.scenarios.questions import filter_key, preview, templates, title_of
from app.scenarios.schema import FieldDef, ScenarioDef

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

STATE_MAX_TOKENS = 4000
# how much of an answer the decider sees in a turn; a longer one is cut at the end
ANSWER_PREVIEW = 1000


@dataclass(frozen=True)
class CheckItem:
    definition: ScenarioDef
    field: FieldDef
    value: str
    states: dict[str, FieldState]  # the run's fields, for context


def filter_questions(item: CheckItem, language: str) -> list[Question]:
    defn, f = item.definition, item.field
    q = defn.text(f.question, language)
    out = []
    for flt in f.filters:
        ask = defn.text(flt.ask, language)
        t = templates(ask.language)
        yes = defn.text(flt.yes, language).text if flt.yes is not None else t["yes"]
        no = defn.text(flt.no, language).text if flt.no is not None else t["no"]
        out.append(
            Question(
                filter_key(defn.key, f.key, flt.key),
                DecisionType.BOOLEAN,
                t["filter"].format(q=q.text, ask=ask.text),
                {"true": yes, "false": no},
            )
        )
    return out


def _answer_lines(items: list[CheckItem], language: str, answer_chars: int) -> list[str]:
    lines = ["Client's answers to check:"]
    for item in items:
        defn = item.definition
        q = defn.text(item.field.question, language).text
        lines.append(f"- [{defn.key}.{item.field.key}] question «{q}»")
        lines.append(f"  client's answer: «{preview(item.value, answer_chars)}»")
    return lines


def answers_fit(items: list[CheckItem], language: str, *, answer_chars: int) -> bool:
    """Whether the answers under check get into the state whole. The state is cut from the
    end (the accepted answers go first, then the tail of what is checked), and a verdict
    about a cut answer says nothing about the answer."""
    return estimate_tokens("\n".join(_answer_lines(items, language, answer_chars))) <= (
        STATE_MAX_TOKENS
    )


def check_state(
    items: list[CheckItem], language: str, *, answer_chars: int = ANSWER_PREVIEW
) -> str:
    lines = _answer_lines(items, language, answer_chars)
    context: dict[str, list[str]] = {}
    checked = {(i.definition.key, i.field.key) for i in items}
    for item in items:
        defn = item.definition
        if defn.key in context:
            continue
        rows = []
        for f in defn.fields:
            fs = item.states[f.key]
            if (defn.key, f.key) in checked or not fs.complete or fs.value is None:
                continue
            q = defn.text(f.question, language).text
            rows.append(f"- «{q}»: «{preview(fs.value)}»")
        context[defn.key] = rows
    for item in items:
        rows = context.pop(item.definition.key, None)
        if rows:
            lines.append("")
            lines.append(f"Answers already accepted in «{title_of(item.definition, language)}»:")
            lines.extend(rows)
    return trim_to_tokens("\n".join(lines), STATE_MAX_TOKENS)


async def check_filters(
    services: "Services",
    ctx: TurnContext,
    items: list[CheckItem],
    language: str,
    *,
    trace_step: dict[str, Any] | None = None,
) -> dict[tuple[str, str], list[str]]:
    """(scenario, field) → keys of the failed filters (empty list = passed). A filter the
    decider did not answer counts as passed and is logged: the client is not asked again
    because of our failure. An unsure answer (rule below = ask_client / operator) counts as
    failed: the client is asked again."""
    outcome = await ask_filters(
        services, ctx.decide_context(), items, language, trace_step=trace_step
    )
    return outcome.failed


@dataclass
class FilterOutcome:
    # (scenario, field) → keys of the failed filters (empty list = passed)
    failed: dict[tuple[str, str], list[str]]
    # (scenario, field) → keys of the filters no decider answered; a turn lets them pass
    unanswered: dict[tuple[str, str], list[str]]
    # what the decider was asked for, with its rows (tokens, cost); None — nothing to ask
    decisions: DecisionSet | None = None


async def ask_filters(
    services: "Services",
    ctx: DecideContext,
    items: list[CheckItem],
    language: str,
    *,
    trace_step: dict[str, Any] | None = None,
    answer_chars: int = ANSWER_PREVIEW,
) -> FilterOutcome:
    """The check itself, with everything the decider answered. A turn reads only `failed`
    (check_filters above); a check made outside a dialogue (POST /v1/scenarios/{key}/check)
    also needs to know which filters got no answer and what the batch cost, and shows the
    decider a longer answer (`answer_chars`)."""
    questions: list[Question] = []
    for item in items:
        questions.extend(filter_questions(item, language))
    failed: dict[tuple[str, str], list[str]] = {(i.definition.key, i.field.key): [] for i in items}
    unanswered: dict[tuple[str, str], list[str]] = {key: [] for key in failed}
    if not questions:
        return FilterOutcome(failed, unanswered)
    decisions = await run_decisions(
        services,
        check_state(items, language, answer_chars=answer_chars),
        questions,
        ctx,
        trace_step=trace_step,
    )
    for item in items:
        defn, f = item.definition, item.field
        for flt in f.filters:
            key = filter_key(defn.key, f.key, flt.key)
            if decisions.is_unsure(key):
                # the rule says «no answer when unsure»: ask the client again
                failed[(defn.key, f.key)].append(flt.key)
                continue
            value = decisions.value(key)
            if value is None:
                log.warning("scenario.filter_unanswered", key=key)
                unanswered[(defn.key, f.key)].append(flt.key)
                continue
            if value is not True:
                failed[(defn.key, f.key)].append(flt.key)
    if trace_step is not None:
        trace_step.setdefault("filters", {}).update(
            {f"{s}.{fk}": keys for (s, fk), keys in failed.items()}
        )
    return FilterOutcome(failed, unanswered, decisions)
