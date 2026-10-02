"""Values of the marked fields from the client's message (docs/04 §5, step 3).

One LLM call (role `extract`) for every field of this turn, all scenarios together.
The source is the ORIGINAL client text, in the client's words; the rewritten text only
hints where to look. A text value whose words are not found in the client's message is
rejected: the field stays empty, the bot asks it, the case goes to the trace.

Schema: one property per field, `<scenario>__<field>` (keys never contain "__"),
`string | null`; choice / yes_no fields get an enum; catalog fields — an enum of the
items' external_ids (the candidates found for this turn, scenarios/catalog.py).

Stage 6: date / time / number fields get a second property `<scenario>__<field>__norm` —
the model's machine reading (YYYY-MM-DD, HH:MM, a number), used only when code can not
read the words itself (scenarios/typed.py); a list field is an array (of objects when it
has item fields). Attachment / receipt fields never go to the model.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.db.models import LlmRole, MessageRole
from app.prompts.builtin import EXTRACT, EXTRACT_KEY
from app.prompts.render import render_json, to_text
from app.prompts.store import PromptText
from app.scenarios.schema import ATTACHMENT_TYPES, NORM_TYPES, FieldDef, FieldType, ScenarioDef
from app.scenarios.typed import list_items, typed_grounded

if TYPE_CHECKING:
    from app.services import Services

SEP = "__"
NORM = "__norm"
YES_NO = ("yes", "no")
NORM_FORMATS = {
    FieldType.DATE: "YYYY-MM-DD",
    FieldType.TIME: "HH:MM (24 hours)",
    FieldType.NUMBER: "number",
}
DEFAULT_PROMPT = PromptText(EXTRACT_KEY, 0, to_text(EXTRACT))


@dataclass(frozen=True)
class Target:
    run_id: uuid.UUID
    definition: ScenarioDef
    field: FieldDef
    current: str | None = None  # value so far (partial answer / corrected answer)
    correction: bool = False
    # type catalog: the items to choose from, (external_id, title) — scenarios/catalog.py
    choices: tuple[tuple[str, str], ...] = ()

    @property
    def scenario(self) -> str:
        return self.definition.key

    @property
    def prop(self) -> str:
        return prop_name(self.definition.key, self.field.key)


@dataclass
class Extracted:
    # (scenario, field) → the client's words; an array for a list field
    values: dict[tuple[str, str], Any] = field(default_factory=dict)
    # (scenario, field) → the model's machine reading of a date / time / number
    norms: dict[tuple[str, str], Any] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    llm_call_id: str | None = None
    model: str | None = None


def prop_name(scenario: str, field_name: str) -> str:
    return f"{scenario}{SEP}{field_name}"


def _list_property(f: FieldDef) -> dict[str, Any]:
    keys = [i.key for i in f.items.item_fields] if f.items is not None else []
    if not keys:
        item: dict[str, Any] = {"type": "string"}
    else:
        item = {
            "type": "object",
            "properties": {k: {"type": ["string", "null"]} for k in keys},
            "required": keys,
            "additionalProperties": False,
        }
    return {"type": ["array", "null"], "items": item}


def _property(target: Target) -> dict[str, Any]:
    f = target.field
    if f.type == FieldType.LIST:
        return _list_property(f)
    if f.type == FieldType.CHOICE:
        return {"type": ["string", "null"], "enum": [*(o.key for o in f.options), None]}
    if f.type == FieldType.YES_NO:
        return {"type": ["string", "null"], "enum": [*YES_NO, None]}
    if f.type == FieldType.CATALOG:
        return {"type": ["string", "null"], "enum": [*(ref for ref, _ in target.choices), None]}
    return {"type": ["string", "null"]}


def extractable(target: Target) -> bool:
    return target.field.type not in ATTACHMENT_TYPES


def extract_schema(targets: list[Target]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for t in targets:
        props[t.prop] = _property(t)
        if t.field.type in NORM_TYPES:
            kind = "number" if t.field.type == FieldType.NUMBER else "string"
            props[t.prop + NORM] = {"type": [kind, "null"]}
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def field_spec(target: Target, language: str) -> dict[str, Any]:
    defn, f = target.definition, target.field
    spec: dict[str, Any] = {
        "id": target.prop,
        "type": str(f.type),
        "question": defn.text(f.question, language).text,
    }
    if f.description is not None:
        spec["description"] = defn.text(f.description, language).text
    if f.options:
        spec["options"] = [
            {"key": o.key, "label": defn.text(o.label, language).text} for o in f.options
        ]
    if f.type == FieldType.CATALOG:
        spec["options"] = [{"key": ref, "label": title} for ref, title in target.choices]
    if f.type in NORM_FORMATS:
        spec["norm"] = {"property": target.prop + NORM, "format": NORM_FORMATS[f.type]}
    if f.type == FieldType.LIST and f.items is not None and f.items.item_fields:
        spec["item_fields"] = [
            {"key": i.key, "label": defn.text(i.label, language).text} for i in f.items.item_fields
        ]
    if target.current:
        spec["current_value"] = target.current
    return spec


def last_bot_message(ctx: TurnContext) -> str:
    for item in reversed(ctx.history):
        if item.role == MessageRole.BOT:
            return item.text
    return ""


def today_line(ctx: TurnContext) -> str:
    from app.scenarios.typed import local_now

    local = local_now(ctx.now, ctx.settings.timezone)
    return f"{local.date().isoformat()} ({local.strftime('%A')}), {ctx.settings.timezone}"


def extract_messages(
    prompt: PromptText, targets: list[Target], ctx: TurnContext, rewritten: str, language: str
) -> list[dict[str, Any]]:
    values: dict[str, Any] = {
        "fields": [field_spec(t, language) for t in targets],
        "client_message": ctx.input_text,
        "rewritten": rewritten,
        "bot_question": last_bot_message(ctx),
    }
    if any(t.field.type in NORM_FORMATS for t in targets):
        values["today"] = today_line(ctx)
    data = render_json(prompt.body, values, key=prompt.key)
    return [{"role": "user", "content": to_text(data)}]


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _same_word(a: str, b: str) -> bool:
    """Equal, or the same stem with another ending ("манікюр" / "манікюру")."""
    if a == b:
        return True
    if a.isdigit() or b.isdigit():
        return False
    n = min(len(a), len(b))
    if n < 4:
        return False
    common = 0
    while common < n and a[common] == b[common]:
        common += 1
    return common >= max(3, n - 3)


# TODO(review): share of the value's words that must be found in the client's message
MIN_GROUNDED_SHARE = 0.6


def grounded(value: str, source: str, *, min_share: float = MIN_GROUNDED_SHARE) -> bool:
    """The value is made of the client's words (allowing other word endings). Short words
    (prepositions, particles) are not counted unless they are numbers."""
    value_words = words(value)
    if not value_words:
        return False
    counted = [w for w in value_words if len(w) >= 3 or w.isdigit()] or value_words
    source_words = set(words(source))
    hits = sum(
        1 for w in counted if w in source_words or any(_same_word(w, s) for s in source_words)
    )
    return hits / len(counted) >= min_share


def _list_value(
    t: Target, raw: Any, source: str, rejected: list[dict[str, Any]]
) -> list[Any] | None:
    """Every item must come from the client: this message or the list so far."""
    items = list_items(t.field, raw)
    if not items:
        return None
    where = f"{t.scenario}.{t.field.key}"
    known = f"{source}\n{t.current or ''}"
    for item in items:
        parts = list(item.values()) if isinstance(item, dict) else [item]
        for part in parts:
            if not grounded(str(part), known):
                rejected.append({"field": where, "value": str(part), "reason": "not_in_message"})
                return None
    return items


def parse_norms(data: dict[str, Any] | None, targets: list[Target]) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for t in targets:
        if t.field.type in NORM_TYPES:
            raw = (data or {}).get(t.prop + NORM)
            if raw not in (None, ""):
                out[(t.scenario, t.field.key)] = raw
    return out


def parse_values(
    data: dict[str, Any] | None, targets: list[Target], source: str
) -> tuple[dict[tuple[str, str], Any], list[dict[str, Any]]]:
    values: dict[tuple[str, str], Any] = {}
    rejected: list[dict[str, Any]] = []
    for t in targets:
        raw = (data or {}).get(t.prop)
        if raw is None:
            continue
        if t.field.type == FieldType.LIST:
            items = _list_value(t, raw, source, rejected)
            if items is not None:
                values[(t.scenario, t.field.key)] = items
            continue
        value = str(raw).strip()
        if not value:
            continue
        where = f"{t.scenario}.{t.field.key}"
        by_type = typed_grounded(t.field, value, source)
        if by_type is not None:
            if not by_type:
                rejected.append({"field": where, "value": value, "reason": "not_in_message"})
                continue
        elif t.field.type == FieldType.CHOICE:
            if value not in {o.key for o in t.field.options}:
                rejected.append({"field": where, "value": value, "reason": "unknown_option"})
                continue
        elif t.field.type == FieldType.YES_NO:
            if value not in YES_NO:
                rejected.append({"field": where, "value": value, "reason": "not_yes_no"})
                continue
        elif t.field.type == FieldType.CATALOG:
            # the value is the item's external_id; apply_values turns it into the title
            if value not in {ref for ref, _ in t.choices}:
                rejected.append({"field": where, "value": value, "reason": "unknown_item"})
                continue
        elif not grounded(value, source):
            rejected.append({"field": where, "value": value, "reason": "not_in_message"})
            continue
        values[(t.scenario, t.field.key)] = value
    return values, rejected


async def extract(
    services: "Services",
    ctx: TurnContext,
    prompt: PromptText | None,
    targets: list[Target],
    *,
    rewritten: str,
    language: str,
) -> Extracted:
    targets = [t for t in targets if extractable(t)]
    if not targets:
        return Extracted()
    prompt = prompt or DEFAULT_PROMPT
    result = await services.llm.complete(
        LlmRole.EXTRACT,
        extract_messages(prompt, targets, ctx, rewritten, language),
        tenant_id=ctx.tenant_id,
        turn_id=ctx.turn_id,
        schema=extract_schema(targets),
        schema_name="extract",
        prompt_key=prompt.key,
        prompt_version=prompt.version or None,
        overrides=ctx.overrides,
    )
    values, rejected = parse_values(result.data, targets, ctx.input_text)
    return Extracted(
        values=values,
        norms=parse_norms(result.data, targets),
        rejected=rejected,
        llm_call_id=str(result.call_id),
        model=result.model,
    )
