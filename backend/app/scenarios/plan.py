"""RespondPlan: what the reply must do for the scenarios — a pure function, no LLM
(docs/04 §5, step 5). The reply model only words it:

  answer the client's own question (if any) → acknowledge accepted answers →
  ask the next field / clarify a partial answer / ask to confirm the collected answers /
  say the scenario is done, cancelled or passed on and waiting.

A side question is answered and, in the same message, the bot returns to the next
scenario question.
"""

from dataclasses import dataclass, field
from typing import Any

from app.db.models import RunStatus
from app.scenarios.events import PAUSED, RECEIPT_REVIEW
from app.scenarios.fields import PARTIAL
from app.scenarios.questions import CONFIRM
from app.scenarios.schema import ATTACHMENT_TYPES, ActionEvent, FieldType, ScenarioDef
from app.scenarios.typed import ISSUE_PREFIX, clarify_text
from app.scenarios.work import WorkRun, focus_of

# TODO(review): the default «is everything correct?» when the scenario has no own text
CONFIRM_QUESTION = {
    "en": "Is everything correct?",
    "uk": "Усе правильно?",
    "ru": "Всё верно?",
}


@dataclass
class AskStep:
    field: str
    question: str
    type: str = FieldType.TEXT
    hint: str | None = None
    options: list[str] = field(default_factory=list)
    # a receipt field: what to pay {amount, currency}
    payment: dict[str, Any] | None = None

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

    @property
    def asks(self) -> bool:
        return self.ask is not None or self.clarify is not None or self.confirm is not None

    def to_prompt(self, rules: list[str]) -> dict[str, Any]:
        """The `scenario_step` object of the user prompt."""
        out: dict[str, Any] = {
            "rules": rules,
            "answer_client_question_first": self.answer_client_question_first,
        }
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
        return out

    def summary(self) -> dict[str, Any]:
        """For the turn trace."""
        return {
            "scenario": self.scenario,
            "answer_client_question_first": self.answer_client_question_first,
            "accepted": [a["field"] for a in self.accepted],
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
    catalog_options: dict[str, list[str]],
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
    options: list[str] = []
    if f.type == FieldType.CHOICE:
        options = [texts(defn, o.label) for o in f.options]
    elif f.type == FieldType.CATALOG:
        # numbered catalog items (scenarios/catalog.py); none → an open question
        options = list(catalog_options.get(f"{defn.key}.{f.key}", []))
    return (
        AskStep(f.key, question, str(f.type), hint, options, payment.get(f"{defn.key}.{f.key}")),
        None,
    )


def build_plan(
    runs: list[WorkRun],
    *,
    language: str,
    client_question: bool,
    wants_human: bool = False,
    catalog_options: dict[str, list[str]] | None = None,
    payment: dict[str, dict[str, Any]] | None = None,
) -> RespondPlan | None:
    """None when the scenarios have nothing to say in this reply (no focus, nothing
    started / finished): the reply is an ordinary answer."""
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
            plan.waiting.append(
                {
                    "title": title,
                    "for": run.data.get("waiting_for") or "event",
                    "new": ActionEvent.WAITING in names or RECEIPT_REVIEW in names,
                }
            )
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
        if not wants_human:
            plan.ask, plan.clarify = _ask_step(focus, texts, catalog_options or {}, payment or {})
            if focus.asking == CONFIRM:
                plan.confirm = _confirm_step(focus, texts)
    happened = (
        plan.started
        or plan.completed
        or plan.cancelled
        or plan.paused
        or plan.accepted
        or any(w["new"] for w in plan.waiting)
    )
    if focus is None and not happened:
        return None
    plan.texts_language = texts.fallback
    return plan
