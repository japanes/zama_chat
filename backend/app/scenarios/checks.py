"""Filters of fields: the second batch to the decider (Jev) in a turn (docs/04 §5).

Every filter is a boolean question `filter:<scenario>.<field>.<filter>`; "true" = the
answer passes. All fields checked in one round go in one batch, together with the show_if
questions of the fields whose condition is checked now (`show:<scenario>.<field>.<key>`,
scenarios/show.py). The state lists the answers being checked with how the bot asked them,
and the run's other answers (the context a re-check needs); for a run whose show_if is
checked — every answer it has, complete or not.

Answers go whole: a verdict about a cut answer says nothing about the answer.
"""

from dataclasses import dataclass, field
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
    from app.scenarios.show import ShowItem
    from app.services import Services

log = get_logger(__name__)

# state + the longest question must stay under the decider's 32k tokens (decisions/limits.py)
STATE_MAX_TOKENS = 28_000
# how much of an answer the decider sees in a turn; None — the whole answer
ANSWER_PREVIEW: int | None = None


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


def _answer_lines(items: list[CheckItem], language: str, answer_chars: int | None) -> list[str]:
    if not items:
        return []
    lines = ["Client's answers to check:"]
    for item in items:
        defn = item.definition
        q = defn.text(item.field.question, language).text
        lines.append(f"- [{defn.key}.{item.field.key}] question «{q}»")
        fs = item.states.get(item.field.key)
        if fs is not None and fs.asked:
            lines.append(f"  the bot asked: «{preview(fs.asked, None)}»")
        lines.append(f"  client's answer: «{preview(item.value, answer_chars)}»")
    return lines


def card_rows(
    defn: ScenarioDef,
    states: dict[str, FieldState],
    language: str,
    *,
    skip: set[str] | frozenset[str] = frozenset(),
    complete_only: bool = True,
) -> list[str]:
    """The run's answers, whole, each with how the bot asked it."""
    rows = []
    for f in defn.fields:
        fs = states[f.key]
        if f.key in skip or fs.value is None or (complete_only and not fs.complete):
            continue
        q = defn.text(f.question, language).text
        mark = "" if fs.complete else " (incomplete)"
        rows.append(f"- «{q}»: «{preview(fs.value, None)}»{mark}")
        if fs.asked:
            rows.append(f"  the bot asked: «{preview(fs.asked, None)}»")
    return rows


def answers_fit(items: list[CheckItem], language: str, *, answer_chars: int | None) -> bool:
    """Whether the answers under check get into the state whole. The state is cut from the
    end (the answers under check go first, then the other answers), and a verdict about a
    cut answer says nothing about the answer."""
    return estimate_tokens("\n".join(_answer_lines(items, language, answer_chars))) <= (
        STATE_MAX_TOKENS
    )


def _state_text(
    items: list[CheckItem],
    language: str,
    answer_chars: int | None,
    shows: "list[ShowItem]",
) -> str:
    lines = _answer_lines(items, language, answer_chars)
    checked = {(i.definition.key, i.field.key) for i in items}
    full = {s.definition.key for s in shows}
    runs: dict[str, tuple[ScenarioDef, dict[str, FieldState]]] = {}
    for item in [*items, *shows]:
        runs.setdefault(item.definition.key, (item.definition, item.states))
    for key, (defn, states) in runs.items():
        every = key in full
        rows = card_rows(
            defn,
            states,
            language,
            skip={f for s, f in checked if s == key},
            complete_only=not every,
        )
        if not rows:
            continue
        if lines:
            lines.append("")
        title = title_of(defn, language)
        if every:
            lines.append(f"Client's answers in «{title}»:")
        else:
            lines.append(f"Answers already accepted in «{title}»:")
        lines.extend(rows)
    return "\n".join(lines)


def check_state(
    items: list[CheckItem],
    language: str,
    *,
    answer_chars: int | None = ANSWER_PREVIEW,
    shows: "list[ShowItem] | None" = None,
) -> str:
    return trim_to_tokens(_state_text(items, language, answer_chars, shows or []), STATE_MAX_TOKENS)


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
    # the show_if questions were not asked: the answers do not fit the state whole
    shows_dropped: list[str] = field(default_factory=list)


async def ask_filters(
    services: "Services",
    ctx: DecideContext,
    items: list[CheckItem],
    language: str,
    *,
    trace_step: dict[str, Any] | None = None,
    answer_chars: int | None = ANSWER_PREVIEW,
    shows: "list[ShowItem] | None" = None,
) -> FilterOutcome:
    """The check itself, with everything the decider answered. A turn reads `failed` and
    the show_if answers (scenarios/show.py reads them from `decisions`); a check made
    outside a dialogue (POST /v1/scenarios/{key}/check) also needs to know which filters
    got no answer and what the batch cost."""
    from app.scenarios.show import show_questions

    shows = list(shows or [])
    questions: list[Question] = []
    for item in items:
        questions.extend(filter_questions(item, language))
    failed: dict[tuple[str, str], list[str]] = {(i.definition.key, i.field.key): [] for i in items}
    unanswered: dict[tuple[str, str], list[str]] = {key: [] for key in failed}
    dropped: list[str] = []
    state = _state_text(items, language, answer_chars, shows)
    if shows and estimate_tokens(state) > STATE_MAX_TOKENS:
        # a condition decided over a cut card says nothing: it stays unknown
        dropped = [f"{s.definition.key}.{s.field.key}" for s in shows]
        log.warning("scenario.show_state_too_long", fields=dropped)
        shows = []
        state = _state_text(items, language, answer_chars, shows)
    for show in shows:
        questions.extend(show_questions(show, language))
    if not questions:
        return FilterOutcome(failed, unanswered, shows_dropped=dropped)
    decisions = await run_decisions(
        services,
        trim_to_tokens(state, STATE_MAX_TOKENS),
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
    if trace_step is not None and items:
        trace_step.setdefault("filters", {}).update(
            {f"{s}.{fk}": keys for (s, fk), keys in failed.items()}
        )
    return FilterOutcome(failed, unanswered, decisions, dropped)
