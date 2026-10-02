"""Stage 6 steps of the scenario runtime, one by one (pure, no DB): confirm, waiting for
an event, incompatible scenarios, contacts found by code, files and receipts."""

from datetime import timedelta
from decimal import Decimal

from app.db.models import ReceiptStatus, RunStatus
from app.receipts.checks import Expected, reading_from
from app.receipts.service import ReceiptCheck
from app.scenarios import fields as fl
from app.scenarios import runtime as rt
from app.scenarios.events import RECEIPT_REVIEW
from app.scenarios.fields import FILLED, PARTIAL, REVIEW, FieldState
from app.scenarios.plan import CONFIRM_QUESTION, build_plan
from app.scenarios.questions import CONFIRM, NO, YES, confirm_key
from app.scenarios.schema import ActionEvent
from app.scenarios.work import WorkRun
from tests.scenario_helpers import decisions, open_run, states, view
from tests.stage6_helpers import CANCEL, ORDER, SALON, UPLOAD
from tests.turn_ctx import T0

LATER = T0 + timedelta(minutes=5)
SALON_DONE = {"service": "манікюр", "date": "завтра", "time": "15:00", "phone": "+380671234567"}


def work(scenario, status=RunStatus.FOCUS, **kw):
    return WorkRun.from_open(open_run(scenario, status, **kw))


def salon_collected(**kw):
    return work(SALON, fields=states(SALON.definition, **SALON_DONE), **kw)


def order_collected(**kw):
    values = {"item": "крем", "phone": "+380671234567"}
    return work(ORDER, fields=states(ORDER.definition, **values), **kw)


# --- confirm ----------------------------------------------------------------------------


def test_a_receipt_is_asked_only_after_the_confirm():
    run = order_collected()
    assert fl.next_field(run.definition, run.states, confirmed=False) is None
    assert fl.next_field(run.definition, run.states, confirmed=True).key == "payment"


def test_everything_collected_asks_to_confirm():
    runs = [salon_collected(asking="phone")]
    rt.next_questions(runs)
    assert runs[0].asking == CONFIRM


def test_after_the_yes_the_receipt_is_asked():
    run = order_collected(asking=CONFIRM, data={"confirmed_at": T0.isoformat()})
    rt.next_questions([run])
    assert run.asking == "payment"


def test_yes_confirms_and_fires_confirmed():
    run = salon_collected(asking=CONFIRM)
    assert rt.apply_confirm(run, decisions({confirm_key("salon"): YES}), LATER) == YES
    assert run.data["confirmed_at"] == LATER.isoformat()
    assert run.event_names() == [ActionEvent.CONFIRMED]
    assert run.confirmed


def test_yes_with_a_correction_in_the_same_message_is_not_a_yes():
    run = salon_collected(asking=CONFIRM)
    run.states["time"].value = "16:00"  # the client corrected the time with «так, але о 16»
    assert rt.apply_confirm(run, decisions({confirm_key("salon"): YES}), LATER) is None
    assert "confirmed_at" not in run.data


def test_no_is_remembered_for_the_reply():
    run = salon_collected(asking=CONFIRM)
    assert rt.apply_confirm(run, decisions({confirm_key("salon"): NO}), LATER) == NO
    assert run.confirm_rejected
    assert not run.confirmed


def test_confirm_counts_only_when_it_was_asked():
    run = salon_collected(asking="phone")
    assert rt.apply_confirm(run, decisions({confirm_key("salon"): YES}), LATER) is None
    assert rt.apply_confirm(None, decisions({}), LATER) is None


def test_collected_without_the_yes_does_not_complete():
    run = salon_collected()
    rt.finish_runs([run], LATER)
    assert run.status == RunStatus.FOCUS
    assert ActionEvent.WAITING not in run.event_names()


def test_confirmed_external_event_scenario_waits():
    run = salon_collected(data={"confirmed_at": T0.isoformat()})
    rt.finish_runs([run], LATER)
    assert run.status == RunStatus.WAITING
    assert run.asking is None
    assert run.data["waiting_for"] == "event"
    assert run.data["waiting_since"] == LATER.isoformat()
    assert ActionEvent.WAITING in run.event_names()
    assert ActionEvent.COMPLETED not in run.event_names()


