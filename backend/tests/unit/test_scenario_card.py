"""The card of the dialogue and the new settings of a scenario, without a database: what
the bot sees when it asks (`known`, `ask_rules`, `waiting.say`), what the checks see (how
the bot asked, the whole answer), when a field with `show_if` is checked and what its
verdict is, the run's own fields (added ones, the caller's options). And the main rule: a
scenario without the new names reads as before — no new version."""

from app.db.models import RunStatus
from app.scenarios.checks import CheckItem, check_state
from app.scenarios.fields import (
    FILLED,
    PARTIAL,
    FieldState,
    all_complete,
    dump_states,
    next_field,
)
from app.scenarios.loader import SUFFIXES, parse_yaml
from app.scenarios.plan import WAITING_FIELDS, build_plan
from app.scenarios.schema import ActionEvent, ScenarioDef, ShowIf, parse_definition
from app.scenarios.show import ShowItem, shows_due, verdict
from app.scenarios.store import effective_definition
from app.scenarios.work import WorkRun
from app.settings import PROJECT_ROOT
from tests.scenario_helpers import interview_data, loaded, make_def, open_run

STORY = "Я вклала останні 1000 євро у перший бізнес. " * 60  # longer than any old preview


def story_data(**over):
    data = {
        "key": "facts",
        "title": {"uk": "Фактаж", "en": "Facts"},
        "language": "uk",
        "start": {"questions": [{"key": "tells", "ask": {"uk": "Розповідає?", "en": "Tells?"}}]},
        "completion": "by_caller",
        "ask_rules": [{"uk": "Не припускай подію.", "en": "Do not assume the event."}],
        "fields": [
            {"key": "story", "question": {"uk": "Розкажіть історію", "en": "Tell the story"}},
            {
                "key": "stakes",
                "question": {"uk": "Що було на кону?", "en": "What was at stake?"},
                "ask_after": ["story"],
                "show_if": {
                    "questions": [
                        {"key": "risked", "ask": {"uk": "Ризикувала?", "en": "Took a risk?"}}
                    ]
                },
            },
            {
                "key": "age",
                "question": {"uk": "Скільки вам було?", "en": "How old were you?"},
                "ask_after": ["story"],
            },
        ],
    }
    data.update(over)
    return data


FACTS = loaded(make_def(story_data()))


def work(scenario=FACTS, status=RunStatus.FOCUS, asking=None, **fields):
    """A run as the turn found it: `fields` are its states at the start of the turn."""
    states = {f.key: FieldState() for f in scenario.definition.fields}
    raw = dump_states({**states, **fields})
    run = WorkRun.from_open(open_run(scenario, status, fields=raw))
    run.asking = asking
    return run


# --- what the bot sees when it asks ---------------------------------------------------------


def test_the_card_has_every_answer_whole_with_how_the_bot_asked_it():
    run = work(
        asking="age",
        story=FieldState(state=FILLED, value=STORY, asked="Розкажіть, що сталося?"),
        # the client corrected «25» to «27»: the card has only what stands now
        age=FieldState(state=PARTIAL, value="27", failed_filters=[]),
    )
    plan = build_plan([run], language="uk", client_question=False)
    assert plan.known == [
        {"question": "Розкажіть історію", "asked": "Розкажіть, що сталося?", "answer": STORY},
        {"question": "Скільки вам було?", "answer": "27", "incomplete": True},
    ]
    assert plan.to_prompt([])["known"] == plan.known
    assert "25" not in str(plan.to_prompt([]))


def test_the_card_leaves_out_skipped_and_empty_fields():
    skipped = FieldState(state=FILLED, skipped=True)
    run = work(asking="age", story=skipped)
    plan = build_plan([run], language="uk", client_question=False)
    assert plan.known == []
    assert "known" not in plan.to_prompt([])


