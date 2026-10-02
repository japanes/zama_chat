from app.scenarios.fields import (
    EMPTY,
    FILLED,
    PARTIAL,
    STUCK_AFTER,
    FieldState,
    all_complete,
    answers,
    dependents,
    load_states,
    mark_dependents,
    merge_value,
    next_field,
    recheck_ready,
    register_ask,
    set_answer,
    set_checked,
)
from tests.scenario_helpers import interview_data, make_def
from tests.turn_ctx import T0

DEFN = make_def()


def filled(value="x"):
    return FieldState(state=FILLED, value=value)


def test_missing_fields_are_empty_in_definition_order():
    states = load_states(DEFN, {"offer": {"state": "filled", "value": "торти"}})
    assert list(states) == ["niche", "offer", "positioning"]
    assert states["niche"].state == EMPTY
    assert states["offer"].complete


def test_unknown_keys_in_the_json_are_ignored():
    states = load_states(DEFN, {"niche": {"state": "filled", "value": "x", "junk": 1}})
    assert states["niche"].value == "x"


def test_next_field_follows_the_order_and_ask_after():
    states = load_states(DEFN, {})
    assert next_field(DEFN, states).key == "niche"
    states["niche"] = filled()
    assert next_field(DEFN, states).key == "offer"
    states["offer"] = filled()
    assert next_field(DEFN, states).key == "positioning"
    states["positioning"] = filled()
    assert next_field(DEFN, states) is None
    assert all_complete(states)


def test_positioning_waits_for_its_ask_after_even_when_niche_is_partial():
    states = load_states(DEFN, {})
    states["niche"] = FieldState(state=PARTIAL, value="краса", failed_filters=["concrete"])
    states["offer"] = filled()
    # partial niche is not complete → asked again before positioning
    assert next_field(DEFN, states).key == "niche"


def test_a_field_waiting_for_a_recheck_is_not_complete():
    fs = FieldState(state=FILLED, value="x", needs_recheck=True)
    assert not fs.complete


def test_merge_value():
    assert merge_value(None, " торти ") == "торти"
    assert merge_value("торти", "Торти") == "торти"
    assert merge_value("торти", "торти на замовлення") == "торти на замовлення"
    assert merge_value("торти", "весільні") == "торти\nвесільні"


def test_a_partial_answer_is_merged_unless_replaced():
    fs = FieldState(state=PARTIAL, value="торти", failed_filters=["concrete"])
    set_answer(fs, "весільні", replace=False, early=False, message_ids=["m2"], now=T0)
    assert fs.value == "торти\nвесільні"
    set_answer(fs, "кекси", replace=True, early=False, message_ids=["m3"], now=T0)
    assert fs.value == "кекси"
    assert fs.message_ids == ["m2", "m3"]


def test_an_early_answer_needs_a_recheck():
    fs = FieldState()
    set_answer(fs, "дешевше", replace=False, early=True, message_ids=["m1"], now=T0)
    assert fs.needs_recheck
    assert set_checked(fs, [], T0) is False  # filled, but still waiting for the recheck
    assert fs.state == FILLED


def test_set_checked_reports_the_moment_a_field_becomes_complete():
    fs = FieldState(value="x")
    assert set_checked(fs, ["concrete"], T0) is False
    assert (fs.state, fs.failed_filters) == (PARTIAL, ["concrete"])
    assert set_checked(fs, [], T0) is True
    assert (fs.state, fs.failed_filters) == (FILLED, [])
    assert set_checked(fs, [], T0) is False  # already complete


def test_dependents_are_transitive():
    defn = make_def(
        interview_data(
            fields=[
                {"key": "a", "question": "A?"},
                {"key": "b", "question": "B?", "ask_after": ["a"]},
                {"key": "c", "question": "C?", "ask_after": ["b"]},
            ],
            start={"questions": [{"key": "go", "ask": "Go?"}]},
        )
    )
    assert dependents(defn, "a") == ["b", "c"]
    assert dependents(defn, "c") == []


def test_a_correction_marks_answered_dependents():
    states = load_states(DEFN, {})
    states["niche"] = filled()
    states["offer"] = filled()
    states["positioning"] = filled("дешевше")
    assert mark_dependents(DEFN, states, "niche") == ["positioning"]
    assert states["positioning"].needs_recheck


def test_a_correction_skips_empty_dependents():
    states = load_states(DEFN, {})
    assert mark_dependents(DEFN, states, "niche") == []


def test_recheck_ready_after_ask_after_is_complete():
    states = load_states(DEFN, {})
    states["positioning"] = FieldState(state=FILLED, value="дешевше", needs_recheck=True)
    assert recheck_ready(DEFN, states) == []
    states["niche"] = filled()
    states["offer"] = filled()
    assert recheck_ready(DEFN, states) == ["positioning"]


def test_stuck_after_three_reasks_in_a_row():
    states = load_states(DEFN, {})
    assert register_ask(states, None, "niche") is False
    became = [register_ask(states, "niche", "niche") for _ in range(STUCK_AFTER)]
    assert became == [False] * (STUCK_AFTER - 1) + [True]
    assert states["niche"].stuck
    # once stuck, it does not fire again
    assert register_ask(states, "niche", "niche") is False


def test_asking_another_field_resets_the_reask_count():
    states = load_states(DEFN, {})
    register_ask(states, "niche", "niche")
    register_ask(states, "niche", "offer")
    assert states["offer"].reasks == 0


def test_answers_complete_only_by_default():
    states = load_states(DEFN, {})
    states["niche"] = FieldState(state=PARTIAL, value="краса", failed_filters=["concrete"])
    states["offer"] = filled("торти")
    assert answers(states) == {"offer": "торти"}
    assert answers(states, complete_only=False) == {"niche": "краса", "offer": "торти"}
