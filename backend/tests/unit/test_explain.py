"""A turn in a person's words: what the bot understood, found and decided."""

from app.core.explain import SHOWN, explain_turn, turn_failure
from app.errors import AppError


def _trace(summary=None, search=None, **respond):
    steps = [{"step": "rewrite", "ms": 5}]
    if search is not None:
        steps.append({"step": "search", **search})
    if summary is not None:
        steps.append({"step": "scenarios", "summary": summary})
    steps.append({"step": "respond", **respond})
    return {"steps": steps}


def _whats(lines):
    return [line["what"] for line in lines]


def test_a_plain_message():
    out = explain_turn(input_text="Привіт!", rewritten="привіт", route={}, trace=_trace())
    assert out == {"understood": [{"what": "plain"}], "found": [], "decided": [{"what": "reply"}]}


def test_scenario_started_and_answers_taken():
    summary = {
        "stop": None,
        "started": ["beauty_booking"],
        "extracted": {"beauty_booking.service": "манікюр", "beauty_booking.date": "2026-10-09"},
        "contacts": ["beauty_booking.phone"],
        "issues": {},
        "confirm": None,
        "runs": {"beauty_booking": {"status": "focus", "events": ["started"], "asking": "time"}},
        "plan": {"scenario": "beauty_booking", "ask": "time", "accepted": ["service", "date"]},
    }
    out = explain_turn(
        input_text="хочу на манікюр у п'ятницю, 0501234567",
        rewritten="Клієнт хоче записатися на манікюр у п'ятницю, телефон 0501234567",
        route={"answers": {"client_question": False}},
        trace=_trace(summary),
    )
    understood = out["understood"]
    assert understood[0]["what"] == "meaning"
    assert understood[1] == {"what": "started", "scenario": "beauty_booking"}
    assert understood[2] == {
        "what": "answer",
        "scenario": "beauty_booking",
        "field": "service",
        "value": "манікюр",
    }
    # a phone found by code: the value is in the run, not in the trace
    assert understood[4] == {
        "what": "answer",
        "scenario": "beauty_booking",
        "field": "phone",
        "value": None,
    }
    assert out["found"] == []
    assert out["decided"] == [{"what": "ask", "scenario": "beauty_booking", "field": "time"}]


def test_the_same_text_is_not_repeated_as_its_meaning():
    out = explain_turn(input_text="Так, все вірно.", rewritten="так, все вірно", route={}, trace={})
    assert _whats(out["understood"]) == ["plain"]


def test_an_answer_that_did_not_fit_is_said_once_with_the_reason():
    summary = {
        "extracted": {"booking.date": "2020-01-01"},
        "issues": {"booking.date": "date_too_far", "booking.phone": "phone_invalid"},
        "issue_params": {"booking.date": {"max": 60}},
        "plan": {"scenario": "booking", "clarify": "date"},
    }
    out = explain_turn(input_text="1 січня", rewritten="1 січня", route={}, trace=_trace(summary))
    assert out["understood"] == [
        {
            "what": "not_accepted",
            "scenario": "booking",
            "field": "date",
            "issue": "date_too_far",
            "params": {"max": 60},
        },
        {
            "what": "not_accepted",
            "scenario": "booking",
            "field": "phone",
            "issue": "phone_invalid",
            "params": {},
        },
    ]
    assert out["decided"] == [{"what": "clarify", "scenario": "booking", "field": "date"}]


def test_a_question_inside_a_scenario_is_answered_first():
    search = {
        "wanted": "catalog",
        "items": [
            {"title": "Манікюр з гель-лаком", "price": "650.00", "currency": "UAH", "ref": "m1"},
        ],
        "knowledge": [],
        "not_found": False,
    }
    summary = {"plan": {"scenario": "booking", "answer_client_question_first": True, "ask": "x"}}
    out = explain_turn(
        input_text="а скільки коштує?",
        rewritten="скільки коштує манікюр з гель-лаком",
        route={"answers": {"client_question": True}},
        trace=_trace(summary, search),
    )
    assert _whats(out["understood"]) == ["meaning", "question"]
    assert out["found"] == [
        {
            "what": "catalog",
            "items": [{"title": "Манікюр з гель-лаком", "price": "650.00", "currency": "UAH"}],
            "more": 0,
        }
    ]
    assert _whats(out["decided"]) == ["answer_first", "ask"]


def test_long_lists_are_cut():
    search = {
        "wanted": "both",
        "items": [{"title": f"Item {n}"} for n in range(SHOWN + 3)],
        "knowledge": [{"title": "Доставка"}],
    }
    out = explain_turn(input_text="q", rewritten="q", route={}, trace=_trace(search=search))
    catalog, knowledge = out["found"]
    assert len(catalog["items"]) == SHOWN and catalog["more"] == 3
    assert knowledge == {"what": "knowledge", "items": [{"title": "Доставка"}], "more": 0}
    assert out["decided"] == [{"what": "answer_question"}]


def test_nothing_found():
    search = {"wanted": "knowledge", "items": [], "knowledge": [], "not_found": True}
    out = explain_turn(input_text="q", rewritten="q", route={}, trace=_trace(search=search))
    assert out["found"] == [{"what": "nothing", "where": "knowledge"}]
    assert out["decided"] == [{"what": "say_unknown"}]


