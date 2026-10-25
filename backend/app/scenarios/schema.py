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
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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


class ShowIfQuestion(Strict):
    """A yes/no question about the client's answers: "does this field apply?"."""

    key: Key
    ask: LocalizedText
    weight: int = Field(default=1, ge=1, le=100)


class ShowIf(Strict):
    """When a field is asked: like `start`, the sum of weights of "yes" answers must reach
    the threshold. Jev answers the questions over the run's answers (scenarios/show.py)."""

    questions: list[ShowIfQuestion] = Field(min_length=1)
    threshold: int | None = Field(default=None, ge=1)
    # no answer (or "unsure") that could decide it: ask — the field is asked, skip — not
    on_unknown: Literal["ask", "skip"] = "ask"
    # every_turn — checked again after every turn that changed the answers; once — once
    recheck: Literal["every_turn", "once"] = "every_turn"

    @property
    def needed(self) -> int:
        """Explicit threshold; a single question without one → its weight."""
        return self.threshold if self.threshold is not None else self.questions[0].weight


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
    CATALOG = "catalog"  # an item of the tenant's catalog (stage 5)
    # stage 6: the value is checked and normalized by code (scenarios/typed.py)
    DATE = "date"
    TIME = "time"
    PHONE = "phone"
    EMAIL = "email"
    NUMBER = "number"
    LIST = "list"  # several items: guests, positions of an order…
    ATTACHMENT = "attachment"  # a photo / file the client sends
    RECEIPT = "receipt"  # a payment receipt: read by a vision model, checked by code


# filled by code from the attachments of the client's message, never by the extract model
ATTACHMENT_TYPES = (FieldType.ATTACHMENT, FieldType.RECEIPT)
# the extract model also returns the machine value (`<prop>__norm`), code checks it
NORM_TYPES = (FieldType.DATE, FieldType.TIME, FieldType.NUMBER)


class Option(Strict):
    key: Key
    label: LocalizedText


class CatalogSource(Strict):
    """Where a `catalog` field takes its items from."""

    # only items of these categories; empty = the whole catalog
    categories: list[str] = Field(default_factory=list)
    # the bot lists the items as options when there are at most this many
    max_options: int = Field(default=5, ge=2, le=10)


class DateSettings(Strict):
    # a date before today is an error the bot re-asks (booking); true for a birthday…
    allow_past: bool = False
    # at most this many days ahead; None = no limit
    max_days_ahead: int | None = Field(default=None, ge=0, le=3650)


class NumberSettings(Strict):
    min: float | None = None
    max: float | None = None
    integer: bool = False


class ListItemField(Strict):
    """A part of every list item (guest: name, service…)."""

    key: Key
    label: LocalizedText
    required: bool = True


class ListSettings(Strict):
    min_items: int = Field(default=1, ge=1, le=50)
    max_items: int = Field(default=10, ge=1, le=50)
    # empty: an item is a plain text
    item_fields: list[ListItemField] = Field(default_factory=list)


AttachmentKind = Literal["image", "video", "audio", "file"]


class AttachmentSettings(Strict):
    kinds: list[AttachmentKind] = Field(default_factory=lambda: ["image", "file"])
    max_items: int = Field(default=1, ge=1, le=10)


class ReceiptSettings(Strict):
    """What a payment receipt must show (docs/03 §8). The expected amount is a fixed
    `amount` or the price of the item chosen in a catalog field (`amount_field`), times
    the number in `quantity_field`."""

    amount: Decimal | None = Field(default=None, gt=0)
    amount_field: Key | None = None
    quantity_field: Key | None = None
    # ISO 4217; with amount_field the item's currency is used when this is empty
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    # names / IBAN / card endings of the receiver; one of them must be on the receipt
    recipients: list[str] = Field(default_factory=list)
    # the payment may be at most this many days old
    max_age_days: int = Field(default=3, ge=0, le=365)
    # allowed difference of the amount
    tolerance: Decimal = Field(default=Decimal(0), ge=0)


