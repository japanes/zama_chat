"""Scenario format (docs/04 §4): YAML → ScenarioDef → `scenarios.definition` (JSON).

Every text (field question, filter, start question, cancel description) is a
LocalizedText: a plain string in the scenario's main `language`, or {language: text}.
A missing tenant language is only a warning: at run time the text in the main language
is used and the reply model translates it.

`check_definition()` raises AppError with a code + params for errors (the admin shows them
in its language) and returns warnings in the same form.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.tenant_settings import LANG_RE
from app.errors import AppError

# snake_case without "__": "<scenario>__<field>" stays unambiguous (extract.py)
KEY_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
Key = Annotated[str, Field(max_length=64, pattern=KEY_RE.pattern)]


def _check_text(value: str | dict[str, str]) -> str | dict[str, str]:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("empty text")
        return value.strip()
    if not value:
        raise ValueError("empty text")
    out: dict[str, str] = {}
    for lang, text in value.items():
        code = str(lang).strip().lower()
        if not LANG_RE.match(code):
            raise ValueError(f"bad language code {lang!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"empty text for {lang!r}")
        out[code] = text.strip()
    return out


LocalizedText = Annotated[str | dict[str, str], AfterValidator(_check_text)]


@dataclass(frozen=True)
class LocalText:
    text: str
    language: str
    exact: bool  # False: the requested language is missing, `text` is in `language`


def pick_text(value: str | dict[str, str], language: str, main: str) -> LocalText:
    """The text in `language`; otherwise in the scenario's main language; otherwise any."""
    if isinstance(value, str):
        return LocalText(value, main, language == main)
    if language in value:
        return LocalText(value[language], language, True)
    if main in value:
        return LocalText(value[main], main, False)
    lang, text = next(iter(value.items()))
    return LocalText(text, lang, False)


def text_languages(value: str | dict[str, str], main: str) -> set[str]:
    return {main} if isinstance(value, str) else set(value)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StartQuestion(Strict):
    """Own question (`key` + `ask`) or a reference to a field (`field`): "does the message
    answer this field?"."""

    key: Key | None = None
    ask: LocalizedText | None = None
    field: Key | None = None
    weight: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def _one_kind(self) -> "StartQuestion":
        own = self.key is not None or self.ask is not None
        if own == (self.field is not None):
            raise ValueError("a start question has either key + ask or field")
        if own and (self.key is None or self.ask is None):
            raise ValueError("an own start question needs both key and ask")
        return self


class Start(Strict):
    questions: list[StartQuestion] = Field(min_length=1)
    # start when the sum of weights of "yes" answers >= threshold
    threshold: int | None = Field(default=None, ge=1)


class Filter(Strict):
    """A check of the answer (a yes/no question to Jev); every filter must pass."""

    key: Key
    ask: LocalizedText
    # optional wording of the criteria; default: "yes" / "no"
    yes: LocalizedText | None = None
    no: LocalizedText | None = None


class FieldType(StrEnum):
    TEXT = "text"
    CHOICE = "choice"
    YES_NO = "yes_no"


class Option(Strict):
    key: Key
    label: LocalizedText


class FieldDef(Strict):
    key: Key
    type: FieldType = FieldType.TEXT
    # what the bot asks; a hint for the reply model, which words it anew every time
    question: LocalizedText
    # what counts as an answer (for the extract model and the "field:" question)
    description: LocalizedText | None = None
    options: list[Option] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    # asked only after these fields are filled
    ask_after: list[Key] = Field(default_factory=list)


class Completion(StrEnum):
    ALL_FIELDS = "all_fields"
    EXPLICIT_YES = "explicit_yes"  # stage 6
    EXTERNAL_EVENT = "external_event"  # stage 6
    RECEIPT = "receipt"  # stage 6


class Resume(Strict):
    # always — continue whenever the client comes back (interview, profile);
    # within_hours — continue only within `hours` of the last activity, then the run
    # is closed as abandoned and the next request starts a new one (booking)
    mode: Literal["always", "within_hours"] = "always"
    hours: float | None = Field(default=None, gt=0)


class Cancel(Strict):
    description: LocalizedText | None = None


class ActionEvent(StrEnum):
    STARTED = "started"
    FIELD_FILLED = "field_filled"
    FIELD_STUCK = "field_stuck"
    ALL_FIELDS = "all_fields"
    CONFIRMED = "confirmed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ABANDONED = "abandoned"
    SILENCE = "silence"


class Action(Strict):
    on: ActionEvent
    do: str = Field(min_length=1, max_length=64)
    endpoint: str | None = Field(default=None, max_length=100)
    # silence only: minutes without a client message (default: tenant silence_minutes)
    after_minutes: float | None = Field(default=None, gt=0)
    # status in the result JSON (default by event: completed / cancelled / incomplete…)
    status: str | None = Field(default=None, max_length=32)
    # field_filled / field_stuck only: just this field
    field: Key | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class ScenarioDef(Strict):
    key: Key
    title: LocalizedText
    language: str  # main language: the source for translation
    description: LocalizedText | None = None
    start: Start
    fields: list[FieldDef] = Field(min_length=1)
    completion: Completion = Completion.ALL_FIELDS
    resume: Resume = Field(default_factory=Resume)
    cancel: Cancel = Field(default_factory=Cancel)
    actions: list[Action] = Field(default_factory=list)

    @property
    def threshold(self) -> int:
        """Explicit threshold; a single start question without one → its weight."""
        if self.start.threshold is not None:
            return self.start.threshold
        return self.start.questions[0].weight

    def field(self, key: str) -> FieldDef:
        for f in self.fields:
            if f.key == key:
                return f
        raise KeyError(key)

    @property
    def field_keys(self) -> list[str]:
        return [f.key for f in self.fields]

    def text(self, value: str | dict[str, str], language: str) -> LocalText:
        return pick_text(value, language, self.language)


