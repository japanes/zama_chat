"""A scenario draft from the owner's description in their own words (the admin's «new
scenario» page).

The model does not write a scenario definition: it fills a short sketch — a name, the
signs to start, the questions. The code builds the definition from the sketch and checks
it the usual way, so a draft is always a scenario the editor can open. Nothing is saved
here: the person looks the draft through in the editor, fixes it and saves it.

The model is the one of the `reply` role: it already writes to clients in the tenant's
languages, and a draft needs no setting of its own.
"""

import json
import uuid
from typing import TYPE_CHECKING, Any

from app.db.models import LlmRole
from app.errors import AppError
from app.logging import get_logger
from app.prompts.builtin import SCENARIO_DRAFT, SCENARIO_DRAFT_KEY
from app.prompts.render import render_json, to_text
from app.prompts.store import PromptText, load_prompts
from app.scenarios.loader import prepare
from app.scenarios.schema import KEY_RE, LANG_RE, FieldType, ScenarioDef

if TYPE_CHECKING:
    from app.services import Services

log = get_logger(__name__)

DEFAULT_PROMPT = PromptText(SCENARIO_DRAFT_KEY, 0, to_text(SCENARIO_DRAFT))
DESCRIPTION_LIMIT = 4000
MAX_QUESTIONS = 20
MAX_SIGNS = 5
KEY_LIMIT = 40
# the types a description in words is enough for; a catalog field, a list and a receipt
# need settings a person chooses in the editor
TYPES = (
    FieldType.TEXT,
    FieldType.CHOICE,
    FieldType.YES_NO,
    FieldType.DATE,
    FieldType.TIME,
    FieldType.PHONE,
    FieldType.EMAIL,
    FieldType.NUMBER,
    FieldType.ATTACHMENT,
)


def draft_schema(languages: list[str]) -> dict[str, Any]:
    """The sketch the model fills; every text in each of the tenant's languages."""
    text = {
        "type": "object",
        "properties": {lang: {"type": "string"} for lang in languages},
        "required": list(languages),
        "additionalProperties": False,
    }
    question = {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "name": text,
            "type": {"type": "string", "enum": [str(t) for t in TYPES]},
            "question": text,
            "options": {"type": "array", "items": text},
        },
        "required": ["key", "name", "type", "question", "options"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "key": {"type": "string"},
            "title": text,
            "description": text,
            "start": {"type": "array", "items": text},
            "questions": {"type": "array", "items": question},
            "confirm": {"type": "boolean"},
        },
        "required": ["key", "title", "description", "start", "questions", "confirm"],
        "additionalProperties": False,
    }


def draft_messages(
    prompt: PromptText, description: str, languages: list[str]
) -> list[dict[str, Any]]:
    data = render_json(
        prompt.body, {"languages": languages, "description": description}, key=prompt.key
    )
    return [{"role": "user", "content": to_text(data)}]


