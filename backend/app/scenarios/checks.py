"""Filters of fields: the second batch to the decider (Jev) in a turn (docs/04 §5).

Every filter is a boolean question `filter:<scenario>.<field>.<filter>`; "true" = the
answer passes. All fields checked in one round go in one batch, together with the show_if
questions of the fields whose condition is checked now (`show:<scenario>.<field>.<key>`,
scenarios/show.py). The state lists the answers being checked with how the bot asked them,
and the run's other answers (the context a re-check needs); for a run whose show_if is
checked — every answer it has, complete or not.

Answers go whole: a verdict about a cut answer says nothing about the answer. While all
of it fits Jev's window (decisions/limits.py) it is one state; else the answers under check
go in as many requests as they need, with the accepted answers after them cut to what is
left (context, not what is checked), and an answer too long for a request alone is checked
in parts: a filter passes when a part passes. The show_if questions need the run's card
whole, so they get a request of their own; a card longer than that is not asked about.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.core.state_render import part_label, split_to_tokens, trim_to_tokens
from app.db.models import DecisionType
from app.decisions.engine import DecideContext, DecisionSet, Question, run_decision_parts
from app.decisions.limits import (
    MAX_QUESTIONS_PER_REQUEST,
    REQUEST_TOKENS,
    WRAPPER_TOKENS,
    estimate_tokens,
    part_room,
    question_tokens,
)
from app.logging import get_logger
from app.scenarios.fields import FieldState
from app.scenarios.questions import filter_key, preview, templates, title_of
from app.scenarios.schema import FieldDef, ScenarioDef

if TYPE_CHECKING:
    from app.scenarios.show import ShowItem
    from app.services import Services

log = get_logger(__name__)

# how much of an answer the decider sees in a turn; None — the whole answer
ANSWER_PREVIEW: int | None = None
CHECK_HEAD = "Client's answers to check:"
ANSWER = "  client's answer"
# context left to a request of answers under check smaller than this is not worth adding
MIN_CONTEXT_TOKENS = 50


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


def _item_head(item: CheckItem, language: str) -> list[str]:
    defn = item.definition
    q = defn.text(item.field.question, language).text
    lines = [f"- [{defn.key}.{item.field.key}] question «{q}»"]
    fs = item.states.get(item.field.key)
    if fs is not None and fs.asked:
        lines.append(f"  the bot asked: «{preview(fs.asked, None)}»")
    return lines


def _answer_lines(items: list[CheckItem], language: str, answer_chars: int | None) -> list[str]:
    if not items:
        return []
    lines = [CHECK_HEAD]
    for item in items:
        lines.extend(_item_head(item, language))
        lines.append(f"{ANSWER}: «{preview(item.value, answer_chars)}»")
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


Runs = dict[str, tuple[ScenarioDef, dict[str, FieldState]]]


def _runs_of(items: "list[CheckItem] | list[ShowItem]") -> Runs:
    runs: Runs = {}
    for item in items:
        runs.setdefault(item.definition.key, (item.definition, item.states))
    return runs


def _context_lines(
    runs: Runs, language: str, checked: set[tuple[str, str]], full: set[str]
) -> list[str]:
    """The runs' answers but the ones under check: the accepted ones, or every answer of
    a run in `full` (its show_if is checked)."""
    lines: list[str] = []
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
    return lines


def _state_text(
    items: list[CheckItem],
    language: str,
    answer_chars: int | None,
    shows: "list[ShowItem]",
) -> str:
    lines = _answer_lines(items, language, answer_chars)
    checked = {(i.definition.key, i.field.key) for i in items}
    full = {s.definition.key for s in shows}
    context = _context_lines(_runs_of([*items, *shows]), language, checked, full)
    if lines and context:
        lines.append("")
    return "\n".join([*lines, *context])


def check_state(
    items: list[CheckItem],
    language: str,
    *,
    answer_chars: int | None = ANSWER_PREVIEW,
    shows: "list[ShowItem] | None" = None,
) -> str:
    """The state of a round while it fits Jev's window."""
    return _state_text(items, language, answer_chars, shows or [])


def _with_context(state: str, context: str, questions: list[Question]) -> str:
    """`state` and as much of the context as the request has room for."""
    left = (
        REQUEST_TOKENS
        - WRAPPER_TOKENS
        - estimate_tokens(state)
        - sum(question_tokens(q) for q in questions)
        - estimate_tokens("\n\n")
    )
    if not context or left < MIN_CONTEXT_TOKENS:
        return state
    return f"{state}\n\n{trim_to_tokens(context, left)}"