def parse_definition(data: Any) -> ScenarioDef:
    try:
        return ScenarioDef.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in err.get("loc", ())), "msg": err.get("msg", "")}
            for err in exc.errors()
        ]
        raise AppError("scenario.invalid", {"errors": errors}) from exc


def _cycle(defn: ScenarioDef) -> list[str] | None:
    graph = {f.key: list(f.ask_after) for f in defn.fields}
    state: dict[str, int] = {}  # 1 = in progress, 2 = done
    path: list[str] = []

    def visit(key: str) -> list[str] | None:
        state[key] = 1
        path.append(key)
        for dep in graph.get(key, []):
            if state.get(dep) == 1:
                return [*path[path.index(dep) :], dep]
            if state.get(dep) is None and (found := visit(dep)):
                return found
        path.pop()
        state[key] = 2
        return None

    for key in graph:
        if state.get(key) is None and (found := visit(key)):
            return found
    return None


def _texts(defn: ScenarioDef) -> Iterator[tuple[str, str | dict[str, str]]]:
    yield "title", defn.title
    if defn.description is not None:
        yield "description", defn.description
    for q in defn.start.questions:
        if q.ask is not None:
            yield f"start.{q.key}", q.ask
    for f in defn.fields:
        yield f"fields.{f.key}.question", f.question
        if f.description is not None:
            yield f"fields.{f.key}.description", f.description
        for o in f.options:
            yield f"fields.{f.key}.options.{o.key}", o.label
        for flt in f.filters:
            yield f"fields.{f.key}.filters.{flt.key}", flt.ask
            for name in ("yes", "no"):
                value = getattr(flt, name)
                if value is not None:
                    yield f"fields.{f.key}.filters.{flt.key}.{name}", value
    if defn.cancel.description is not None:
        yield "cancel.description", defn.cancel.description


KNOWN_ACTIONS = ("http_post",)


def check_definition(
    defn: ScenarioDef, languages: list[str], *, actions: tuple[str, ...] = KNOWN_ACTIONS
) -> list[dict[str, Any]]:
    """Errors → AppError; returns warnings [{"code", "params"}]."""
    if not LANG_RE.match(defn.language):
        raise AppError("scenario.language_invalid", {"language": defn.language})

    keys = defn.field_keys
    if len(set(keys)) != len(keys):
        dup = sorted({k for k in keys if keys.count(k) > 1})
        raise AppError("scenario.field_duplicate", {"fields": dup})
    for f in defn.fields:
        for ref in f.ask_after:
            if ref not in keys:
                raise AppError("scenario.ask_after_unknown", {"field": f.key, "ref": ref})
            if ref == f.key:
                raise AppError("scenario.ask_after_cycle", {"fields": [f.key, f.key]})
        filter_keys = [flt.key for flt in f.filters]
        if len(set(filter_keys)) != len(filter_keys):
            raise AppError("scenario.filter_duplicate", {"field": f.key})
        if f.type == FieldType.CHOICE and len(f.options) < 2:
            raise AppError("scenario.choice_options_missing", {"field": f.key})
        if f.type != FieldType.CHOICE and f.options:
            raise AppError("scenario.options_not_allowed", {"field": f.key, "type": str(f.type)})
    if cycle := _cycle(defn):
        raise AppError("scenario.ask_after_cycle", {"fields": cycle})

    start_ids = []
    for q in defn.start.questions:
        if q.field is not None and q.field not in keys:
            raise AppError("scenario.start_field_unknown", {"field": q.field})
        start_ids.append(q.key or f"field:{q.field}")
    if len(set(start_ids)) != len(start_ids):
        raise AppError("scenario.start_question_duplicate", {"questions": start_ids})
    total = sum(q.weight for q in defn.start.questions)
    if defn.start.threshold is None and len(defn.start.questions) > 1:
        raise AppError("scenario.threshold_required", {"questions": len(defn.start.questions)})
    if defn.threshold > total:
        # the scenario could never start
        raise AppError("scenario.threshold_too_high", {"threshold": defn.threshold, "max": total})

    if defn.resume.mode == "within_hours" and defn.resume.hours is None:
        raise AppError("scenario.resume_hours_required")
    for i, action in enumerate(defn.actions):
        if action.do not in actions:
            raise AppError("scenario.action_unknown", {"index": i, "do": action.do})
        if action.do == "http_post" and not action.endpoint:
            raise AppError("scenario.action_endpoint_required", {"index": i, "on": action.on})
        if action.field is not None and action.field not in keys:
            raise AppError("scenario.action_field_unknown", {"index": i, "field": action.field})
        if action.after_minutes is not None and action.on != ActionEvent.SILENCE:
            raise AppError("scenario.after_minutes_not_silence", {"index": i})

    warnings: list[dict[str, Any]] = []
    if languages and defn.language not in languages:
        warnings.append(
            {
                "code": "scenario.main_language_not_in_tenant",
                "params": {"language": defn.language, "languages": languages},
            }
        )
    for path, value in _texts(defn):
        have = text_languages(value, defn.language)
        for lang in languages:
            if lang not in have:
                warnings.append(
                    {
                        "code": "scenario.text_missing_language",
                        "params": {"path": path, "language": lang},
                    }
                )
    if defn.completion != Completion.ALL_FIELDS:
        # TODO(stage 6): explicit_yes / external_event / receipt; until then the run waits
        warnings.append(
            {"code": "scenario.completion_not_supported", "params": {"mode": defn.completion}}
        )
    return warnings