def test_ask_rules_go_only_to_a_reply_that_asks():
    run = work(asking="age", story=FieldState(state=FILLED, value="історія"))
    plan = build_plan([run], language="en", client_question=False)
    assert plan.ask.field == "age"
    assert plan.to_prompt(["engine rule"])["rules"] == ["engine rule", "Do not assume the event."]

    # nothing to ask: the rules of wording a question are not needed
    waiting = work(status=RunStatus.WAITING)
    waiting.data["waiting_for"] = WAITING_FIELDS
    waiting.add(ActionEvent.ALL_FIELDS)
    waiting.add(ActionEvent.WAITING)
    plan = build_plan([waiting], language="en", client_question=False)
    assert plan.to_prompt(["engine rule"])["rules"] == ["engine rule"]


def test_a_scenario_without_ask_rules_keeps_the_engine_rules_as_they_are():
    run = work(loaded(make_def()), asking="niche")
    assert build_plan([run], language="uk", client_question=False).to_prompt(["r"])["rules"] == [
        "r"
    ]


def test_waiting_for_fields_says_what_the_scenario_wants():
    data = story_data(waiting={"say": {"uk": "Дякую, досить.", "en": "Thanks, enough."}})
    run = work(loaded(make_def(data)), status=RunStatus.WAITING)
    run.data["waiting_for"] = WAITING_FIELDS
    run.add(ActionEvent.ALL_FIELDS)
    run.add(ActionEvent.WAITING)
    plan = build_plan([run], language="en", client_question=False)
    assert plan.waiting == [
        {"title": "Facts", "for": "fields", "new": True, "say": "Thanks, enough."}
    ]
    # without `say`: the waiting is told, the words are the bot's
    run = work(status=RunStatus.WAITING)
    run.data["waiting_for"] = WAITING_FIELDS
    run.add(ActionEvent.ALL_FIELDS)
    run.add(ActionEvent.WAITING)
    plan = build_plan([run], language="en", client_question=False)
    assert plan.waiting == [{"title": "Facts", "for": "fields", "new": True}]


# --- what the checks see --------------------------------------------------------------------


def test_the_check_sees_how_the_bot_asked_and_the_whole_answer():
    defn = make_def(story_data())
    states = {f.key: FieldState() for f in defn.fields}
    states["story"] = FieldState(state=FILLED, value=STORY, asked="Що сталося?")
    states["age"] = FieldState(state=PARTIAL, value="Так", asked="Вам було більше 25?")
    state = check_state([CheckItem(defn, defn.field("age"), "Так", states)], "uk")
    assert "the bot asked: «Вам було більше 25?»" in state
    assert "client's answer: «Так»" in state
    # the other answers are context, whole as well
    assert STORY.strip() in state
    assert "the bot asked: «Що сталося?»" in state


def test_a_show_check_sees_every_answer_of_the_run_complete_or_not():
    defn = make_def(story_data())
    states = {f.key: FieldState() for f in defn.fields}
    states["story"] = FieldState(state=FILLED, value="історія")
    states["age"] = FieldState(state=PARTIAL, value="27")
    plain = check_state([], "uk", shows=None)
    assert plain == ""
    state = check_state([], "uk", shows=[ShowItem(defn, defn.field("stakes"), states)])
    assert "Client's answers in «Фактаж»:" in state
    assert "«27» (incomplete)" in state


# --- show_if ----------------------------------------------------------------------------------


def _show(**over) -> ShowIf:
    data = {"questions": [{"key": "a", "ask": "a?", "weight": 1}, {"key": "b", "ask": "b?"}]}
    data.update(over)
    return ShowIf.model_validate(data)


def test_verdict_sums_the_yes_weights_like_start():
    show = _show(threshold=2)
    assert verdict(show, {"a": True, "b": True}) == (False, False)  # shown
    assert verdict(show, {"a": True, "b": False}) == (True, False)  # hidden
    # «b» has no answer: it could still reach the threshold — on_unknown decides
    assert verdict(show, {"a": True, "b": None}) == (False, True)
    assert verdict(_show(threshold=2, on_unknown="skip"), {"a": True, "b": None}) == (True, True)
    # one sign without a threshold: its weight
    single = ShowIf.model_validate({"questions": [{"key": "a", "ask": "a?"}]})
    assert verdict(single, {"a": False}) == (True, False)


