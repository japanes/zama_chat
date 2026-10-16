"""RespondPlan: what the reply must do for the scenarios — a pure function, no LLM
(docs/04 §5, step 5). The reply model only words it:

  answer the client's own question (if any) → acknowledge accepted answers →
  ask the next field / clarify a partial answer / ask to confirm the collected answers /
  say the scenario is done, cancelled or passed on and waiting.

A side question is answered and, in the same message, the bot returns to the next
scenario question.

`known` is the card of the dialogue: every answer the run has, whole, with how the bot
asked it — the reply model takes the details of a question from it and from the dialogue,
and trusts the card when they differ (prompt `scenario_card`). It goes with every reply a
scenario is in: the run in the focus, else the runs something happened to in this turn.
"""

from dataclasses import dataclass, field
from typing import Any

from app.db.models import RunStatus
from app.scenarios.events import PAUSED, RECEIPT_REVIEW
from app.scenarios.fields import PARTIAL, FieldState, load_states
from app.scenarios.questions import CONFIRM
from app.scenarios.schema import ATTACHMENT_TYPES, ActionEvent, FieldType, ScenarioDef
from app.scenarios.store import ScenarioView
from app.scenarios.typed import ISSUE_PREFIX, clarify_text
from app.scenarios.work import WorkRun, focus_of

# scenario_runs.data.waiting_for of a run whose fields ran out (completion by_caller)
WAITING_FIELDS = "fields"

# TODO(review): the default «is everything correct?» when the scenario has no own text
CONFIRM_QUESTION = {
    "en": "Is everything correct?",
    "uk": "Усе правильно?",
    "ru": "Всё верно?",
}


@dataclass
class CatalogAsk:
    """What goes with the question of a catalog field (scenarios/catalog.py)."""

    options: list[str] = field(default_factory=list)  # numbered items with prices
    more: bool = False  # more items fit than are listed
    categories: list[str] = field(default_factory=list)  # named when no items are listed


@dataclass
class AskStep:
    field: str
    question: str
    type: str = FieldType.TEXT
    hint: str | None = None
    options: list[str] = field(default_factory=list)
    # a receipt field: what to pay {amount, currency}
    payment: dict[str, Any] | None = None
    # a catalog field: more items fit than `options` has; the categories to name instead
    more_options: bool = False
    categories: list[str] = field(default_factory=list)

    @property
    def attachment(self) -> bool:
        return self.type in ATTACHMENT_TYPES


@dataclass
class ConfirmStep:
    question: str
    summary: list[dict[str, str]] = field(default_factory=list)  # [{question, answer}]
    rejected: bool = False  # the client said something is wrong


@dataclass
class ClarifyStep:
    field: str
    question: str
    client_answer: str
    missing: list[str] = field(default_factory=list)


