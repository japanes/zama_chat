"""background_answers: the decider marks which questions a message answers (in full, in
part, adding to an answer), the turn asks on, and the job scenario_answers writes the
answers after the reply (scenarios/answers.py)."""

from datetime import timedelta

from app.db.models import DecisionType, RunStatus
from app.scenarios import fields as fl
from app.scenarios.answers import merge_states
from app.scenarios.fields import FILLED, PARTIAL, PARTLY, FieldState
from app.scenarios.questions import (
    FULL,
    IN_PART,
    MORE,
    NONE,
    build_questions,
    field_key,
    field_question,
    says_answer,
)
from app.scenarios.runtime import mark_background, waits_for_answers
from app.scenarios.work import WorkRun
from tests.scenario_helpers import (
    decisions,
    interview_data,
    loaded,
    make_def,
    open_run,
    states,
    view,
)
from tests.turn_ctx import T0

BACKGROUND = make_def(interview_data(background_answers=True))
PLAIN = make_def()


def work(defn=BACKGROUND, status=RunStatus.FOCUS, **values) -> WorkRun:
    return WorkRun.from_open(open_run(loaded(defn), status, fields=states(defn, **values)))


def marks(**values) -> dict:
    return {field_key("interview", k): v for k, v in values.items()}


# --- the decider's questions ----------------------------------------------------------------


def test_an_answer_in_full_in_part_or_added_says_the_field_is_answered():
    assert all(says_answer(v) for v in (True, FULL, IN_PART, MORE))
    assert not any(says_answer(v) for v in (False, NONE, None, "other"))


def test_a_background_field_is_asked_in_full_in_part_or_not():
    niche = BACKGROUND.field("niche")
    q = field_question(BACKGROUND, niche, "en")
    assert q.type == DecisionType.CHOICE
    assert set(q.criteria) == {NONE, IN_PART, FULL}
    assert q.neutral == NONE
    # without the setting: yes or no, as before
    assert field_question(PLAIN, PLAIN.field("niche"), "en").type == DecisionType.BOOLEAN


def test_a_field_with_an_answer_is_asked_whether_the_message_adds_to_it():
    niche = BACKGROUND.field("niche")
    fs = FieldState(state=FILLED, value="coffee shops")
    q = field_question(BACKGROUND, niche, "en", fs)
    assert set(q.criteria) == {NONE, MORE}
    assert "coffee shops" in q.instructions


def test_a_complete_background_field_is_still_asked_about_more():
    run = open_run(
        loaded(BACKGROUND), fields=states(BACKGROUND, niche="coffee shops"), asking="offer"
    )
    by_key = {q.key: q for q in build_questions(view([loaded(BACKGROUND)], [run]), "en").questions}
    assert set(by_key[field_key("interview", "niche")].criteria) == {NONE, MORE}
    plain = open_run(loaded(PLAIN), fields=states(PLAIN, niche="coffee shops"), asking="offer")
    keys = build_questions(view([loaded(PLAIN)], [plain]), "en").keys
    assert field_key("interview", "niche") not in keys


# --- the field's state ----------------------------------------------------------------------


def test_an_answer_heard_in_part_stays_partial_when_its_filters_pass():
    fs = FieldState(value="two of us", answered_partly=True)
    assert fl.set_checked(fs, [], T0) is False
    assert (fs.state, fs.failed_filters) == (PARTIAL, [PARTLY])


def test_more_words_are_added_to_a_complete_answer():
    fs = FieldState(state=FILLED, value="coffee shops")
    fl.set_answer(fs, "in Kyiv", replace=False, early=False, message_ids=[], now=T0, merge=True)
    assert "coffee shops" in fs.value and "in Kyiv" in fs.value


def test_a_correction_drops_what_waits_in_the_background():
    fs = FieldState(state=PARTIAL, value="two of us", answered_partly=True, pending=["t1"])
    fl.set_answer(fs, "five people", replace=True, early=False, message_ids=[], now=T0)
    assert (fs.value, fs.pending, fs.answered_partly) == ("five people", [], False)


def test_a_field_being_written_is_not_asked():
    st = fl.load_states(BACKGROUND, states(BACKGROUND, niche=FieldState(pending=["t1"])))
    assert fl.next_field(BACKGROUND, st).key == "offer"


# --- the turn marks ---------------------------------------------------------------------------


def test_the_answered_fields_go_to_the_background():
    run = work()
    done = mark_background(
        [run],
        decisions(marks(niche=FULL, offer=IN_PART, positioning=NONE)),
        [],
        skip=set(),
        turn_id="t1",
    )
    assert done == ["interview.niche", "interview.offer"]
    niche, offer, pos = (run.states[k] for k in ("niche", "offer", "positioning"))
    assert (niche.answered, niche.answered_partly, niche.pending) == (True, False, ["t1"])
    assert (offer.answered, offer.answered_partly, offer.pending) == (False, True, ["t1"])
    assert pos.pending == [] and run.marked == ["niche", "offer"]
    # nothing is written in the turn
    assert niche.value is None and offer.value is None