class FieldDef(Strict):
    key: Key
    type: FieldType = FieldType.TEXT
    # a short human name of the answer for the admin ("Service"); the client never sees
    # it. Without it the key is shown.
    title: LocalizedText | None = None
    # what the bot asks; a hint for the reply model, which words it anew every time
    question: LocalizedText
    # what counts as an answer (for the extract model and the "field:" question)
    description: LocalizedText | None = None
    options: list[Option] = Field(default_factory=list)
    # type catalog: which items fit (the value is the item title, the result also gets
    # its external_id in `refs`)
    catalog: CatalogSource | None = None
    # settings of the typed fields (stage 6); each only for its own type
    date: DateSettings | None = None
    number: NumberSettings | None = None
    items: ListSettings | None = None  # type list
    attachment: AttachmentSettings | None = None
    receipt: ReceiptSettings | None = None
    filters: list[Filter] = Field(default_factory=list)
    # asked only after these fields are filled
    ask_after: list[Key] = Field(default_factory=list)
    # asked only when the answers so far say it applies; no show_if — always asked
    show_if: ShowIf | None = None
    # choice only: the options come with POST /v1/runs/start, the file has none
    options_from: Literal["caller"] | None = None


class Completion(StrEnum):
    """When a run is completed (stage 6):
    all_fields     — every field is complete (and confirmed, when `confirm` is set);
    explicit_yes   — the same, the client's «yes» to the summary is required;
    external_event — collected (and confirmed) → waiting for an event from outside
                     (POST /v1/events: CRM booked, slot taken…), see `events`;
    receipt        — all fields with a receipt field accepted (by code or an operator).
    by_caller      — every field is complete → the run waits for new fields from the
                     caller (POST /v1/runs/{id}/fields) until it says it is done
                     (POST /v1/runs/{id}/finish).
    """

    ALL_FIELDS = "all_fields"
    EXPLICIT_YES = "explicit_yes"
    EXTERNAL_EVENT = "external_event"
    RECEIPT = "receipt"
    BY_CALLER = "by_caller"


class Confirm(Strict):
    """The bot shows the collected answers and asks the client to confirm them."""

    # default: «Is everything correct?» in the reply language
    question: LocalizedText | None = None