def test_a_correction_after_the_yes_needs_a_new_yes():
    run = salon_collected(data={"confirmed_at": T0.isoformat()})
    run.states["time"].value = "17:00"
    rt.finish_runs([run], LATER)
    assert "confirmed_at" not in run.data
    assert run.status == RunStatus.FOCUS


def test_the_confirm_step_shows_the_answers_without_the_receipt():
    run = order_collected(asking=CONFIRM)
    plan = build_plan([run], language="en", client_question=False)
    assert plan.confirm.question == CONFIRM_QUESTION["en"]
    assert [s["answer"] for s in plan.confirm.summary] == ["крем", "+380671234567"]
    assert plan.ask is None
    assert plan.asks
    salon = salon_collected(asking=CONFIRM)
    salon.confirm_rejected = True
    plan = build_plan([salon], language="uk", client_question=False)
    assert plan.confirm.question == "Усе вірно?"
    assert plan.confirm.rejected


def test_the_plan_tells_about_a_new_wait_and_the_payment():
    run = salon_collected(data={"confirmed_at": T0.isoformat()})
    rt.finish_runs([run], LATER)
    plan = build_plan([run], language="uk", client_question=False)
    assert plan.waiting == [{"title": "Запис", "for": "event", "new": True}]
    order = order_collected(asking="payment", data={"confirmed_at": T0.isoformat()})
    payment = {"order.payment": {"amount": "500", "currency": "UAH"}}
    plan = build_plan([order], language="uk", client_question=False, payment=payment)
    assert plan.ask.payment == {"amount": "500", "currency": "UAH"}
    assert plan.ask.attachment


def test_a_value_code_rejected_is_clarified_with_the_issue_text():
    fs = FieldState(state=PARTIAL, value="вчора", failed_filters=["@date_in_past"])
    run = work(SALON, fields=states(SALON.definition, service="манікюр", date=fs), asking="date")
    plan = build_plan([run], language="en", client_question=False)
    assert plan.clarify.field == "date"
    assert plan.clarify.missing == ["this date has already passed: ask for a date from today on"]


# --- incompatible scenarios -------------------------------------------------------------


def test_incompatible_works_both_ways():
    assert rt.incompatible(SALON.definition, CANCEL.definition)
    assert rt.incompatible(CANCEL.definition, SALON.definition)
    assert not rt.incompatible(SALON.definition, SALON.definition)
    assert not rt.incompatible(SALON.definition, ORDER.definition)


def test_a_scenario_incompatible_with_an_open_run_does_not_start():
    runs = [work(SALON, asking="date")]
    blocked: list[str] = []
    votes = decisions({"start:cancel.wants_cancel": True})
    found = rt.start_candidates(view([SALON, CANCEL], runs), runs, votes, blocked)
    assert [c.key for c in found] == []
    assert blocked == ["cancel"]


def test_two_incompatible_scenarios_fired_together_keep_the_focus_one():
    runs: list[WorkRun] = []
    votes = decisions({"start:salon.wants_booking": True, "start:cancel.wants_cancel": True})
    result = rt.apply_start(view([SALON, CANCEL]), runs, votes, T0)
    assert result.new == ["salon"]
    assert result.blocked == ["cancel"]
    assert [r.key for r in runs] == ["salon"]


def test_a_cancelled_run_frees_the_way():
    runs = [work(SALON, asking="date")]
    rt.apply_stop(runs, "cancel:salon")
    votes = decisions({"start:cancel.wants_cancel": True})
    result = rt.apply_start(view([SALON, CANCEL]), runs, votes, T0)
    assert result.new == ["cancel"]


# --- contacts by code -------------------------------------------------------------------


def test_one_phone_fills_the_only_open_phone_field():
    run = work(SALON, asking="service")
    done = rt.prefill_contacts([run], "мій номер 067 123 45 67", message_ids=["m1"], now=T0)
    assert done == ["salon.phone"]
    fs = run.states["phone"]
    assert (fs.value, fs.norm) == ("+380671234567", "+380671234567")
    assert run.answered == ["phone"]


def test_two_phones_are_left_to_the_model():
    run = work(SALON)
    text = "067 123 45 67 або 050 765 43 21"
    assert rt.prefill_contacts([run], text, message_ids=["m1"], now=T0) == []