def _answer_parts(
    item: CheckItem,
    questions: list[Question],
    language: str,
    answer_chars: int | None,
    room: int,
    context: str,
) -> list[tuple[str, list[Question]]]:
    """One answer too long for a request: a request a part, each with the same filters."""
    head = "\n".join([CHECK_HEAD, *_item_head(item, language)])
    label = part_label(ANSWER, 99, 99)
    piece_room = (
        room
        - estimate_tokens(head)
        - estimate_tokens(f"\n{label}: «»")
        - sum(question_tokens(q) for q in questions)
    )
    pieces = split_to_tokens(preview(item.value, answer_chars), piece_room)
    asks = []
    for n, piece in enumerate(pieces, start=1):
        state = f"{head}\n{part_label(ANSWER, n, len(pieces))}: «{piece}»"
        asks.append((_with_context(state, context, questions), questions))
    return asks


def _filter_asks(
    items: list[CheckItem],
    filters: dict[tuple[str, str], list[Question]],
    language: str,
    answer_chars: int | None,
) -> list[tuple[str, list[Question]]]:
    """The answers under check when they do not fit one state: as many in a request as
    fit, leaving up to a quarter of the window to the context; an answer too long for a
    request alone is split, each part with the same filters."""
    checked = set(filters)
    full: set[str] = set()
    window = REQUEST_TOKENS - WRAPPER_TOKENS
    every = "\n".join(_context_lines(_runs_of(items), language, checked, full))
    room = window - min(estimate_tokens(every), window // 4)
    head_tokens = estimate_tokens(CHECK_HEAD)
    asks: list[tuple[str, list[Question]]] = []
    pack: list[CheckItem] = []
    pack_questions: list[Question] = []
    pack_tokens = head_tokens

    def context_of(group: list[CheckItem]) -> str:
        return "\n".join(_context_lines(_runs_of(group), language, checked, full))

    def close() -> None:
        nonlocal pack, pack_questions, pack_tokens
        if pack:
            state = "\n".join(_answer_lines(pack, language, answer_chars))
            asks.append((_with_context(state, context_of(pack), pack_questions), pack_questions))
        pack, pack_questions, pack_tokens = [], [], head_tokens

    for item in items:
        questions = filters[(item.definition.key, item.field.key)]
        if not questions:
            continue
        lines = _answer_lines([item], language, answer_chars)[1:]
        tokens = estimate_tokens("\n".join(lines)) + sum(question_tokens(q) for q in questions)
        if head_tokens + tokens > room:
            close()
            asks.extend(
                _answer_parts(item, questions, language, answer_chars, room, context_of([item]))
            )
            continue
        if pack and (
            pack_tokens + tokens > room
            or len(pack_questions) + len(questions) > MAX_QUESTIONS_PER_REQUEST
        ):
            close()
        pack.append(item)
        pack_questions = [*pack_questions, *questions]
        pack_tokens += tokens
    close()
    return asks


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
    # the show_if questions were not asked: the run's card does not fit a request whole
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
    filters = {(i.definition.key, i.field.key): filter_questions(i, language) for i in items}
    questions = [q for qs in filters.values() for q in qs]
    asked_shows = [q for s in shows for q in show_questions(s, language)]
    failed: dict[tuple[str, str], list[str]] = {key: [] for key in filters}
    unanswered: dict[tuple[str, str], list[str]] = {key: [] for key in failed}
    dropped: list[str] = []
    state = _state_text(items, language, answer_chars, shows)
    everything = [*questions, *asked_shows]
    if estimate_tokens(state) <= part_room(everything):
        asks = [(state, everything)]
    else:
        asks = []
        if asked_shows:
            card = _state_text([], language, answer_chars, shows)
            if estimate_tokens(card) <= part_room(asked_shows):
                asks.append((card, asked_shows))
            else:
                # a condition decided over a cut card says nothing: it stays unknown
                dropped = [f"{s.definition.key}.{s.field.key}" for s in shows]
                log.warning("scenario.show_state_too_long", fields=dropped)
        asks += _filter_asks(items, filters, language, answer_chars)
    if not any(qs for _, qs in asks):
        return FilterOutcome(failed, unanswered, shows_dropped=dropped)
    decisions = await run_decision_parts(services, asks, ctx, trace_step=trace_step)
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
