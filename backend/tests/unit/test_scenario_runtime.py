"""The scenario step: pure steps one by one, then run_scenarios() on fakes."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.db.models import RunStatus
from app.decisions.fake import FakeDecider
from app.scenarios import runtime as rt
from app.scenarios.events import FOCUSED, PAUSED
from app.scenarios.extract import Extracted
from app.scenarios.fields import FILLED, PARTIAL, STUCK_AFTER, FieldState
from app.scenarios.questions import CHANGE_FIELD, PAUSE_FOCUS, STOP, field_key, start_key
from app.scenarios.schema import ActionEvent
from app.scenarios.work import WorkRun, focus_of
from tests.scenario_helpers import (
    booking_data,
    decisions,
    loaded,
    make_def,
    open_run,
    states,
    view,
)
from tests.turn_ctx import T0, make_ctx

INTERVIEW = loaded(make_def(), position=0)
BOOKING = loaded(make_def(booking_data()), position=1)
LATER = T0 + timedelta(minutes=5)


def work(scenario, status=RunStatus.FOCUS, **kw):
    return WorkRun.from_open(open_run(scenario, status, **kw))


# --- expire / stop ----------------------------------------------------------------------


def test_expired_runs_are_abandoned():
    expired = open_run(BOOKING, RunStatus.PAUSED)
    [run] = rt.expire_runs(view([BOOKING], expired=[expired]))
    assert run.status == RunStatus.ABANDONED
    assert run.event_names() == [ActionEvent.ABANDONED]
    assert run.closed_now


def test_stop_pauses_the_focus():
    runs = [work(INTERVIEW, asking="niche")]
    assert rt.apply_stop(runs, PAUSE_FOCUS) == "paused:interview"
    assert runs[0].status == RunStatus.PAUSED
    assert runs[0].paused_now
    assert runs[0].asking is None
    assert runs[0].event_names() == [PAUSED]


def test_stop_cancels_any_open_run():
    runs = [work(INTERVIEW), work(BOOKING, RunStatus.PAUSED)]
    assert rt.apply_stop(runs, "cancel:booking") == "cancelled:booking"
    assert runs[1].status == RunStatus.CANCELLED
    assert runs[1].event_names() == [ActionEvent.CANCELLED]
    assert runs[0].status == RunStatus.FOCUS


@pytest.mark.parametrize("value", ["none", None, "cancel:nope", "rubbish"])
def test_stop_without_effect(value):
    runs = [work(INTERVIEW)]
    assert rt.apply_stop(runs, value) is None
    assert runs[0].status == RunStatus.FOCUS


def test_pause_without_focus_does_nothing():
    runs = [work(BOOKING, RunStatus.PAUSED)]
    assert rt.apply_stop(runs, PAUSE_FOCUS) is None


# --- start ------------------------------------------------------------------------------

YES_INTERVIEW = {start_key("interview", "wants_help"): True}
YES_BOOKING = {start_key("booking", "wants_booking"): True}


def test_start_without_focus_creates_the_focus_run():
    runs: list[WorkRun] = []
    result = rt.apply_start(view([INTERVIEW, BOOKING]), runs, decisions(YES_INTERVIEW), T0)
    assert result.new == ["interview"]
    [run] = runs
    assert (run.key, run.status, run.is_new) == ("interview", RunStatus.FOCUS, True)
    assert run.event_names() == [ActionEvent.STARTED]


def test_two_field_signs_start_the_interview():
    marks = {field_key("interview", "niche"): True, field_key("interview", "offer"): True}
    runs: list[WorkRun] = []
    rt.apply_start(view([INTERVIEW]), runs, decisions(marks), T0)
    assert [r.key for r in runs] == ["interview"]


def test_one_field_sign_is_not_enough():
    runs: list[WorkRun] = []
    rt.apply_start(view([INTERVIEW]), runs, decisions({field_key("interview", "niche"): True}), T0)
    assert runs == []


def test_a_busy_focus_puts_the_new_run_on_pause():
    runs = [work(INTERVIEW)]
    rt.apply_start(view([INTERVIEW, BOOKING]), runs, decisions(YES_BOOKING), T0)
    assert [(r.key, r.status) for r in runs] == [
        ("interview", RunStatus.FOCUS),
        ("booking", RunStatus.PAUSED),
    ]


def test_stop_and_start_in_one_message_pass_the_focus():
    runs = [work(INTERVIEW)]
    rt.apply_stop(runs, "cancel:interview")
    rt.apply_start(view([INTERVIEW, BOOKING]), runs, decisions(YES_BOOKING), T0)
    assert focus_of(runs).key == "booking"


def test_a_cancelled_scenario_is_not_restarted_by_the_same_message():
    runs = [work(INTERVIEW)]
    rt.apply_stop(runs, "cancel:interview")
    result = rt.apply_start(view([INTERVIEW]), runs, decisions(YES_INTERVIEW), T0)
    assert result.new == []
    assert len(runs) == 1


def test_a_fired_paused_run_takes_the_free_focus_without_a_copy():
    runs = [work(BOOKING, RunStatus.PAUSED)]
    result = rt.apply_start(view([INTERVIEW, BOOKING]), runs, decisions(YES_BOOKING), T0)
    assert result.new == []
    assert runs[0].status == RunStatus.FOCUS
    assert runs[0].event_names() == [FOCUSED]


def test_a_waiting_run_is_not_a_candidate():
    runs = [work(BOOKING, RunStatus.WAITING)]
    cands = rt.start_candidates(view([BOOKING]), runs, decisions(YES_BOOKING))
    assert cands == []


def test_a_scenario_started_by_the_caller_is_never_a_candidate():
    caller = loaded(make_def({**booking_data(), "start_by_caller": True}), position=1)
    assert rt.start_candidates(view([caller]), [], decisions(YES_BOOKING)) == []
    runs = [work(caller, RunStatus.PAUSED)]
    assert rt.start_candidates(view([caller]), runs, decisions(YES_BOOKING)) == []


# --- extract / apply values -----------------------------------------------------------


def test_a_new_run_extracts_every_field():
    runs = [WorkRun.new(INTERVIEW, RunStatus.FOCUS, T0)]
    targets = rt.extract_targets(runs, decisions({}), None)
    assert [t.field.key for t in targets] == ["niche", "offer", "positioning"]


def test_marked_fields_and_partial_current_value():
    fields = states(INTERVIEW.definition, niche=("partial", "краса", ["concrete"]))
    runs = [work(INTERVIEW, fields=fields)]
    marks = {field_key("interview", "niche"): True, field_key("interview", "offer"): False}
    [target] = rt.extract_targets(runs, decisions(marks), runs[0])
    assert (target.field.key, target.current, target.correction) == ("niche", "краса", False)


def test_a_correction_targets_a_filled_field_of_the_focus():
    fields = states(INTERVIEW.definition, niche="торти")
    runs = [work(INTERVIEW, fields=fields)]
    [target] = rt.extract_targets(runs, decisions({CHANGE_FIELD: "niche"}), runs[0])
    assert (target.field.key, target.current, target.correction) == ("niche", "торти", True)


def test_a_complete_field_marked_by_mistake_is_not_extracted():
    fields = states(INTERVIEW.definition, niche="торти")
    runs = [work(INTERVIEW, fields=fields)]
    assert rt.extract_targets(runs, decisions({field_key("interview", "niche"): True}), None) == []


def test_closed_and_waiting_runs_take_no_data():
    runs = [work(BOOKING, RunStatus.WAITING)]
    assert rt.extract_targets(runs, decisions({field_key("booking", "day"): True}), None) == []


def test_an_answer_before_its_turn_waits_for_a_recheck():
    runs = [work(INTERVIEW)]
    targets = rt.extract_targets(
        runs, decisions({field_key("interview", "positioning"): True}), runs[0]
    )
    got = Extracted(values={("interview", "positioning"): "дешевше"})
    rt.apply_values(runs, targets, got, message_ids=["m1"], now=T0)
    fs = runs[0].states["positioning"]
    assert (fs.value, fs.needs_recheck, fs.message_ids) == ("дешевше", True, ["m1"])
    assert runs[0].answered == ["positioning"]


def test_a_correction_replaces_and_marks_dependents():
    fields = states(INTERVIEW.definition, niche="торти", offer="торти", positioning="дешевше")
    runs = [work(INTERVIEW, fields=fields)]
    targets = rt.extract_targets(runs, decisions({CHANGE_FIELD: "niche"}), runs[0])
    got = Extracted(values={("interview", "niche"): "кекси"})
    marked = rt.apply_values(runs, targets, got, message_ids=[], now=T0)
    assert runs[0].states["niche"].value == "кекси"
    assert marked == ["interview.positioning"]
    assert runs[0].states["positioning"].needs_recheck


# --- checks -----------------------------------------------------------------------------


def test_first_round_checks_answered_fields_then_ready_rechecks():
    fields = states(
        INTERVIEW.definition,
        niche="торти",
        positioning=FieldState(state=FILLED, value="дешевше", needs_recheck=True),
    )
    run = work(INTERVIEW, fields=fields)
    run.states["offer"].value = "торти"
    run.answered = ["offer"]
    due = rt.checks_due([run], first=True)
    assert [k for _, k in due] == ["offer"]  # positioning not ready: offer not complete

    rt.apply_checks(due, {("interview", "offer"): []}, T0)
    assert run.states["offer"].complete
    assert [k for _, k in rt.checks_due([run], first=False)] == ["positioning"]


def test_an_early_answer_checked_with_its_ask_after_in_one_round_is_complete():
    run = work(INTERVIEW, fields=states(INTERVIEW.definition, niche="торти"))
    for key, value in (("offer", "торти"), ("positioning", "дешевше")):
        fs = run.states[key]
        fs.value = value
        fs.needs_recheck = key == "positioning"
    run.answered = ["offer", "positioning"]
    due = rt.checks_due([run], first=True)
    rt.apply_checks(due, {("interview", "offer"): [], ("interview", "positioning"): []}, T0)
    assert run.states["positioning"].complete


def test_a_failed_filter_leaves_the_field_partial():
    run = work(INTERVIEW)
    run.states["niche"].value = "краса"
    run.answered = ["niche"]
    due = rt.checks_due([run], first=True)
    rt.apply_checks(due, {("interview", "niche"): ["concrete"]}, T0)
    fs = run.states["niche"]
    assert (fs.state, fs.failed_filters) == (PARTIAL, ["concrete"])


# --- finish / focus / next question ---------------------------------------------------


def test_all_fields_complete_the_run_with_events():
    fields = states(INTERVIEW.definition, niche="торти", offer="торти")
    run = work(INTERVIEW, fields=fields)
    run.states["positioning"] = FieldState(state=FILLED, value="дешевше")
    rt.finish_runs([run])
    assert run.status == RunStatus.COMPLETED
    assert run.filled == ["positioning"]
    assert run.event_names() == [
        ActionEvent.FIELD_FILLED,
        ActionEvent.ALL_FIELDS,
        ActionEvent.COMPLETED,
    ]
    assert run.closed_now


def test_a_completed_focus_passes_to_the_most_recent_paused_run():
    done = work(INTERVIEW)
    done.status = RunStatus.COMPLETED
    old = work(BOOKING, RunStatus.PAUSED, last_activity_at=T0)
    other = loaded(make_def(booking_data(key="delivery")), position=2)
    recent = work(other, RunStatus.PAUSED, last_activity_at=LATER)
    passed = rt.pass_focus([done, old, recent])
    assert passed is recent
    assert recent.status == RunStatus.FOCUS
    assert recent.event_names() == [FOCUSED]


def test_a_focus_put_aside_by_the_client_does_not_pass_to_an_idle_paused_run():
    aside = work(INTERVIEW)
    rt.apply_stop([aside], PAUSE_FOCUS)
    idle = work(BOOKING, RunStatus.PAUSED)
    assert rt.pass_focus([aside, idle]) is None


def test_a_paused_run_that_got_data_takes_a_free_focus():
    run = work(BOOKING, RunStatus.PAUSED)
    run.answered = ["service"]
    assert rt.pass_focus([run]) is run


def _queued(key, place, **kw):
    return work(loaded(make_def(booking_data(key=key, queue=place))), RunStatus.PAUSED, **kw)


def test_a_free_focus_goes_to_the_smallest_place_in_the_queue():
    done = work(INTERVIEW)
    done.status = RunStatus.COMPLETED
    third = _queued("market", 3, last_activity_at=LATER)
    third.answered = ["service"]  # answered last: without a queue it would take the focus
    second = _queued("offer", 2, last_activity_at=T0)
    assert rt.pass_focus([done, third, second]) is second
    assert (second.status, second.event_names()) == (RunStatus.FOCUS, [FOCUSED])
    assert third.status == RunStatus.PAUSED


def test_the_queue_waits_while_the_client_has_put_the_focus_aside():
    aside = work(INTERVIEW)
    rt.apply_stop([aside], PAUSE_FOCUS)
    assert rt.pass_focus([aside, _queued("offer", 2)]) is None


def test_next_question_and_stuck():
    run = work(INTERVIEW, asking="niche")
    for _ in range(STUCK_AFTER):
        run.events.clear()
        rt.next_questions([run])
    assert run.asking == "niche"
    assert run.states["niche"].stuck
    assert run.event_names() == [ActionEvent.FIELD_STUCK]


def test_nothing_is_asked_when_the_client_wants_a_human():
    run = work(INTERVIEW, asking="niche")
    rt.next_questions([run], ask=False)
    assert run.asking == "niche"
    assert run.states["niche"].reasks == 0


def test_paused_runs_ask_nothing():
    run = work(BOOKING, RunStatus.PAUSED, asking="service")
    rt.next_questions([run])
    assert run.asking is None


# --- run_scenarios on fakes -------------------------------------------------------------


class ExtractLLM:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def complete(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        return SimpleNamespace(data=self.data, call_id="call-x", model="openai/a")


@pytest.fixture
def no_decision_log(monkeypatch):
    async def save(sm, rows):
        return None

    monkeypatch.setattr("app.decisions.log.save_decisions", save)


async def _run(v, route, filters, extracted, text, **kw):
    decider = FakeDecider(filters)
    services = SimpleNamespace(llm=ExtractLLM(extracted), extra={"decider": decider}, sm=None)
    ctx = make_ctx(input_text=text)
    step: dict = {}
    turn = await rt.run_scenarios(
        services,
        ctx,
        v,
        decisions(route),
        prompts={},
        rewritten=text,
        question_language="uk",
        client_language="uk",
        reply_language="uk",
        wants_human=kw.get("wants_human", False),
        step=step,
    )
    return turn, step, services


async def test_interview_starts_and_takes_the_early_niche(no_decision_log):
    route = {
        start_key("interview", "wants_help"): True,
        field_key("interview", "niche"): True,
        field_key("interview", "offer"): False,
    }
    turn, step, _services = await _run(
        view([INTERVIEW]),
        route,
        {"filter:interview.niche.concrete": True},
        {"interview__niche": "торти на замовлення", "interview__offer": None},
        "Хочу розібратися з позиціонуванням, я печу торти на замовлення",
    )
    [run] = turn.runs
    assert run.status == RunStatus.FOCUS
    assert run.states["niche"].complete
    assert run.asking == "offer"
    assert turn.plan.ask.field == "offer"
    assert turn.plan.started == ["Інтерв'ю"]
    assert turn.plan.accepted[0]["answer"] == "торти на замовлення"
    assert step["started"] == ["interview"]
    assert step["check_rounds"] == 1
    assert step["filters"] == {"interview.niche": []}
    assert turn.summary["extract_targets"] == [
        "interview.niche",
        "interview.offer",
        "interview.positioning",
    ]


async def test_a_retold_value_is_rejected_and_the_field_asked(no_decision_log):
    run = open_run(INTERVIEW, asking="niche")
    turn, step, _ = await _run(
        view([INTERVIEW], [run]),
        {field_key("interview", "niche"): True},
        {},
        {"interview__niche": "кондитерський бізнес преміум-класу"},
        "ну я печу",
    )
    assert step["rejected"][0]["reason"] == "not_in_message"
    assert turn.runs[0].states["niche"].value is None
    assert turn.plan.ask.field == "niche"


async def test_early_positioning_is_rechecked_and_the_interview_completes(no_decision_log):
    fields = states(INTERVIEW.definition, niche="торти на замовлення")
    run = open_run(INTERVIEW, fields=fields, asking="offer")
    turn, step, _ = await _run(
        view([INTERVIEW], [run]),
        {field_key("interview", "offer"): True, field_key("interview", "positioning"): True},
        {"filter:interview.positioning.fits": True},
        {"interview__offer": "весільні торти", "interview__positioning": "роблю за добу"},
        "Продаю весільні торти, роблю за добу",
    )
    [done] = turn.runs
    assert done.status == RunStatus.COMPLETED
    assert ActionEvent.COMPLETED in done.event_names()
    assert turn.plan.completed == ["Інтерв'ю"]
    assert turn.plan.ask is None
    assert step["check_rounds"] == 1


async def test_a_failed_filter_gives_a_clarify_step(no_decision_log):
    run = open_run(INTERVIEW, asking="niche")
    turn, _, _ = await _run(
        view([INTERVIEW], [run]),
        {field_key("interview", "niche"): True},
        {"filter:interview.niche.concrete": False},
        {"interview__niche": "краса"},
        "краса",
    )
    clarify = turn.plan.clarify
    assert (clarify.field, clarify.client_answer) == ("niche", "краса")
    assert clarify.missing == ["Ніша конкретна?"]


async def test_side_question_is_answered_and_the_bot_returns(no_decision_log):
    run = open_run(INTERVIEW, asking="niche")
    turn, _, services = await _run(
        view([INTERVIEW], [run]),
        {field_key("interview", "niche"): False, "client_question": True},
        {},
        {},
        "а скільки коштує консультація?",
    )
    assert turn.plan.answer_client_question_first is True
    assert turn.plan.ask.field == "niche"
    assert services.llm.calls == []  # nothing to extract


async def test_wants_human_asks_nothing(no_decision_log):
    run = open_run(INTERVIEW, asking="niche")
    turn, _, _ = await _run(
        view([INTERVIEW], [run]), {STOP: "none"}, {}, {}, "покличте людину", wants_human=True
    )
    assert turn.plan.scenario == "interview"
    assert not turn.plan.asks