class EventResult(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REOPEN = "reopen"  # the run goes back to the client: `reopen_fields` are asked again


class EventRule(Strict):
    """What an external event (POST /v1/events) does to a waiting run."""

    result: EventResult
    reopen_fields: list[Key] = Field(default_factory=list)
    # the bot's message to the client; {field_key} and {data.name} are filled in
    say: LocalizedText | None = None


class Waiting(Strict):
    # waiting for an event longer than this → abandoned (+ final JSON); None = forever
    hours: float | None = Field(default=None, gt=0)
    # completion by_caller: what the bot says when the fields ran out (its meaning, in
    # the bot's words); None — a short acknowledgement
    say: LocalizedText | None = None


class AddedFields(Strict):
    """completion by_caller: the fields the caller adds to a running run."""

    # own_words — the bot words the added question itself; as_sent — sent word for word
    wording: Literal["own_words", "as_sent"] = "own_words"
    # an added field is closed with what the client said after this many failed answers
    close_after: int = Field(default=3, ge=1, le=20)
    # at most this many added fields per run; None = no limit
    max_fields: int | None = Field(default=None, ge=1, le=200)


class Resume(Strict):
    # always — continue whenever the client comes back (interview, profile);
    # within_hours — continue only within `hours` of the last activity, then the run
    # is closed as abandoned and the next request starts a new one (booking)
    mode: Literal["always", "within_hours"] = "always"
    hours: float | None = Field(default=None, gt=0)


class Reopen(Strict):
    """A completed run goes back to work when the client amends it: «also change the
    time» after the reschedule was collected changes that request, it does not start a
    new one. Only for completion all_fields / explicit_yes."""

    # within this many hours of the completion; 0 — never reopened
    hours: float = Field(default=24, ge=0, le=720)


class Cancel(Strict):
    description: LocalizedText | None = None


class ActionEvent(StrEnum):
    STARTED = "started"
    FIELD_FILLED = "field_filled"
    FIELD_STUCK = "field_stuck"
    ALL_FIELDS = "all_fields"
    CONFIRMED = "confirmed"
    # collected, waiting for an external event (completion: external_event) — the moment
    # to send the request to the CRM
    WAITING = "waiting"
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
    # the action waits for an operator's «approve» (operator queue, approve_action)
    requires_approval: bool = False


class ScenarioDef(Strict):
    key: Key
    title: LocalizedText
    language: str  # main language: the source for translation
    description: LocalizedText | None = None
    start: Start
    fields: list[FieldDef] = Field(min_length=1)
    completion: Completion = Completion.ALL_FIELDS
    confirm: Confirm | None = None
    # completion external_event: event name → what it does
    events: dict[Key, EventRule] = Field(default_factory=dict)
    waiting: Waiting = Field(default_factory=Waiting)
    # scenarios that can not be open together with this one (both ways)
    incompatible_with: list[Key] = Field(default_factory=list)
    resume: Resume = Field(default_factory=Resume)
    # None — the default: reopened within 24 hours (Reopen)
    reopen: Reopen | None = None
    cancel: Cancel = Field(default_factory=Cancel)
    actions: list[Action] = Field(default_factory=list)
    # rules only the reply model sees when it words a question (not extract, not checks)
    ask_rules: list[LocalizedText] | None = Field(default=None, max_length=50)
    # how many last messages of the dialogue the bot sees next to the run's answers;
    # None — the default (settings.history_messages)
    history_messages: int | None = Field(default=None, ge=1, le=200)
    # completion by_caller: the fields added while the run goes
    added: AddedFields | None = None
    # while a run of it is open in the conversation, the turn is the scenarios' alone: no
    # greeting / farewell / identity reply, no hand-over, no catalog or knowledge search,
    # no other scenario starts by itself, no answer to the client's own question — whatever
    # the client writes goes to the steps (core/pipeline.py, scenarios/questions.py)
    only_scenario: bool | None = None
    # with only_scenario: the client's own question is noticed all the same (one more
    # question of the route, client_question) and the reply answers it before the step
    client_questions: bool | None = None
    # started only by the caller (POST /v1/runs/start), never by the client's words: its
    # `start` questions are never asked, and an open run of it never takes the dialogue
    # because of them
    start_by_caller: bool | None = None
    # its place in the queue of the conversation's scenarios: when the dialogue is free, the
    # paused run with the smallest place gets it, not the one the client answered last
    queue: int | None = Field(default=None, ge=1, le=100)
    # the answers of its text fields are written in the background (scenarios/answers.py):
    # the turn only marks which questions the client's words answer, in full or in part,
    # and the bot goes on asking at once; a long story is not read field by field before
    # the reply
    background_answers: bool | None = None
    # how many times the bot asks a question again after the first time; then the answer
    # is taken as the client said it, even a general one (scenarios/fields.py
    # settle_asked_out). None — no limit: the bot asks until the filters pass
    clarify_limit: int | None = Field(default=None, ge=0, le=20)

    # an empty list / a block of defaults says nothing: kept as absent, so a file that writes
    # it out and one that leaves it out are one version (the editor leaves defaults out)
    @field_validator("ask_rules")
    @classmethod
    def _no_rules(cls, value: list[LocalizedText] | None) -> list[LocalizedText] | None:
        return value or None

    @field_validator("added")
    @classmethod
    def _no_added(cls, value: AddedFields | None) -> AddedFields | None:
        return None if value == AddedFields() else value

    @field_validator("reopen")
    @classmethod
    def _no_reopen(cls, value: Reopen | None) -> Reopen | None:
        return None if value == Reopen() else value

    @field_validator("only_scenario", "client_questions", "start_by_caller", "background_answers")
    @classmethod
    def _no_only(cls, value: bool | None) -> bool | None:
        return True if value else None

    @property
    def reopen_hours(self) -> float:
        """How long after the completion the client may amend a run; 0 — never."""
        if self.completion not in (Completion.ALL_FIELDS, Completion.EXPLICIT_YES):
            return 0
        return (self.reopen or Reopen()).hours

    @property
    def needs_confirm(self) -> bool:
        return self.confirm is not None or self.completion == Completion.EXPLICIT_YES

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

    @property
    def caller_options(self) -> list[str]:
        """Fields whose options come with POST /v1/runs/start: the scenario is started
        only so, never by a client's message."""
        return [f.key for f in self.fields if f.options_from == "caller" and not f.options]

    def asks_start(self, run_open: bool) -> bool:
        """Its `start` questions are asked in a turn: not when the caller alone starts it,
        not while its options can come only with POST /v1/runs/start (no run yet)."""
        if self.start_by_caller:
            return False
        return run_open or not self.caller_options

    def text(self, value: str | dict[str, str], language: str) -> LocalText:
        return pick_text(value, language, self.language)


def parse_definition(data: Any) -> ScenarioDef:
    try:
        return ScenarioDef.model_validate(data)
    except ValidationError as exc:
        # `type` (missing, extra_forbidden…) lets the admin say it in the person's language
        errors = [
            {
                "loc": ".".join(str(p) for p in err.get("loc", ())),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
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
        if f.items is not None:
            for item in f.items.item_fields:
                yield f"fields.{f.key}.items.{item.key}", item.label
        for flt in f.filters:
            yield f"fields.{f.key}.filters.{flt.key}", flt.ask
            for name in ("yes", "no"):
                value = getattr(flt, name)
                if value is not None:
                    yield f"fields.{f.key}.filters.{flt.key}.{name}", value
        if f.show_if is not None:
            for q in f.show_if.questions:
                yield f"fields.{f.key}.show_if.{q.key}", q.ask
    if defn.cancel.description is not None:
        yield "cancel.description", defn.cancel.description
    if defn.confirm is not None and defn.confirm.question is not None:
        yield "confirm.question", defn.confirm.question
    for name, rule in defn.events.items():
        if rule.say is not None:
            yield f"events.{name}.say", rule.say
    for i, action in enumerate(defn.actions):
        if action.do == "message_client" and action.params.get("text"):
            yield f"actions.{i}.text", action.params["text"]
    for i, rule in enumerate(defn.ask_rules or []):
        yield f"ask_rules.{i}", rule
    if defn.waiting.say is not None:
        yield "waiting.say", defn.waiting.say


# typed settings and the type they belong to
TYPED_SETTINGS = {
    "date": FieldType.DATE,
    "number": FieldType.NUMBER,
    "items": FieldType.LIST,
    "attachment": FieldType.ATTACHMENT,
    "receipt": FieldType.RECEIPT,
}


def _check_typed(defn: ScenarioDef, f: FieldDef) -> None:
    for name, field_type in TYPED_SETTINGS.items():
        if getattr(f, name) is not None and f.type != field_type:
            raise AppError(
                "scenario.field_setting_not_allowed",
                {"field": f.key, "type": str(f.type), "setting": name},
            )
    num = f.number
    if num is not None and num.min is not None and num.max is not None and num.min > num.max:
        raise AppError("scenario.number_range_invalid", {"field": f.key})
    if f.items is not None:
        if f.items.min_items > f.items.max_items:
            raise AppError("scenario.list_range_invalid", {"field": f.key})
        keys = [i.key for i in f.items.item_fields]
        if len(set(keys)) != len(keys):
            raise AppError("scenario.list_item_duplicate", {"field": f.key})
    if f.type in ATTACHMENT_TYPES and f.filters:
        # Jev does not see pictures: an attachment is checked by code only
        raise AppError("scenario.filters_not_allowed", {"field": f.key, "type": str(f.type)})
    r = f.receipt
    if r is None:
        return
    if r.amount is not None and r.currency is None:
        raise AppError("scenario.receipt_currency_required", {"field": f.key})
    if r.amount_field is not None:
        ref = r.amount_field
        if ref not in defn.field_keys or defn.field(ref).type != FieldType.CATALOG:
            raise AppError("scenario.receipt_amount_field_invalid", {"field": f.key, "ref": ref})
    if r.quantity_field is not None:
        ref = r.quantity_field
        if ref not in defn.field_keys or defn.field(ref).type != FieldType.NUMBER:
            raise AppError("scenario.receipt_quantity_field_invalid", {"field": f.key, "ref": ref})


def _check_show_if(f: FieldDef) -> None:
    show = f.show_if
    if show is None:
        return
    keys = [q.key for q in show.questions]
    if len(set(keys)) != len(keys):
        raise AppError("scenario.show_if_duplicate", {"field": f.key})
    if show.threshold is None and len(show.questions) > 1:
        raise AppError(
            "scenario.show_if_threshold_required",
            {"field": f.key, "questions": len(show.questions)},
        )
    total = sum(q.weight for q in show.questions)
    if show.needed > total:
        # the field could never be asked
        raise AppError(
            "scenario.show_if_threshold_too_high",
            {"field": f.key, "threshold": show.needed, "max": total},
        )


def _check_completion(defn: ScenarioDef) -> None:
    if defn.completion == Completion.RECEIPT and not any(
        f.type == FieldType.RECEIPT for f in defn.fields
    ):
        raise AppError("scenario.receipt_field_required")
    if defn.completion == Completion.EXTERNAL_EVENT:
        if not defn.events:
            raise AppError("scenario.events_required")
    elif defn.events:
        raise AppError("scenario.events_not_allowed", {"mode": str(defn.completion)})
    if defn.waiting.hours is not None and defn.completion != Completion.EXTERNAL_EVENT:
        raise AppError("scenario.events_not_allowed", {"mode": str(defn.completion)})
    if defn.completion == Completion.BY_CALLER and defn.confirm is not None:
        # the run never ends by itself: there is no moment to confirm everything
        raise AppError("scenario.confirm_not_allowed", {"mode": str(defn.completion)})
    if defn.completion != Completion.BY_CALLER:
        if defn.waiting.say is not None:
            raise AppError("scenario.waiting_say_not_allowed", {"mode": str(defn.completion)})
        if defn.added is not None:
            raise AppError("scenario.added_not_allowed", {"mode": str(defn.completion)})
    for name, rule in defn.events.items():
        if rule.result == EventResult.REOPEN and not rule.reopen_fields:
            raise AppError("scenario.event_reopen_fields_required", {"event": name})
        if rule.result != EventResult.REOPEN and rule.reopen_fields:
            raise AppError("scenario.event_reopen_fields_not_allowed", {"event": name})
        for ref in rule.reopen_fields:
            if ref not in defn.field_keys:
                raise AppError("scenario.event_field_unknown", {"event": name, "field": ref})
    if defn.key in defn.incompatible_with:
        raise AppError("scenario.incompatible_self")


KNOWN_ACTIONS = ("http_post", "notify_telegram", "notify_email", "message_client", "handover")
NOTIFIER_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,99}$")


def _check_action(i: int, action: Action) -> None:
    if action.do == "http_post" and not action.endpoint:
        raise AppError("scenario.action_endpoint_required", {"index": i, "on": action.on})
    if action.do in ("notify_telegram", "notify_email"):
        name = action.params.get("notifier")
        if not isinstance(name, str) or not NOTIFIER_NAME_RE.match(name):
            raise AppError("scenario.action_notifier_required", {"index": i, "on": action.on})
    if action.do == "message_client":
        try:
            _check_text(action.params.get("text") or "")
        except (ValueError, AttributeError, TypeError):
            raise AppError("scenario.action_text_required", {"index": i, "on": action.on}) from None


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
        if f.options_from is not None:
            if f.type != FieldType.CHOICE:
                raise AppError(
                    "scenario.options_from_not_allowed", {"field": f.key, "type": str(f.type)}
                )
            if f.options:
                # the caller's options replace the file's: both would be a puzzle
                raise AppError("scenario.options_from_with_options", {"field": f.key})
        elif f.type == FieldType.CHOICE and len(f.options) < 2:
            raise AppError("scenario.choice_options_missing", {"field": f.key})
        if f.type != FieldType.CHOICE and f.options:
            raise AppError("scenario.options_not_allowed", {"field": f.key, "type": str(f.type)})
        _check_show_if(f)
        if f.type != FieldType.CATALOG and f.catalog is not None:
            raise AppError("scenario.catalog_not_allowed", {"field": f.key, "type": str(f.type)})
        _check_typed(defn, f)
    if cycle := _cycle(defn):
        raise AppError("scenario.ask_after_cycle", {"fields": cycle})
    conditional = {f.key for f in defn.fields if f.show_if is not None}
    for f in defn.fields:
        for ref in f.ask_after:
            if ref in conditional:
                # a hidden field would lock the fields asked after it for good
                raise AppError("scenario.ask_after_conditional", {"field": f.key, "ref": ref})

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
    _check_completion(defn)
    for i, action in enumerate(defn.actions):
        if action.do not in actions:
            raise AppError("scenario.action_unknown", {"index": i, "do": action.do})
        _check_action(i, action)
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
    for f in defn.fields:
        r = f.receipt
        if f.type == FieldType.RECEIPT and (r is None or (r.amount is None and not r.amount_field)):
            # nothing to compare the amount with: every receipt goes to an operator
            warnings.append({"code": "scenario.receipt_amount_missing", "params": {"field": f.key}})
    return warnings