def test_more_words_clear_an_answer_heard_in_part():
    run = work(offer=FieldState(state=PARTIAL, value="cakes", answered_partly=True))
    mark_background([run], decisions(marks(offer=MORE)), [], skip=set(), turn_id="t2")
    offer = run.states["offer"]
    assert (offer.answered, offer.answered_partly, offer.pending) == (True, False, ["t2"])


def test_no_background_without_the_setting_or_for_a_button():
    plain = work(PLAIN)
    assert (
        mark_background([plain], decisions(marks(niche=True)), [], skip=set(), turn_id="t1") == []
    )
    run = work()
    done = mark_background(
        [run], decisions(marks(niche=FULL)), [], skip={"interview.niche"}, turn_id="t1"
    )
    assert done == [] and run.states["niche"].pending == []


def test_a_run_started_in_the_turn_reads_its_answers_at_once():
    run = WorkRun.new(loaded(BACKGROUND), RunStatus.FOCUS, T0)
    assert mark_background([run], decisions(marks(niche=FULL)), [], skip=set(), turn_id="t1") == []


# --- when the turn waits ------------------------------------------------------------------


def test_the_focus_asks_on_while_answers_are_written():
    run = work(niche=FieldState(pending=["t1"]))
    assert waits_for_answers(run) is False  # «offer» is asked meanwhile


def test_the_focus_waits_when_nothing_else_is_left_to_ask():
    run = work(niche=FieldState(pending=["t1"]), offer=FieldState(pending=["t1"]))
    # «positioning» comes after both answers
    assert waits_for_answers(run) is True


def test_the_focus_waits_for_an_answer_an_earlier_question_needs():
    data = interview_data(background_answers=True)
    data["fields"] = [
        {**data["fields"][2], "ask_after": ["offer"]},  # positioning, after offer
        data["fields"][0],  # niche
        data["fields"][1],  # offer
    ]
    defn = make_def(data)
    run = work(defn, offer=FieldState(pending=["t1"]))
    assert waits_for_answers(run) is True


def test_a_paused_run_never_waits():
    run = work(
        status=RunStatus.PAUSED, niche=FieldState(pending=["t1"]), offer=FieldState(pending=["t1"])
    )
    assert waits_for_answers(run) is False


# --- the job's work put in the run as it is now -----------------------------------------


def _st(**values) -> dict[str, FieldState]:
    return fl.load_states(BACKGROUND, states(BACKGROUND, **values))


def test_the_job_writes_the_answer_it_read():
    start = _st(niche=FieldState(pending=["t1"], answered=True))
    mine = _st(niche=FieldState(state=FILLED, value="coffee shops", pending=["t1"], answered=True))
    out = merge_states(BACKGROUND, states(BACKGROUND, **start), start, mine, {"niche": ["t1"]})
    assert (out["niche"].state, out["niche"].value, out["niche"].pending) == (
        FILLED,
        "coffee shops",
        [],
    )


def test_words_said_meanwhile_wait_for_the_next_job():
    start = _st(offer=FieldState(pending=["t1"], answered_partly=True))
    mine = _st(offer=FieldState(state=PARTIAL, value="cakes", failed_filters=[PARTLY]))
    now = _st(offer=FieldState(pending=["t1", "t2"], answered=True))
    out = merge_states(BACKGROUND, states(BACKGROUND, **now), start, mine, {"offer": ["t1"]})
    offer = out["offer"]
    assert (offer.value, offer.pending) == ("cakes", ["t2"])
    # the newer mark stands: the answer is not heard in part any more
    assert (offer.answered, offer.answered_partly) == (True, False)


def test_a_field_a_turn_changed_meanwhile_keeps_the_turn():
    start = _st(niche=FieldState(pending=["t1"], answered=True))
    mine = _st(niche=FieldState(state=FILLED, value="coffee shops"))
    now = _st(niche="bakeries")  # the client corrected it: the pending turn is dropped
    out = merge_states(BACKGROUND, states(BACKGROUND, **now), start, mine, {"niche": ["t1"]})
    assert out["niche"].value == "bakeries"


def test_a_field_the_job_did_not_touch_stays_as_it_is_now():
    asked = (T0 + timedelta(minutes=1)).isoformat()
    start = _st()
    now = _st(offer=FieldState(asked=asked))
    out = merge_states(BACKGROUND, states(BACKGROUND, **now), start, start, {})
    assert out["offer"].asked == asked