def read_sketch(text: str) -> dict[str, Any] | None:
    """The sketch from the answer of a model without structured output: the JSON object
    in the text, whatever is written around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def build_definition(sketch: dict[str, Any], languages: list[str]) -> dict[str, Any]:
    """Sketch → scenario definition (a plain dict for `parse_definition`). Whatever of the
    sketch can not be used is left out; no questions at all → `scenario.draft_empty`."""
    fields: list[dict[str, Any]] = []
    taken: set[str] = set()
    for item in _list(sketch.get("questions"))[:MAX_QUESTIONS]:
        item = item if isinstance(item, dict) else {}
        name = _texts(item.get("name"), languages)
        question = _texts(item.get("question"), languages) or name
        if question is None:
            continue
        key = _key(item.get("key"), taken, "question")
        kind = item.get("type") if item.get("type") in TYPES else FieldType.TEXT
        options = [
            label for o in _list(item.get("options")) if (label := _texts(o, languages)) is not None
        ]
        if kind == FieldType.CHOICE and len(options) < 2:
            kind = FieldType.TEXT  # nothing to choose from: the client answers in words
        field: dict[str, Any] = {"key": key, "type": str(kind), "question": question}
        if name is not None:
            field["title"] = name
        if kind == FieldType.CHOICE:
            option_keys: set[str] = set()
            field["options"] = [
                {"key": _key(None, option_keys, "option"), "label": label} for label in options
            ]
        fields.append(field)
    if not fields:
        raise AppError("scenario.draft_empty", status=422)

    asks = [_texts(sign, languages) for sign in _list(sketch.get("start"))]
    signs: list[dict[str, Any]] = []
    for ask in [a for a in asks if a is not None][:MAX_SIGNS]:
        # keys as the editor names them: sign, sign_2…
        key = f"sign_{len(signs) + 1}" if signs else "sign"
        signs.append({"key": key, "ask": ask, "weight": 1})
    if not signs:
        # no sign in words: the scenario starts when the client answers its first question
        signs = [{"field": fields[0]["key"], "weight": 1}]

    # a draft without a name is called by its first question
    title = _texts(sketch.get("title"), languages) or fields[0].get("title")
    out: dict[str, Any] = {
        "key": _key(sketch.get("key"), set(), "scenario"),
        "title": title or fields[0]["question"],
        "language": languages[0],
        # each sign alone is enough; the person makes it stricter in the editor
        "start": {"questions": signs, "threshold": 1},
        "fields": fields,
    }
    if (description := _texts(sketch.get("description"), languages)) is not None:
        out["description"] = description
    if sketch.get("confirm") is True:
        out["confirm"] = {}
    return out


async def draft_scenario(
    services: "Services", tenant_id: uuid.UUID, languages: list[str], description: str
) -> tuple[ScenarioDef, list[dict[str, Any]], str]:
    """(the draft, its warnings, the model that wrote it). `scenario.draft_empty` when the
    description gave no questions, `scenario.draft_failed` when the answer is no sketch."""
    languages = [lang for lang in languages if LANG_RE.match(lang)] or ["en"]
    prompts = await load_prompts(services.sm, tenant_id, [SCENARIO_DRAFT_KEY])
    prompt = prompts.get(SCENARIO_DRAFT_KEY) or DEFAULT_PROMPT
    config = await services.llm.role_config(LlmRole.REPLY, tenant_id)
    result = await services.llm.complete(
        LlmRole.REPLY,
        draft_messages(prompt, description[:DESCRIPTION_LIMIT], languages),
        tenant_id=tenant_id,
        # a model without structured output answers with text: the sketch is read from it
        schema=draft_schema(languages) if config.model.supports_json_schema else None,
        schema_name="scenario_draft",
        prompt_key=prompt.key,
        prompt_version=prompt.version or None,
    )
    sketch = result.data if result.data is not None else read_sketch(result.text)
    if sketch is None:
        log.warning("scenario_draft.no_sketch", model=result.model, call_id=str(result.call_id))
        raise AppError("scenario.draft_failed", status=422)
    try:
        defn, warnings = prepare(build_definition(sketch, languages), languages)
    except AppError as exc:
        if exc.code == "scenario.draft_empty":
            raise
        log.warning("scenario_draft.invalid", model=result.model, code=exc.code, params=exc.params)
        raise AppError("scenario.draft_failed", status=422) from exc
    return defn, warnings, result.model


# --- helpers ------------------------------------------------------------------------


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _texts(value: Any, languages: list[str]) -> dict[str, str] | None:
    """A text of the sketch in the tenant's languages; None when there is none. A plain
    string is the text in the main language."""
    if isinstance(value, str):
        value = {languages[0]: value}
    if not isinstance(value, dict):
        return None
    out = {
        lang: " ".join(value[lang].split())
        for lang in languages
        if isinstance(value.get(lang), str) and value[lang].strip()
    }
    return out or None


def _key(value: Any, taken: set[str], fallback: str) -> str:
    """A key as the scenario schema wants it, not used yet: the model's own when it fits,
    otherwise `fallback`, `fallback_2`…"""
    base = ""
    if isinstance(value, str):
        chars = [c if c.isascii() and c.isalnum() else "_" for c in value.strip().lower()]
        base = "_".join(part for part in "".join(chars).split("_") if part)[:KEY_LIMIT]
        base = base.strip("_")
    if not KEY_RE.match(base):
        base = fallback
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    taken.add(key)
    return key
