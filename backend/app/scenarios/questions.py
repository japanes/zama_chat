"""Scenario questions added to the route batch (docs/04 §5 «Вопросы маршрута»):

  start:<scenario>.<key>        boolean — a start sign of a channel scenario (not in focus)
  field:<scenario>.<field>      boolean — "does the message answer this field?": unfilled
                                fields of the focus and paused runs + `field:` start signs;
                                a text field of a scenario with `background_answers` —
                                choice: none / partial / full, and for a field with an
                                answer none / more ("does it add to the answer?"), so the
                                answers are written in the background (scenarios/answers.py)
  stop                          choice  — none / pause_focus / cancel:<scenario>
  change_field                  choice  — none / a field of the focus that has an answer
  client_question               boolean — the message has the client's own question
  confirm:<scenario>            choice  — yes / no / none: the client's answer to «is
                                everything correct?» (only when the focus asked it)
  reopen:<scenario>             boolean — the client amends a request completed lately
                                (ScenarioView.completed, runtime.apply_reopen)

and the state sections Jev sees with them: the scenario in focus with its field states,
the field the bot asked last, the paused scenarios.

Scenario texts are taken in the question language (= the language of the rewritten
text), falling back to the scenario's main language. The wrapper sentences below exist
in en / uk / ru; any other language uses English ones.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from app.db.models import DecisionType, RunStatus
from app.decisions.engine import Question
from app.scenarios.fields import PARTIAL, FieldState, background_field, load_states
from app.scenarios.schema import (
    ATTACHMENT_TYPES,
    FieldDef,
    FieldType,
    ScenarioDef,
    StartQuestion,
)
from app.scenarios.store import LoadedScenario, OpenRun, ScenarioView

STOP = "stop"
CHANGE_FIELD = "change_field"
CLIENT_QUESTION = "client_question"
NONE = "none"
YES = "yes"
NO = "no"
# run.asking while the bot waits for the client's «yes» to the collected answers
CONFIRM = "@confirm"
PAUSE_FOCUS = "pause_focus"
# TODO(review): the Jev probe checks which choice keys are accepted; ":" is assumed fine
CANCEL_PREFIX = "cancel:"
VALUE_PREVIEW = 300
# background_answers: the client's words answer the question in full / in part / add to the
# answer it already has (field questions of choice type)
FULL = "full"
IN_PART = "partial"
MORE = "more"

# TODO(review): wording of the scenario questions to Jev; check on the probe / make eval
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "field": "Does the client's latest message contain an answer to the question «{q}»?",
        "field_hint": "What counts as an answer: {hint}",
        "field_options": "Possible answers: {options}.",
        "field_true": "The message answers this question, fully or in part.",
        "field_false": "The message does not answer this question.",
        "field_none": "The message does not answer this question.",
        "field_partial": "The message answers only part of this question: some of what is "
        "asked is missing.",
        "field_full": "The message answers this question in full.",
        "field_more": "The client has already answered the question «{q}»: «{value}». Does "
        "the client's latest message add something new to this answer?",
        "field_more_none": "The message adds nothing new to this answer.",
        "field_more_more": "The message adds new facts or details to this answer, or what "
        "was missing in it.",
        "yes": "Yes.",
        "no": "No.",
        "stop": "Does the client want to stop or postpone a scenario that is in progress?",
        "stop_none": "The client does not ask to stop or postpone anything.",
        "stop_pause": "The client wants to put «{title}» aside for now and come back to it "
        "later, without cancelling it.",
        "stop_cancel": "The client refuses «{title}» or no longer wants it.",
        "change": "Does the client correct an answer they gave earlier?",
        "change_none": "The client does not correct any earlier answer.",
        "change_option": "The client corrects the earlier answer to «{q}» (it was «{value}»).",
        "client_question": "Does the client's message contain their own question or request "
        "to the bot (not just an answer to the bot's question)?",
        "client_question_true": "The message has the client's own question or request.",
        "client_question_false": "The message only answers the bot or gives information.",
        "filter": "About the client's answer to the question «{q}»: {ask}",
        "show": "About the client's answers in «{title}»: {ask}",
        "confirm": "The bot listed the client's answers for «{title}» and asked whether "
        "everything is correct. What does the client answer?",
        "confirm_yes": "The client confirms: everything is correct, agrees, says yes.",
        "confirm_no": "The client says something is wrong or wants to change something.",
        "confirm_none": "The client neither confirms nor rejects (asks something else or "
        "the answer is unclear).",
        "asked_confirm": "asked the client to confirm the collected answers",
        "reopen": "The client's request «{title}» was collected lately with these "
        "answers: {answers}. Does the client's latest message change or add something to "
        "this very request (not a new, separate request)?",
        "reopen_true": "Yes: the client changes or adds to this request.",
        "reopen_false": "No: the message is not about this request, or it is a new, "
        "separate request.",
    },
    "uk": {
        "field": "Чи є в останньому повідомленні клієнта відповідь на питання «{q}»?",
        "field_hint": "Що вважається відповіддю: {hint}",
        "field_options": "Можливі відповіді: {options}.",
        "field_true": "Повідомлення відповідає на це питання повністю або частково.",
        "field_false": "Повідомлення не відповідає на це питання.",
        "field_none": "Повідомлення не відповідає на це питання.",
        "field_partial": "Повідомлення відповідає лише на частину цього питання: частини "
        "того, про що питають, бракує.",
        "field_full": "Повідомлення відповідає на це питання повністю.",
        "field_more": "Клієнт уже відповів на питання «{q}»: «{value}». Чи додає останнє "
        "повідомлення клієнта щось нове до цієї відповіді?",
        "field_more_none": "Повідомлення нічого нового до цієї відповіді не додає.",
        "field_more_more": "Повідомлення додає до цієї відповіді нові факти чи подробиці або "
        "те, чого в ній бракувало.",
        "yes": "Так.",
        "no": "Ні.",
        "stop": "Чи хоче клієнт зупинити або відкласти сценарій, який зараз триває?",
        "stop_none": "Клієнт не просить нічого зупиняти чи відкладати.",
        "stop_pause": "Клієнт хоче поки відкласти «{title}» і повернутися до цього пізніше, "
        "не скасовуючи.",
        "stop_cancel": "Клієнт відмовляється від «{title}» або більше цього не хоче.",
        "change": "Чи виправляє клієнт відповідь, яку дав раніше?",
        "change_none": "Клієнт не виправляє жодної попередньої відповіді.",
        "change_option": "Клієнт виправляє попередню відповідь на «{q}» (було «{value}»).",
        "client_question": "Чи є в повідомленні клієнта його власне питання або прохання до "
        "бота (а не лише відповідь на питання бота)?",
        "client_question_true": "У повідомленні є власне питання або прохання клієнта.",
        "client_question_false": "Повідомлення лише відповідає боту або дає інформацію.",
        "filter": "Щодо відповіді клієнта на питання «{q}»: {ask}",
        "show": "Щодо відповідей клієнта в «{title}»: {ask}",
        "confirm": "Бот перелічив відповіді клієнта для «{title}» і запитав, чи все "
        "правильно. Що відповідає клієнт?",
        "confirm_yes": "Клієнт підтверджує: усе правильно, погоджується, каже «так».",
        "confirm_no": "Клієнт каже, що щось не так, або хоче щось змінити.",
        "confirm_none": "Клієнт не підтверджує і не заперечує (питає інше або відповідь "
        "незрозуміла).",
        "asked_confirm": "попросив клієнта підтвердити зібрані відповіді",
        "reopen": "Нещодавно було зібрано запит клієнта «{title}» з такими відповідями: "
        "{answers}. Чи змінює або доповнює клієнт в останньому повідомленні саме цей запит "
        "(а не робить новий, окремий)?",
        "reopen_true": "Так: клієнт змінює або доповнює цей запит.",
        "reopen_false": "Ні: повідомлення не про цей запит або це новий, окремий запит.",
    },
    "ru": {
        "field": "Есть ли в последнем сообщении клиента ответ на вопрос «{q}»?",
        "field_hint": "Что считается ответом: {hint}",
        "field_options": "Возможные ответы: {options}.",
        "field_true": "Сообщение отвечает на этот вопрос полностью или частично.",
        "field_false": "Сообщение не отвечает на этот вопрос.",
        "field_none": "Сообщение не отвечает на этот вопрос.",
        "field_partial": "Сообщение отвечает только на часть этого вопроса: части того, о чём "
        "спрашивают, не хватает.",
        "field_full": "Сообщение отвечает на этот вопрос полностью.",
        "field_more": "Клиент уже ответил на вопрос «{q}»: «{value}». Добавляет ли последнее "
        "сообщение клиента что-то новое к этому ответу?",
        "field_more_none": "Сообщение ничего нового к этому ответу не добавляет.",
        "field_more_more": "Сообщение добавляет к этому ответу новые факты или подробности или "
        "то, чего в нём не хватало.",
        "yes": "Да.",
        "no": "Нет.",
        "stop": "Хочет ли клиент остановить или отложить сценарий, который сейчас идёт?",
        "stop_none": "Клиент не просит ничего останавливать или откладывать.",
        "stop_pause": "Клиент хочет пока отложить «{title}» и вернуться к этому позже, не отменяя.",
        "stop_cancel": "Клиент отказывается от «{title}» или больше этого не хочет.",
        "change": "Исправляет ли клиент ответ, который дал раньше?",
        "change_none": "Клиент не исправляет ни одного прежнего ответа.",
        "change_option": "Клиент исправляет прежний ответ на «{q}» (было «{value}»).",
        "client_question": "Есть ли в сообщении клиента его собственный вопрос или просьба к "
        "боту (а не только ответ на вопрос бота)?",
        "client_question_true": "В сообщении есть собственный вопрос или просьба клиента.",
        "client_question_false": "Сообщение только отвечает боту или даёт информацию.",
        "filter": "Об ответе клиента на вопрос «{q}»: {ask}",
        "show": "Об ответах клиента в «{title}»: {ask}",
        "confirm": "Бот перечислил ответы клиента для «{title}» и спросил, всё ли верно. "
        "Что отвечает клиент?",
        "confirm_yes": "Клиент подтверждает: всё верно, соглашается, говорит «да».",
        "confirm_no": "Клиент говорит, что что-то не так, или хочет что-то изменить.",
        "confirm_none": "Клиент не подтверждает и не возражает (спрашивает другое или ответ "
        "непонятен).",
        "asked_confirm": "попросил клиента подтвердить собранные ответы",
        "reopen": "Недавно был собран запрос клиента «{title}» с такими ответами: "
        "{answers}. Меняет или дополняет клиент в последнем сообщении именно этот запрос "
        "(а не делает новый, отдельный)?",
        "reopen_true": "Да: клиент меняет или дополняет этот запрос.",
        "reopen_false": "Нет: сообщение не об этом запросе или это новый, отдельный запрос.",
    },
}
DEFAULT_TEMPLATES = "en"


def templates(language: str) -> dict[str, str]:
    return TEMPLATES.get(language) or TEMPLATES[DEFAULT_TEMPLATES]


def start_key(scenario: str, key: str) -> str:
    return f"start:{scenario}.{key}"


def field_key(scenario: str, field_name: str) -> str:
    return f"field:{scenario}.{field_name}"


def filter_key(scenario: str, field_name: str, flt: str) -> str:
    return f"filter:{scenario}.{field_name}.{flt}"


def show_key(scenario: str, field_name: str, key: str) -> str:
    """A show_if question of a field (scenarios/show.py)."""
    return f"show:{scenario}.{field_name}.{key}"


def confirm_key(scenario: str) -> str:
    return f"confirm:{scenario}"


def reopen_key(scenario: str) -> str:
    return f"reopen:{scenario}"


def cancel_option(scenario: str) -> str:
    return f"{CANCEL_PREFIX}{scenario}"


def start_question_key(scenario: str, q: StartQuestion) -> str:
    """Route question key of a start sign: own question or the field question."""
    if q.field is not None:
        return field_key(scenario, q.field)
    assert q.key is not None
    return start_key(scenario, q.key)


def preview(value: str, limit: int | None = VALUE_PREVIEW) -> str:
    """Whitespace collapsed; cut to `limit` characters, None — whole."""
    value = " ".join(value.split())
    return value if limit is None or len(value) <= limit else value[: limit - 1] + "…"


def title_of(defn: ScenarioDef, language: str) -> str:
    return defn.text(defn.title, language).text


def says_answer(value: object) -> bool:
    """The answer of a `field:` question says the client's words answer the field: «yes» of
    a boolean one, any option but «none» of a choice one (background_answers)."""
    return value is True or value in (FULL, IN_PART, MORE)


def field_question(
    defn: ScenarioDef, f: FieldDef, language: str, fs: FieldState | None = None
) -> Question:
    """`fs` — the field's state in an open run: with background_answers a field that has an
    answer is asked whether the message adds to it. Of an open run, an unsure «it answers»
    is taken as an answer in part (BelowAction.SAFE): the words are read and checked as
    any answer; an unsure start sign starts nothing."""
    q = defn.text(f.question, language)
    t = templates(q.language)
    key = field_key(defn.key, f.key)
    hint = (
        [t["field_hint"].format(hint=defn.text(f.description, language).text)]
        if f.description is not None
        else []
    )
    if background_field(defn, f) and fs is not None and fs.value is not None:
        text = t["field_more"].format(q=q.text, value=preview(fs.value, 200))
        return Question(
            key,
            DecisionType.CHOICE,
            " ".join([text, *hint]),
            {NONE: t["field_more_none"], MORE: t["field_more_more"]},
            neutral=NONE,
            unsure_as={MORE: MORE},
        )
    parts = [t["field"].format(q=q.text), *hint]
    if f.type == FieldType.CHOICE and f.options:
        labels = ", ".join(defn.text(o.label, language).text for o in f.options)
        parts.append(t["field_options"].format(options=labels))
    if background_field(defn, f):
        return Question(
            key,
            DecisionType.CHOICE,
            " ".join(parts),
            {NONE: t["field_none"], IN_PART: t["field_partial"], FULL: t["field_full"]},
            neutral=NONE,
            unsure_as={FULL: IN_PART, IN_PART: IN_PART} if fs is not None else {},
        )
    return Question(
        key,
        DecisionType.BOOLEAN,
        " ".join(parts),
        {"true": t["field_true"], "false": t["field_false"]},
        unsure_as={True: True} if fs is not None else {},
    )


def start_question(defn: ScenarioDef, q: StartQuestion, language: str) -> Question:
    if q.field is not None:
        return field_question(defn, defn.field(q.field), language)
    assert q.key is not None and q.ask is not None
    ask = defn.text(q.ask, language)
    t = templates(ask.language)
    return Question(
        start_key(defn.key, q.key),
        DecisionType.BOOLEAN,
        ask.text,
        {"true": t["yes"], "false": t["no"]},
    )


def stop_question(runs: list[OpenRun], focus: OpenRun | None, language: str) -> Question:
    t = templates(language)
    criteria = {NONE: t["stop_none"]}
    if focus is not None:
        criteria[PAUSE_FOCUS] = t["stop_pause"].format(
            title=title_of(focus.scenario.definition, language)
        )
    for run in runs:
        defn = run.scenario.definition
        text = t["stop_cancel"].format(title=title_of(defn, language))
        if defn.cancel.description is not None:
            text += " " + defn.text(defn.cancel.description, language).text
        criteria[cancel_option(defn.key)] = text
    return Question(STOP, DecisionType.CHOICE, t["stop"], criteria, neutral=NONE)


def change_question(focus: OpenRun, states: dict[str, FieldState], language: str) -> Question:
    t = templates(language)
    defn = focus.scenario.definition
    criteria = {NONE: t["change_none"]}
    for f in defn.fields:
        fs = states[f.key]
        if fs.value is None or not asks_text(f):
            continue
        criteria[f.key] = t["change_option"].format(
            q=defn.text(f.question, language).text, value=preview(fs.value, 200)
        )
    return Question(CHANGE_FIELD, DecisionType.CHOICE, t["change"], criteria, neutral=NONE)


def confirm_question(defn: ScenarioDef, language: str) -> Question:
    t = templates(language)
    return Question(
        confirm_key(defn.key),
        DecisionType.CHOICE,
        t["confirm"].format(title=title_of(defn, language)),
        {YES: t["confirm_yes"], NO: t["confirm_no"], NONE: t["confirm_none"]},
        neutral=NONE,
    )


def reopen_question(run: OpenRun, language: str) -> Question:
    t = templates(language)
    defn = run.scenario.definition
    states = load_states(defn, run.fields)
    answers = "; ".join(
        f"«{defn.text(f.question, language).text}» — «{preview(states[f.key].value or '', 120)}»"
        for f in defn.fields
        if states[f.key].value is not None and asks_text(f)
    )
    return Question(
        reopen_key(defn.key),
        DecisionType.BOOLEAN,
        t["reopen"].format(title=title_of(defn, language), answers=answers or "—"),
        {"true": t["reopen_true"], "false": t["reopen_false"]},
    )


def asks_text(f: FieldDef) -> bool:
    """Fields the client answers with words (attachments and receipts come as files)."""
    return f.type not in ATTACHMENT_TYPES


def client_question(language: str) -> Question:
    t = templates(language)
    return Question(
        CLIENT_QUESTION,
        DecisionType.BOOLEAN,
        t["client_question"],
        {"true": t["client_question_true"], "false": t["client_question_false"]},
    )


@dataclass
class ScenarioQuestions:
    questions: list[Question] = field(default_factory=list)
    sections: list[tuple[str, str]] = field(default_factory=list)

    @property
    def keys(self) -> list[str]:
        return [q.key for q in self.questions]


def _asks_data(run: OpenRun) -> bool:
    return run.status in (RunStatus.FOCUS, RunStatus.PAUSED)


def build_questions(view: ScenarioView, language: str) -> ScenarioQuestions:
    """All scenario questions of this turn, in a stable order (start signs in channel
    order, then fields, then stop / change_field / client_question). While a scenario with
    `only_scenario` is open, no other scenario starts by itself and the client asks the bot
    nothing of their own: no start signs, no client_question — unless the scenario has
    `client_questions`."""
    found: dict[str, Question] = {}

    def add(q: Question) -> None:
        found.setdefault(q.key, q)

    focus = view.focus
    only = view.only_scenario
    for scenario in () if only else view.channel:
        if focus is not None and scenario.key == focus.scenario.key:
            continue  # the focus is not a start candidate
        run = view.run_for(scenario.key)
        if run is not None and run.status == RunStatus.WAITING:
            continue  # collected, waiting for an event: never started again meanwhile
        if not scenario.definition.asks_start(run is not None):
            continue  # started only by POST /v1/runs/start
        for q in scenario.definition.start.questions:
            add(start_question(scenario.definition, q, language))

    for run in view.runs:
        if not _asks_data(run):
            continue
        defn = run.scenario.definition
        states = load_states(defn, run.fields)
        for f in defn.fields:
            fs = states[f.key]
            if not asks_text(f):
                continue
            if not fs.complete:
                add(field_question(defn, f, language, fs))
            elif background_field(defn, f) and not fs.skipped:
                # the client may add to an answer already taken: it is completed
                add(field_question(defn, f, language, fs))

    out = ScenarioQuestions(questions=list(found.values()))
    if view.runs:
        out.questions.append(stop_question(view.runs, focus, language))
    if focus is not None:
        states = load_states(focus.scenario.definition, focus.fields)
        if any(fs.value is not None for fs in states.values()):
            out.questions.append(change_question(focus, states, language))
        if focus.asking == CONFIRM:
            out.questions.append(confirm_question(focus.scenario.definition, language))
    if not only:
        # a scenario with only_scenario open: nothing else is taken up by itself
        out.questions.extend(reopen_question(run, language) for run in view.completed)
    if view.client_questions:
        out.questions.append(client_question(language))
    out.sections = list(state_sections(view, language))
    return out


def _field_line(defn: ScenarioDef, f: FieldDef, fs: FieldState, language: str) -> str:
    q = defn.text(f.question, language).text
    if fs.hidden and not fs.complete:
        # show_if said it does not apply: the bot does not ask it
        said = f": «{preview(fs.value)}»" if fs.value is not None else ""
        return f"- {f.key} «{q}»: not asked: does not apply{said}"
    if fs.closed:
        return f"- {f.key} «{q}»: closed after several attempts: «{preview(fs.value or '')}»"
    if fs.pending and fs.value is None:
        return f"- {f.key} «{q}»: answered, the answer is being recorded"
    if fs.value is None:
        return f"- {f.key} «{q}»: no answer yet"
    if fs.state == PARTIAL:
        status = "incomplete answer"
    elif fs.needs_recheck:
        status = "answer to re-check"
    else:
        status = "answered"
    return f"- {f.key} «{q}»: {status}: «{preview(fs.value)}»"


def state_sections(view: ScenarioView, language: str) -> Iterator[tuple[str, str]]:
    focus = view.focus
    if focus is not None:
        defn = focus.scenario.definition
        states = load_states(defn, focus.fields)
        lines = [_field_line(defn, f, states[f.key], language) for f in defn.fields]
        if focus.asking == CONFIRM:
            lines.append(f"The bot's last question: {templates('en')['asked_confirm']}")
        elif focus.asking is not None:
            lines.append(f"The bot's last question was about: {focus.asking}")
        yield f"Scenario in progress: {title_of(defn, language)}", "\n".join(lines)
    others = [r for r in view.runs if r is not focus]
    if others:
        lines = [
            f"- {title_of(r.scenario.definition, language)} ({r.status})"
            for r in sorted(others, key=lambda r: r.started_at)
        ]
        yield "Other open scenarios", "\n".join(lines)


def scenario_by_key(view: ScenarioView) -> dict[str, LoadedScenario]:
    found = {s.key: s for s in view.channel}
    for run in view.runs:
        found.setdefault(run.scenario.key, run.scenario)
    return found