def test_a_waiting_run_takes_no_contacts():
    run = salon_collected(status=RunStatus.WAITING)
    run.states["phone"] = FieldState()
    assert rt.prefill_contacts([run], "0671234567", message_ids=["m1"], now=T0) == []


def test_a_filled_phone_is_not_overwritten_by_code():
    run = salon_collected()
    assert rt.prefill_contacts([run], "0509999999", message_ids=["m1"], now=T0) == []
    assert run.states["phone"].value == "+380671234567"


# --- files ------------------------------------------------------------------------------

PHOTO = {"type": "image", "url": "https://cdn.example/1.jpg", "message_id": "m1"}
VIDEO = {"type": "video", "url": "https://cdn.example/2.mp4"}


def test_an_attachment_field_takes_files_of_its_kinds():
    run = work(UPLOAD, fields=states(UPLOAD.definition, name="Оля"), asking="photo")
    done = rt.apply_attachments([run], [PHOTO, VIDEO, PHOTO], [], message_ids=["m1"], now=T0)
    assert done == ["upload.photo"]
    fs = run.states["photo"]
    assert fs.state == FILLED
    assert [i["type"] for i in fs.norm] == ["image", "image"]  # max_items 2, video left out


def test_a_file_of_the_wrong_kind_is_asked_again():
    run = work(UPLOAD, fields=states(UPLOAD.definition, name="Оля"), asking="photo")
    rt.apply_attachments([run], [VIDEO], [], message_ids=["m1"], now=T0)
    fs = run.states["photo"]
    assert fs.state == PARTIAL
    assert fs.failed_filters == ["@attachment_kind"]
    assert fs.issue_params == {"kinds": ["image"]}


def test_a_receipt_waits_until_everything_else_is_collected():
    run = work(ORDER, fields=states(ORDER.definition, item="крем"))
    assert rt.attachment_field(run) is None
    assert rt.attachment_field(order_collected()).key == "payment"


def _check(run, *, issues=(), review=None, reading=None):
    default = {"is_receipt": True, "amount": 500, "currency": "UAH", "date": "2026-10-01"}
    data = reading if reading is not None else default
    check = ReceiptCheck(run.id, "payment", PHOTO, Expected(amount=Decimal(500), currency="UAH"))
    check.reading = reading_from(data)
    check.issues = list(issues)
    check.review = review
    return check


def test_an_accepted_receipt_fills_the_field_and_counts_as_yes():
    run = order_collected(asking="payment")
    check = _check(run)
    assert check.status == ReceiptStatus.ACCEPTED
    rt.apply_attachments([run], [PHOTO], [check], message_ids=["m1"], now=T0)
    fs = run.states["payment"]
    assert fs.state == FILLED
    assert fs.norm["receipt_id"] == str(check.id)
    assert ActionEvent.CONFIRMED in run.event_names()
    rt.finish_runs([run], T0)
    assert run.status == RunStatus.COMPLETED


def test_a_receipt_for_a_person_puts_the_run_on_wait():
    run = order_collected(asking="payment", data={"confirmed_at": T0.isoformat()})
    check = _check(run, review="always")
    assert check.status == ReceiptStatus.REVIEW
    rt.apply_attachments([run], [PHOTO], [check], message_ids=["m1"], now=T0)
    assert run.states["payment"].state == REVIEW
    assert run.status == RunStatus.WAITING
    assert run.data["waiting_for"] == "receipt_review"
    assert run.data["receipt_id"] == str(check.id)
    assert RECEIPT_REVIEW in run.event_names()
    assert fl.next_field(run.definition, run.states) is None  # nothing asked meanwhile


def test_not_a_receipt_is_asked_again_without_a_person():
    run = order_collected(asking="payment", data={"confirmed_at": T0.isoformat()})
    check = _check(run, issues=[{"code": "not_receipt", "params": {}}], reading={})
    assert check.status == ReceiptStatus.REJECTED
    rt.apply_attachments([run], [PHOTO], [check], message_ids=["m1"], now=T0)
    fs = run.states["payment"]
    assert fs.state == PARTIAL
    assert fs.failed_filters == ["@receipt_not_receipt"]
    assert run.status == RunStatus.FOCUS


def test_files_without_a_focus_are_ignored():
    run = work(UPLOAD, RunStatus.PAUSED)
    assert rt.apply_attachments([run], [PHOTO], [], message_ids=["m1"], now=T0) == []
