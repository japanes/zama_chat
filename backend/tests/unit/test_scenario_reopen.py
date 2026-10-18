"""A completed run the client amends goes back to work («also change the time»): the same
request is changed, no new one is started."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.db.models import RunStatus
from app.decisions.fake import FakeDecider
from app.scenarios import runtime as rt
from app.scenarios.events import REOPENED
from app.scenarios.fields import FILLED, FieldState
from app.scenarios.questions import CLIENT_QUESTION, build_questions, reopen_key, start_key
from app.scenarios.schema import ActionEvent
from app.scenarios.store import definition_json
from app.scenarios.work import WorkRun
from tests.scenario_helpers import (
    booking_data,
    decisions,
    interview_data,
    loaded,
    make_def,
    open_run,
    states,
    view,
)
from tests.turn_ctx import T0, make_ctx

BOOKING = loaded(make_def(booking_data()), position=0)
CANCEL = loaded(
    make_def(booking_data(key="cancel", title={"uk": "Скасування", "en": "Cancel"})),
    position=1,
)
INTERVIEW = loaded(make_def(interview_data()), position=2)
REOPEN = {reopen_key("booking"): True}


def done(scenario=BOOKING, **values):
    """A run completed in the conversation earlier."""
    fields = states(scenario.definition, **(values or {"service": "стрижка", "day": "понеділок"}))
    return open_run(scenario, RunStatus.COMPLETED, fields=fields, data={"confirmed_at": "x"})


# --- schema -------------------------------------------------------------------------------


def test_reopen_is_on_by_default_and_off_for_waiting_scenarios():
    assert make_def(booking_data()).reopen_hours == 24
    assert make_def(booking_data(reopen={"hours": 0})).reopen_hours == 0
    waits = booking_data(completion="external_event", events={"ok": {"result": "completed"}})
    assert make_def({**waits, "reopen": {"hours": 5}}).reopen_hours == 0


def test_the_default_reopen_leaves_the_definition_as_it_was():
    plain = definition_json(make_def(booking_data()))
    assert "reopen" not in plain
    assert definition_json(make_def(booking_data(reopen={"hours": 24}))) == plain
    assert definition_json(make_def(booking_data(reopen={"hours": 2})))["reopen"] == {"hours": 2}


# --- questions ----------------------------------------------------------------------------


def test_a_completed_run_is_asked_about_before_the_client_question():
    built = build_questions(view([BOOKING], completed=[done()]), "uk")
    assert built.keys[-2:] == [reopen_key("booking"), CLIENT_QUESTION]
    question = built.questions[-2]
    assert "«Запис»" in question.instructions
    assert "«Який день?» — «понеділок»" in question.instructions


def test_no_reopen_question_while_an_only_scenario_run_is_open():
    only = loaded(make_def(interview_data(only_scenario=True)))
    built = build_questions(view([only, BOOKING], [open_run(only)], completed=[done()]), "uk")
    assert reopen_key("booking") not in built.keys


# --- runtime steps ------------------------------------------------------------------------


def test_the_amended_run_takes_the_focus_and_no_copy_starts():
    runs: list[WorkRun] = []
    v = view([BOOKING], completed=[done()])
    yes = {**REOPEN, start_key("booking", "wants_booking"): True}
    assert rt.apply_reopen(v, runs, decisions(yes)) == "booking"
    [run] = runs
    assert (run.status, run.reopened, run.event_names()) == (RunStatus.FOCUS, True, [REOPENED])
    assert "confirmed_at" not in run.data
    assert rt.apply_start(v, runs, decisions(yes), T0).new == []
    assert len(runs) == 1


def test_no_reopen_without_the_sign():
    runs: list[WorkRun] = []
    assert rt.apply_reopen(view([BOOKING], completed=[done()]), runs, decisions({})) is None
    assert runs == []


def test_a_busy_focus_gets_the_amended_run_paused():
    runs = [WorkRun.from_open(open_run(INTERVIEW))]
    rt.apply_reopen(view([BOOKING, INTERVIEW], completed=[done()]), runs, decisions(REOPEN))
    assert runs[1].status == RunStatus.PAUSED


def test_no_reopen_next_to_an_incompatible_open_run():
    booking = loaded(make_def(booking_data(incompatible_with=["cancel"])))
    runs = [WorkRun.from_open(open_run(CANCEL))]
    v = view([booking, CANCEL], completed=[done(booking)])
    assert rt.apply_reopen(v, runs, decisions(REOPEN)) is None
    assert len(runs) == 1


def test_every_answer_of_the_amended_run_is_a_correction():
    runs: list[WorkRun] = []
    rt.apply_reopen(view([BOOKING], completed=[done()]), runs, decisions(REOPEN))
    targets = rt.extract_targets(runs, decisions({}), None)
    assert [(t.field.key, t.current, t.correction) for t in targets] == [
        ("service", "стрижка", True),
        ("day", "понеділок", True),
    ]


def test_a_changed_answer_completes_the_run_again():
    runs: list[WorkRun] = []
    rt.apply_reopen(view([BOOKING], completed=[done()]), runs, decisions(REOPEN))
    [run] = runs
    run.states["day"] = FieldState(state=FILLED, value="вівторок")
    rt.finish_runs(runs)
    assert run.status == RunStatus.COMPLETED
    assert run.event_names() == [
        REOPENED,
        ActionEvent.FIELD_FILLED,
        ActionEvent.ALL_FIELDS,
        ActionEvent.COMPLETED,
    ]
    assert run.closed_now  # the completion actions fire again


def test_nothing_new_leaves_the_run_completed_as_it_was():
    runs: list[WorkRun] = []
    rt.apply_reopen(view([BOOKING], completed=[done()]), runs, decisions(REOPEN))
    [run] = runs
    rt.finish_runs(runs)
    assert (run.status, run.reopened, run.events) == (RunStatus.COMPLETED, False, [])
    assert run.data == {"confirmed_at": "x"}
    assert not run.closed_now


# --- the whole step -----------------------------------------------------------------------


class ExtractLLM:
    def __init__(self, data):
        self.data = data

    async def complete(self, role, messages, **kw):
        return SimpleNamespace(data=self.data, call_id="call-x", model="openai/a")


@pytest.fixture
def no_decision_log(monkeypatch):
    async def save(sm, rows):
        return None

    monkeypatch.setattr("app.decisions.log.save_decisions", save)


async def _run(v, route, extracted, text):
    services = SimpleNamespace(
        llm=ExtractLLM(extracted), extra={"decider": FakeDecider({})}, sm=None
    )
    return await rt.run_scenarios(
        services,
        make_ctx(input_text=text, now=T0 + timedelta(hours=1)),
        v,
        decisions(route),
        prompts={},
        rewritten=text,
        question_language="uk",
        client_language="uk",
        reply_language="uk",
        wants_human=False,
        step={},
    )


async def test_also_change_the_day_updates_the_completed_request(no_decision_log):
    route = {**REOPEN, start_key("booking", "wants_booking"): True}
    turn = await _run(
        view([BOOKING], completed=[done()]),
        route,
        {"booking__service": None, "booking__day": "вівторок"},
        "Хочу ще змінити день на вівторок",
    )
    [run] = turn.runs
    assert run.status == RunStatus.COMPLETED
    assert run.states["day"].value == "вівторок"
    assert turn.summary["reopened"] == "booking"
    assert turn.summary["started"] == []
    assert turn.plan.reopened == ["Запис"]
    assert turn.plan.completed == ["Запис"]
    assert turn.plan.to_prompt([])["scenario_reopened"] == "Запис"
