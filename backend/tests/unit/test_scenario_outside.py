"""Pure parts of the changes from outside a turn: the bot's words of an event rule, the
operator's verdict on a receipt, where the run goes next (settle)."""

import uuid
from types import SimpleNamespace

from app.db.models import RunStatus
from app.scenarios import fields as fl
from app.scenarios.events import RECEIPT_ACCEPTED, RECEIPT_REJECTED
from app.scenarios.fields import FILLED, PARTIAL, REVIEW, FieldState
from app.scenarios.outside import (
    Change,
    apply_receipt_decision,
    confirmed,
    fill_say,
    receipt_text,
    settle,
)
from app.scenarios.schema import ActionEvent
from tests.scenario_helpers import make_def
from tests.stage6_helpers import order_data, salon_data
from tests.turn_ctx import T0

SALON = make_def(salon_data())
ORDER = make_def(order_data())


def _states(defn, **values):
    return fl.load_states(defn, None) | {
        k: v if isinstance(v, FieldState) else FieldState(state=FILLED, value=v)
        for k, v in values.items()
    }


class _Session:
    """settle() asks only «does another run of the conversation have the focus?»."""

    def __init__(self, other_focus: bool) -> None:
        self.other_focus = other_focus

    async def execute(self, _query):
        return SimpleNamespace(first=lambda: (uuid.uuid4(),) if self.other_focus else None)


def _run(status=RunStatus.WAITING):
    return SimpleNamespace(
        id=uuid.uuid4(), conversation_id=uuid.uuid4(), status=status, asking=None, finished_at=None
    )


# --- fill_say ---------------------------------------------------------------------------


def test_say_fills_answers_and_event_data():
    states = _states(SALON, date="2026-10-09", time="15:00")
    text = {"uk": "Запис {data.booking_id}: {date} о {time}, {unknown}", "en": "x"}
    assert (
        fill_say(SALON, text, "uk", states, {"booking_id": "B-7", "slots": [1, 2]})
        == "Запис B-7: 2026-10-09 о 15:00, {unknown}"
    )


def test_say_in_the_main_language_when_the_reply_one_is_missing():
    assert fill_say(SALON, {"uk": "Готово"}, "en", _states(SALON), {}) == "Готово"
    assert fill_say(SALON, None, "uk", _states(SALON), {}) is None


def test_an_empty_answer_is_an_empty_string():
    assert fill_say(SALON, "о {time}", "uk", _states(SALON), {}) == "о "


# --- the operator's verdict on a receipt ---------------------------------------------------


def _review_states():
    review = FieldState(state=REVIEW, value="500 UAH", norm={"receipt_id": "r1"})
    return _states(ORDER, item="крем", phone="+380671234567", payment=review)


def test_accepted_fills_the_receipt_field():
    states, data, change = _review_states(), {"receipt_id": "r1"}, Change()
    apply_receipt_decision(
        ORDER, states, data, field_key="payment", accept=True, language="uk", change=change, now=T0
    )
    assert states["payment"].state == FILLED
    assert [e.name for e in change.events] == [RECEIPT_ACCEPTED, ActionEvent.FIELD_FILLED]
    assert "receipt_id" not in data
    assert change.say == receipt_text("accepted", "uk")


def test_rejected_asks_for_another_receipt():
    states, data, change = _review_states(), {"receipt_id": "r1"}, Change()
    apply_receipt_decision(
        ORDER, states, data, field_key="payment", accept=False, language="en", change=change, now=T0
    )
    fs = states["payment"]
    assert fs.state == PARTIAL
    assert fs.failed_filters == ["@receipt_rejected"]
    assert [e.name for e in change.events] == [RECEIPT_REJECTED]
    assert change.say.startswith("Unfortunately")


def test_receipt_text_falls_back_to_english():
    assert receipt_text("accepted", "de") == "Thank you, the payment is confirmed."


# --- settle -----------------------------------------------------------------------------


async def test_settle_completes_a_collected_and_confirmed_order():
    run, change = _run(), Change()
    states = _states(ORDER, item="крем", phone="+380671234567", payment="500 UAH")
    data = {"confirmed_at": T0.isoformat(), "receipt_id": "r1"}
    await settle(_Session(False), run, ORDER, states, data, change, T0)
    assert run.status == RunStatus.COMPLETED
    assert run.finished_at == T0
    assert [e.name for e in change.events] == [ActionEvent.COMPLETED]


async def test_settle_waits_for_the_crm_after_the_receipt():
    run, change = _run(), Change()
    defn = make_def(
        salon_data(
            fields=[
                *salon_data()["fields"],
                {
                    "key": "deposit",
                    "type": "receipt",
                    "question": {"uk": "Квитанція?", "en": "Receipt?"},
                    "receipt": {"amount": 200, "currency": "UAH"},
                },
            ]
        )
    )
    values = {"service": "манікюр", "date": "2026-10-09", "time": "15:00", "phone": "+380"}
    states = _states(defn, **values, deposit="200 UAH")
    data = {"confirmed_at": T0.isoformat()}
    await settle(_Session(False), run, defn, states, data, change, T0)
    assert run.status == RunStatus.WAITING
    assert data["waiting_for"] == "event"
    assert [e.name for e in change.events] == [ActionEvent.WAITING]


async def test_settle_sends_the_run_back_to_the_client():
    run, change = _run(), Change()
    states = _states(ORDER, item="крем", phone="+380671234567")
    data = {"confirmed_at": T0.isoformat(), "waiting_for": "receipt_review", "receipt_id": "r1"}
    await settle(_Session(False), run, ORDER, states, data, change, T0)
    assert run.status == RunStatus.FOCUS
    assert run.asking == "payment"
    assert data == {"confirmed_at": T0.isoformat()}


async def test_settle_pauses_when_another_run_has_the_focus():
    run = _run()
    states = _states(ORDER, item="крем")
    await settle(_Session(True), run, ORDER, states, {}, Change(), T0)
    assert run.status == RunStatus.PAUSED
    assert run.asking == "phone"


def test_confirmed_needs_the_yes_only_when_the_scenario_asks():
    assert not confirmed(ORDER, {})
    assert confirmed(ORDER, {"confirmed_at": T0.isoformat()})
    assert confirmed(
        make_def(
            order_data(
                confirm=None,
                completion="all_fields",
                fields=[{"key": "item", "question": {"uk": "?", "en": "?"}}],
            )
        ),
        {},
    )
