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
from app.decisions.engine import DecideContext, Question, run_decisions
from app.logging import get_logger
from app.scenarios.fields import FieldState
from app.scenarios.questions import filter_key, preview, templates, title_of
from app.scenarios.schema import FieldDef, ScenarioDef

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

STATE_MAX_TOKENS = 4000


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


def check_state(items: list[CheckItem], language: str) -> str:
    lines = ["Client's answers to check:"]
    for item in items:
        defn = item.definition
        q = defn.text(item.field.question, language).text
        lines.append(f"- [{defn.key}.{item.field.key}] question «{q}»")
        lines.append(f"  client's answer: «{preview(item.value, 1000)}»")
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
    because of our failure."""
    questions: list[Question] = []
    for item in items:
        questions.extend(filter_questions(item, language))
    failed: dict[tuple[str, str], list[str]] = {(i.definition.key, i.field.key): [] for i in items}
    if not questions:
        return failed
    decisions = await run_decisions(
        services,
        check_state(items, language),
        questions,
        DecideContext(ctx.tenant_id, ctx.turn_id, ctx.overrides),
        trace_step=trace_step,
    )
    for item in items:
        defn, f = item.definition, item.field
        for flt in f.filters:
            key = filter_key(defn.key, f.key, flt.key)
            value = decisions.value(key)
            if value is None:
                log.warning("scenario.filter_unanswered", key=key)
                continue
            if value is not True:
                failed[(defn.key, f.key)].append(flt.key)
    if trace_step is not None:
        trace_step.setdefault("filters", {}).update(
            {f"{s}.{fk}": keys for (s, fk), keys in failed.items()}
        )
    return failed
