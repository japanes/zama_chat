from app.scenarios.fields import (
    EMPTY,
    FILLED,
    PARTIAL,
    STUCK_AFTER,
    FieldState,
    all_complete,
    answers,
    asked_out,
    dependents,
    load_states,
    mark_dependents,
    merge_value,
    next_field,
    recheck_ready,
    register_ask,
    set_answer,
    set_checked,
    settle_asked_out,
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


def test_a_clarification_is_an_ask_after_the_first_or_of_a_field_with_an_answer():
    states = load_states(DEFN, {})
    register_ask(states, None, "niche")
    assert (states["niche"].asks, states["niche"].clarified) == (1, 0)
    register_ask(states, "niche", "niche")
    assert (states["niche"].asks, states["niche"].clarified) == (2, 1)
    # told before it was asked: the first ask already asks for the rest
    states["offer"] = FieldState(state=PARTIAL, value="торти", failed_filters=["x"])
    register_ask(states, "niche", "offer")
    assert states["offer"].clarified == 1
    # the client's own question: the field is asked again as it was, not counted
    register_ask(states, "offer", "offer", count=False)
    assert (states["offer"].asks, states["offer"].clarified) == (1, 1)


def test_asked_out_after_the_clarifications_allowed():
    fs = FieldState(asks=1)
    assert asked_out(fs, None) is False
    assert asked_out(fs, 0) is True
    assert asked_out(fs, 1) is False
    fs.clarified = 1
    assert asked_out(fs, 1) is True
    assert asked_out(FieldState(), 0) is False  # never asked, nothing said


def test_a_general_answer_after_the_last_clarification_is_taken_as_said():
    fs = FieldState(value="всім", asks=2, clarified=1)
    assert set_checked(fs, ["concrete"], T0) is False  # no limit: the bot asks again
    assert (fs.state, fs.taken_as_said) == (PARTIAL, False)
    assert set_checked(fs, ["concrete"], T0, clarify_limit=1) is True
    assert (fs.state, fs.failed_filters, fs.taken_as_said) == (FILLED, [], True)
    # before the last clarification the filters stand
    early = FieldState(value="всім", asks=1)
    assert set_checked(early, ["concrete"], T0, clarify_limit=1) is False
    assert early.state == PARTIAL


def test_take_partial_an_answer_heard_in_part_is_not_asked_for_the_rest():
    fs = FieldState(value="коли зламалась піч", answered=True, answered_partly=True)
    assert set_checked(fs, [], T0) is False  # by default the bot asks for the rest
    assert fs.failed_filters == ["@partly"]
    assert set_checked(fs, [], T0, take_partial=True) is True
    assert (fs.state, fs.failed_filters, fs.taken_as_said) == (FILLED, [], False)
    # the filters still judge it
    empty = FieldState(value="всім", answered=True, answered_partly=True)
    assert set_checked(empty, ["concrete"], T0, take_partial=True) is False
    assert (empty.state, empty.failed_filters) == (PARTIAL, ["concrete"])


def test_a_value_code_rejected_is_never_taken_as_said():
    fs = FieldState(value="вчора", asks=2, clarified=1, issue="past")
    assert set_checked(fs, [], T0, clarify_limit=1) is False
    assert (fs.state, fs.taken_as_said) == (PARTIAL, False)


def test_settle_asked_out_takes_the_part_and_closes_the_silence():
    defn = make_def(interview_data(clarify_limit=1))
    states = load_states(defn, {})
    states["niche"] = FieldState(state=PARTIAL, value="краса", asks=2, clarified=1)
    states["offer"] = FieldState(asks=2, clarified=1)
    states["positioning"] = FieldState(asks=1)
    closed = settle_asked_out(defn, states, ["niche", "offer", "positioning"], T0)
    assert closed == ["offer"]
    assert (states["niche"].state, states["niche"].taken_as_said) == (FILLED, True)
    assert states["offer"].closed and states["offer"].value is None
    assert states["positioning"].state == EMPTY  # one clarification is still allowed


def test_settle_asked_out_waits_for_words_being_written():
    defn = make_def(interview_data(clarify_limit=1))
    states = load_states(defn, {})
    states["niche"] = FieldState(asks=2, clarified=1, pending=["t1"])
    assert settle_asked_out(defn, states, ["niche"], T0) == []
    assert not states["niche"].closed
    # no limit in the scenario: nothing is settled
    states = load_states(DEFN, {})
    states["niche"] = FieldState(asks=5, clarified=4)
    assert settle_asked_out(DEFN, states, ["niche"], T0) == []