def test_the_search_kept_only_in_the_route_is_read_too():
    route = {"search": {"wanted": "catalog", "items": ["m1"], "knowledge": [], "not_found": False}}
    out = explain_turn(input_text="q", rewritten="q", route=route, trace={})
    assert out["found"] == [{"what": "catalog", "items": [{"title": ""}], "more": 0}]


def test_confirm_cancel_finish_and_a_person():
    summary = {
        "stop": "cancelled:reschedule",
        "confirm": "yes",
        "runs": {
            "booking": {"status": "waiting", "events": ["confirmed", "waiting"], "asking": None},
            "pay": {"status": "waiting", "events": ["receipt_review:receipt"], "asking": None},
            "order": {"status": "completed", "events": ["field_filled:x", "completed"]},
            "old": {"status": "abandoned", "events": ["abandoned"], "asking": None},
        },
        "plan": {"scenario": "booking", "waiting": ["Запис у салон"]},
    }
    out = explain_turn(
        input_text="так",
        rewritten="так",
        route={"wants_human": True},
        trace=_trace(summary),
    )
    assert out["understood"] == [
        {"what": "cancelled", "scenario": "reschedule"},
        {"what": "confirm_yes"},
        {"what": "wants_human"},
    ]
    assert out["decided"] == [
        {"what": "wait_outside", "scenario": "booking"},
        {"what": "wait_review", "scenario": "pay"},
        {"what": "completed", "scenario": "order"},
        {"what": "abandoned", "scenario": "old"},
        {"what": "handover"},
    ]


def test_the_summary_is_shown_again_after_a_no():
    summary = {"confirm": "no", "plan": {"scenario": "booking", "confirm": "rejected"}}
    out = explain_turn(input_text="ні", rewritten="ні", route={}, trace=_trace(summary))
    assert _whats(out["understood"]) == ["confirm_no"]
    assert out["decided"] == [{"what": "ask_what_wrong", "scenario": "booking"}]


def test_operator_mode_offers_variants():
    out = explain_turn(input_text="q", rewritten="q", route={}, trace=_trace(variants=3))
    assert out["decided"] == [{"what": "variants", "n": 3}]


def test_values_are_made_readable():
    summary = {
        "extracted": {
            "s.agree": True,
            "s.people": [{"name": "Olha", "age": 7}, {"name": "Petro", "age": 9}],
            "s.note": "x" * 500,
        },
    }
    out = explain_turn(input_text="q", rewritten="q", route={}, trace=_trace(summary))
    values = [line["value"] for line in out["understood"]]
    assert values[0] == "yes"
    assert values[1] == "name: Olha, age: 7, name: Petro, age: 9"
    assert len(values[2]) == 200 and values[2].endswith("…")


def test_a_broken_trace_does_not_break_the_page():
    out = explain_turn(
        input_text=None,
        rewritten=None,
        route={"answers": "?", "search": []},
        trace={"steps": ["?", {"step": "scenarios", "summary": "?"}, {"step": "search"}]},
    )
    assert out == {"understood": [{"what": "plain"}], "found": [], "decided": [{"what": "reply"}]}


def test_a_request_passed_on_is_told_even_without_the_run_events():
    summary = {"plan": {"scenario": "booking", "waiting": ["Запис у салон"]}}
    out = explain_turn(input_text="так", rewritten="так", route={}, trace=_trace(summary))
    assert out["decided"] == [{"what": "wait_outside", "scenario": "booking"}]


def _stored(exc: Exception) -> str:
    """The error text a failed turn keeps (core/turn.py `_fail`)."""
    error = exc.to_dict() if isinstance(exc, AppError) else {"code": type(exc).__name__}
    return f"{error}: {exc}"[:2000]


def test_a_failed_turn_says_why_by_a_code():
    assert turn_failure(None) is None
    assert turn_failure("") is None

    no_model = AppError("llm.role_not_assigned", {"role": "rewrite"}, status=503)
    assert turn_failure(_stored(no_model)) == {
        "code": "llm.role_not_assigned",
        "params": {"role": "rewrite"},
    }
    assert turn_failure(_stored(AppError("turn.not_finished"))) == {
        "code": "turn.not_finished",
        "params": {},
    }
    # the text of the model's error may look like the end of the code part
    tricky = AppError("llm.unavailable", {"role": "reply", "error": "bad {x}: y"}, status=503)
    assert turn_failure(_stored(tricky)) == {
        "code": "llm.unavailable",
        "params": {"role": "reply", "error": "bad {x}: y"},
    }


def test_an_error_of_the_program_is_named_not_shown():
    assert turn_failure(_stored(ValueError("x: {'a': 1}"))) == {
        "code": "turn.failed",
        "params": {"error": "ValueError"},
    }
    # a text in another form (an old turn): shown as it is, shortened
    assert turn_failure("something went wrong") == {
        "code": "turn.failed",
        "params": {"error": "something went wrong"},
    }
    assert len(turn_failure("x" * 5000)["params"]["error"]) == 200