@dataclass
class RespondPlan:
    scenario: str | None = None  # key of the scenario in focus
    # the card: [{question, asked?, answer, incomplete?, scenario?}]
    known: list[dict[str, Any]] = field(default_factory=list)
    # the focus scenario's own rules for wording a question (scenario file `ask_rules`)
    ask_rules: list[str] = field(default_factory=list)
    answer_client_question_first: bool = False
    accepted: list[dict[str, str]] = field(default_factory=list)  # [{field, question, answer}]
    ask: AskStep | None = None
    clarify: ClarifyStep | None = None
    confirm: ConfirmStep | None = None
    # requests passed on: [{title, for: event | receipt_review, new}]
    waiting: list[dict[str, Any]] = field(default_factory=list)
    started: list[str] = field(default_factory=list)  # titles
    completed: list[str] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    paused: list[str] = field(default_factory=list)
    # scenario texts are not in the reply language: the reply model translates
    texts_language: str | None = None
    # a bot turn: the client wrote nothing, the bot speaks first (core/pipeline.py)
    bot_turn: bool = False

    @property
    def asks(self) -> bool:
        return self.ask is not None or self.clarify is not None or self.confirm is not None

    def to_prompt(self, rules: list[str]) -> dict[str, Any]:
        """The `scenario_step` object of the user prompt."""
        out: dict[str, Any] = {
            "rules": rules,
            "answer_client_question_first": self.answer_client_question_first,
        }
        if self.ask_rules and self.asks:
            out["rules"] = [*rules, *self.ask_rules]
        if self.known:
            out["known"] = self.known
        if self.accepted:
            # without the internal field ids
            out["accepted_answers"] = [
                {"question": a["question"], "answer": a["answer"]} for a in self.accepted
            ]
        if self.ask is not None:
            ask: dict[str, Any] = {"question": self.ask.question}
            if self.ask.hint:
                ask["hint"] = self.ask.hint
            if self.ask.options:
                ask["options"] = self.ask.options
            if self.ask.type == FieldType.CATALOG:
                # the reply names only what the catalog has (the rules of scenario_step)
                ask["from_catalog"] = True
                if self.ask.more_options:
                    ask["more_options"] = True
                if self.ask.categories:
                    ask["categories"] = self.ask.categories
            if self.ask.attachment:
                ask["attachment"] = True
            if self.ask.payment:
                ask["payment"] = self.ask.payment
            out["ask_next"] = ask
        if self.clarify is not None:
            out["clarify"] = {
                "question": self.clarify.question,
                "client_answer": self.clarify.client_answer,
                "missing": self.clarify.missing,
            }
        if self.confirm is not None:
            confirm: dict[str, Any] = {
                "question": self.confirm.question,
                "summary": self.confirm.summary,
            }
            if self.confirm.rejected:
                confirm["rejected"] = True
            out["confirm"] = confirm
        if self.waiting:
            out["waiting"] = self.waiting
        if self.started:
            out["scenario_started"] = ", ".join(self.started)
        if self.completed:
            out["scenario_completed"] = ", ".join(self.completed)
        if self.cancelled:
            out["scenario_cancelled"] = ", ".join(self.cancelled)
        if self.paused:
            out["scenario_paused"] = ", ".join(self.paused)
        if self.texts_language:
            out["texts_language"] = self.texts_language
        if self.bot_turn:
            out["bot_turn"] = True
        return out

    def summary(self) -> dict[str, Any]:
        """For the turn trace."""
        return {
            "scenario": self.scenario,
            "answer_client_question_first": self.answer_client_question_first,
            "accepted": [a["field"] for a in self.accepted],
            "known": len(self.known),
            "ask": self.ask.field if self.ask else None,
            "clarify": self.clarify.field if self.clarify else None,
            "confirm": (
                ("rejected" if self.confirm.rejected else "asked") if self.confirm else None
            ),
            "waiting": [w["title"] for w in self.waiting if w.get("new")],
            "completed": self.completed,
            "cancelled": self.cancelled,
        }


class _Texts:
    """Picks scenario texts in the reply language and remembers when one was missing."""

    def __init__(self, language: str) -> None:
        self.language = language
        self.fallback: str | None = None

    def __call__(self, defn: ScenarioDef, value: str | dict[str, str]) -> str:
        picked = defn.text(value, self.language)
        if not picked.exact and picked.language != self.language:
            self.fallback = picked.language
        return picked.text


def _missing(run: WorkRun, key: str, texts: _Texts) -> list[str]:
    """What is wrong with a partial answer: the failed filters' texts and, for a value
    code rejected (date in the past, short phone…), the issue's text."""
    defn = run.definition
    f = defn.field(key)
    fs = run.states[key]
    failed = set(fs.failed_filters)
    out = [texts(defn, flt.ask) for flt in f.filters if flt.key in failed]
    for item in fs.failed_filters:
        if item.startswith(ISSUE_PREFIX):
            out.append(clarify_text(item[len(ISSUE_PREFIX) :], fs.issue_params, texts.language))
    return out


