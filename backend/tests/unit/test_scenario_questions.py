from app.db.models import DecisionType, RunStatus
from app.scenarios.questions import (
    CHANGE_FIELD,
    CLIENT_QUESTION,
    NONE,
    PAUSE_FOCUS,
    STOP,
    build_questions,
    cancel_option,
    field_key,
    start_key,
    templates,
)
from tests.scenario_helpers import booking_data, loaded, make_def, open_run, states, view

INTERVIEW = loaded(make_def(), position=0)
BOOKING = loaded(make_def(booking_data()), position=1)


def test_no_runs_asks_start_signs_in_channel_order_and_client_question():
    built = build_questions(view([INTERVIEW, BOOKING]), "uk")
    assert built.keys == [
        start_key("interview", "wants_help"),
        field_key("interview", "niche"),
        field_key("interview", "offer"),
        start_key("booking", "wants_booking"),
        CLIENT_QUESTION,
    ]
    assert built.sections == []


def test_start_question_texts_in_the_question_language():
    built = build_questions(view([INTERVIEW]), "en")
    q = built.questions[0]
    assert q.type == DecisionType.BOOLEAN
    assert q.instructions == "Does the client want help?"
    assert q.criteria == {"true": "Yes.", "false": "No."}
    niche = built.questions[1]
    assert "«What is your niche?»" in niche.instructions


def test_unknown_language_uses_english_wrappers_and_the_main_language_text():
    built = build_questions(view([INTERVIEW]), "de")
    niche = next(q for q in built.questions if q.key == field_key("interview", "niche"))
    # the text falls back to uk → uk wrapper sentences
    assert niche.instructions.startswith("Чи є в останньому")
    assert templates("de") is templates("en")


def test_focus_asks_its_unfilled_fields_stop_and_change():
    run = open_run(INTERVIEW, fields=states(INTERVIEW.definition, niche="торти"), asking="offer")
    built = build_questions(view([INTERVIEW, BOOKING], [run]), "uk")
    assert built.keys == [
        start_key("booking", "wants_booking"),
        field_key("interview", "offer"),
        field_key("interview", "positioning"),
        STOP,
        CHANGE_FIELD,
        CLIENT_QUESTION,
    ]
    stop = built.questions[3]
    assert stop.type == DecisionType.CHOICE
    assert list(stop.criteria) == [NONE, PAUSE_FOCUS, cancel_option("interview")]
    change = built.questions[4]
    assert list(change.criteria) == [NONE, "niche"]
    assert "торти" in change.criteria["niche"]


def test_no_change_question_while_the_focus_has_no_answers():
    run = open_run(INTERVIEW)
    built = build_questions(view([INTERVIEW], [run]), "uk")
    assert CHANGE_FIELD not in built.keys
    assert STOP in built.keys


def test_paused_runs_take_data_and_can_be_cancelled():
    focus = open_run(BOOKING)
    paused = open_run(INTERVIEW, RunStatus.PAUSED)
    built = build_questions(view([INTERVIEW, BOOKING], [paused, focus]), "uk")
    # interview is not in focus → its start signs; its fields are asked once
    assert built.keys.count(field_key("interview", "niche")) == 1
    assert field_key("booking", "service") in built.keys
    stop = next(q for q in built.questions if q.key == STOP)
    assert cancel_option("interview") in stop.criteria
    assert cancel_option("booking") in stop.criteria


def test_a_waiting_run_is_not_started_again_and_takes_no_data():
    waiting = open_run(BOOKING, RunStatus.WAITING)
    built = build_questions(view([BOOKING], [waiting]), "uk")
    assert start_key("booking", "wants_booking") not in built.keys
    assert field_key("booking", "service") not in built.keys
    assert STOP in built.keys


def test_state_sections_show_the_focus_fields_and_the_last_question():
    fields = states(INTERVIEW.definition, niche=("partial", "краса", ["concrete"]))
    run = open_run(INTERVIEW, fields=fields, asking="niche")
    paused = open_run(BOOKING, RunStatus.PAUSED)
    built = build_questions(view([INTERVIEW, BOOKING], [run, paused]), "uk")
    title, text = built.sections[0]
    assert title == "Scenario in progress: Інтерв'ю"
    assert "- niche «Яка у вас ніша?»: incomplete answer: «краса»" in text
    assert "- offer «Що ви продаєте?»: no answer yet" in text
    assert text.endswith("The bot's last question was about: niche")
    assert built.sections[1] == ("Other open scenarios", "- Запис (paused)")


ONLY = loaded(make_def({**booking_data(), "only_scenario": True}), position=1)


def test_only_scenario_asks_no_starts_and_no_client_question():
    run = open_run(ONLY)
    built = build_questions(view([INTERVIEW, ONLY], [run]), "uk")
    assert start_key("interview", "wants_help") not in built.keys
    assert CLIENT_QUESTION not in built.keys
    assert field_key("booking", "service") in built.keys
    assert STOP in built.keys


def test_only_scenario_counts_while_it_waits():
    focus = open_run(INTERVIEW)
    waiting = open_run(ONLY, RunStatus.WAITING)
    built = build_questions(view([INTERVIEW, ONLY], [waiting, focus]), "uk")
    assert CLIENT_QUESTION not in built.keys
    assert field_key("interview", "offer") in built.keys


def test_a_closed_only_scenario_changes_nothing():
    built = build_questions(view([INTERVIEW, ONLY]), "uk")
    assert start_key("booking", "wants_booking") in built.keys
    assert CLIENT_QUESTION in built.keys