def test_a_field_is_checked_once_ask_after_is_complete():
    before = work(asking="story")
    assert shows_due([before], set()) == []
    run = work(asking="age", story=FieldState(state=FILLED, value="історія"))
    assert shows_due([run], set()) == [(run, "stakes")]
    # checked already in this turn
    assert shows_due([run], {("facts", "stakes")}) == []
    # a run that does not take data is not checked
    assert shows_due([work(status=RunStatus.WAITING)], set()) == []


def test_every_turn_rechecks_after_a_changed_answer_once_does_not():
    checked = {"hidden": True, "answers": {"risked": False}, "unknown": False, "at": "t"}
    story = FieldState(state=FILLED, value="історія")

    def ran(data):
        hidden = FieldState(hidden=True, show=dict(checked))
        return work(loaded(make_def(data)), asking="age", story=story, stakes=hidden)

    same = ran(story_data())
    assert shows_due([same], set()) == []  # nothing changed in this turn
    changed = ran(story_data())
    changed.states["age"] = FieldState(state=FILLED, value="27, я взяла кредит")
    assert shows_due([changed], set()) == [(changed, "stakes")]

    once = story_data()
    once["fields"][1]["show_if"]["recheck"] = "once"
    run = ran(once)
    run.states["age"] = FieldState(state=FILLED, value="27, я взяла кредит")
    assert shows_due([run], set()) == []


def test_a_hidden_field_is_done_and_keeps_what_was_said():
    defn = make_def(story_data())
    states = {f.key: FieldState() for f in defn.fields}
    states["story"] = FieldState(state=FILLED, value="історія")
    states["stakes"] = FieldState(state=PARTIAL, value="мабуть гроші", hidden=True)
    states["age"] = FieldState(state=FILLED, value="27")
    assert next_field(defn, states) is None
    assert all_complete(states)
    assert states["stakes"].value == "мабуть гроші"


# --- the run's own fields ---------------------------------------------------------------------


def test_the_run_sees_the_caller_options_and_the_added_fields():
    data = story_data()
    data["fields"].append(
        {
            "key": "angle",
            "type": "choice",
            "question": "Який ракурс?",
            "options_from": "caller",
        }
    )
    defn = make_def(data)
    assert effective_definition(defn, None) is defn
    extra = {
        "options": {"angle": [{"key": "a1", "label": "Чоловік був проти — сумнів"}]},
        "fields": [{"key": "q1", "question": "Скільки ви вклали?", "added_at": "2026-10-15"}],
    }
    run_defn = effective_definition(defn, extra)
    assert [f.key for f in run_defn.fields] == ["story", "stakes", "age", "angle", "q1"]
    assert [(o.key, o.label) for o in run_defn.field("angle").options] == [
        ("a1", "Чоловік був проти — сумнів")
    ]
    # the scenario itself does not change
    assert defn.field("angle").options == []


# --- the main rule ----------------------------------------------------------------------------

NEW_NAMES = {"show_if", "ask_rules", "history_messages", "added", "options_from"}


def _dumped(defn: ScenarioDef) -> dict:
    return defn.model_dump(mode="json", exclude_none=True)


def _walk_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _walk_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _walk_keys(v)}
    return set()


def test_a_scenario_without_the_new_names_is_stored_as_before():
    # the stored definition decides whether an upload is a new version (store.save_scenario)
    files = [
        p for p in (PROJECT_ROOT / "scenarios").rglob("*") if p.suffix in SUFFIXES and p.is_file()
    ]
    for data in [interview_data(), *(parse_yaml(p.read_text()) for p in files)]:
        dumped = _dumped(parse_definition(data))
        assert not (_walk_keys(dumped) & NEW_NAMES), data.get("key")
        assert "say" not in dumped.get("waiting", {}), data.get("key")