def _card_rows(
    defn: ScenarioDef, states: dict[str, FieldState], texts: _Texts, title: str | None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in defn.fields:
        fs = states[f.key]
        if fs.value is None or fs.skipped:
            continue
        row: dict[str, Any] = {"question": texts(defn, f.question)}
        if fs.asked:
            row["asked"] = fs.asked
        row["answer"] = fs.value
        if not fs.complete:
            row["incomplete"] = True
        if title is not None:
            row["scenario"] = title
        out.append(row)
    return out


def card(runs: list[WorkRun], texts: _Texts) -> list[dict[str, Any]]:
    """`known`: the answers of the runs, whole, each with how the bot asked it. Several
    runs → each entry names its scenario."""
    out: list[dict[str, Any]] = []
    for run in runs:
        defn = run.definition
        title = texts(defn, defn.title) if len(runs) > 1 else None
        out += _card_rows(defn, run.states, texts, title)
    return out


def view_card(view: ScenarioView, language: str) -> list[dict[str, Any]]:
    """The card of the run in the focus before the turn — for the rewrite step, which
    runs before the scenarios do."""
    focus = view.focus
    if focus is None:
        return []
    defn = focus.scenario.definition
    return _card_rows(defn, load_states(defn, focus.fields), _Texts(language), None)


def _confirm_step(run: WorkRun, texts: _Texts) -> ConfirmStep:
    defn = run.definition
    summary = [
        {"question": texts(defn, f.question), "answer": run.states[f.key].value or ""}
        for f in defn.fields
        if f.type != FieldType.RECEIPT and run.states[f.key].value
    ]
    own = defn.confirm.question if defn.confirm is not None else None
    question = (
        texts(defn, own)
        if own is not None
        else CONFIRM_QUESTION.get(texts.language, CONFIRM_QUESTION["en"])
    )
    return ConfirmStep(question, summary, run.confirm_rejected)


def _ask_step(
    run: WorkRun,
    texts: _Texts,
    catalog_options: dict[str, CatalogAsk],
    payment: dict[str, dict[str, Any]],
) -> tuple[AskStep | None, ClarifyStep | None]:
    if run.asking is None or run.asking == CONFIRM:
        return None, None
    defn = run.definition
    f = defn.field(run.asking)
    fs = run.states[f.key]
    question = texts(defn, f.question)
    if fs.state == PARTIAL and fs.value:
        return None, ClarifyStep(f.key, question, fs.value, _missing(run, f.key, texts))
    hint = texts(defn, f.description) if f.description is not None else None
    step = AskStep(f.key, question, str(f.type), hint, payment=payment.get(f"{defn.key}.{f.key}"))
    if f.type == FieldType.CHOICE:
        step.options = [texts(defn, o.label) for o in f.options]
    elif f.type == FieldType.CATALOG:
        # numbered catalog items or the categories (scenarios/catalog.py); neither → an
        # open question
        shown = catalog_options.get(f"{defn.key}.{f.key}") or CatalogAsk()
        step.options = list(shown.options)
        step.more_options = shown.more
        step.categories = list(shown.categories)
    return step, None


def build_plan(
    runs: list[WorkRun],
    *,
    language: str,
    client_question: bool,
    wants_human: bool = False,
    catalog_options: dict[str, CatalogAsk] | None = None,
    payment: dict[str, dict[str, Any]] | None = None,
    only_scenario: bool = False,
) -> RespondPlan | None:
    """None when the scenarios have nothing to say in this reply (no focus, nothing
    started / finished): the reply is an ordinary answer. `only_scenario` (an open run of
    such a scenario) — never None: there is no ordinary answer in that conversation."""
    texts = _Texts(language)
    plan = RespondPlan(answer_client_question_first=client_question)
    for run in runs:
        defn = run.definition
        title = texts(defn, defn.title)
        names = run.event_names()
        if ActionEvent.STARTED in names:
            plan.started.append(title)
        if ActionEvent.COMPLETED in names:
            plan.completed.append(title)
        if run.status == RunStatus.WAITING:
            waiting: dict[str, Any] = {
                "title": title,
                "for": run.data.get("waiting_for") or "event",
                "new": ActionEvent.WAITING in names or RECEIPT_REVIEW in names,
            }
            if waiting["for"] == WAITING_FIELDS and defn.waiting.say is not None:
                waiting["say"] = texts(defn, defn.waiting.say)
            plan.waiting.append(waiting)
        if ActionEvent.CANCELLED in names:
            plan.cancelled.append(title)
        if PAUSED in names:
            plan.paused.append(title)
        for key in run.filled:
            f = defn.field(key)
            value = run.states[key].value or ""
            plan.accepted.append(
                {"field": f"{defn.key}.{key}", "question": texts(defn, f.question), "answer": value}
            )

    focus = focus_of(runs)
    if focus is not None:
        plan.scenario = focus.key
        plan.known = card([focus], texts)
        if not wants_human:
            plan.ask, plan.clarify = _ask_step(focus, texts, catalog_options or {}, payment or {})
            if focus.asking == CONFIRM:
                plan.confirm = _confirm_step(focus, texts)
            plan.ask_rules = [texts(focus.definition, r) for r in focus.definition.ask_rules or []]
        if plan.asks:
            # Another run ran out of questions and gave the conversation back to this one:
            # the client hears this one's question, not that one's «enough for now» — the
            # phrase would read as the bot going quiet with a question still to ask.
            plan.waiting = [w for w in plan.waiting if w["for"] != WAITING_FIELDS]
    else:
        plan.known = card([r for r in runs if r.events], texts)
    happened = (
        plan.started
        or plan.completed
        or plan.cancelled
        or plan.paused
        or plan.accepted
        or any(w["new"] for w in plan.waiting)
    )
    if focus is None and not happened and not only_scenario:
        return None
    plan.texts_language = texts.fallback
    return plan
