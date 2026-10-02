"""Scenario format of stage 6: typed fields, confirm, completion, events, incompatible
pairs, approvals — each wrong combination has its own error code."""

import pytest

from app.errors import AppError
from app.scenarios.loader import prepare, read_file
from app.scenarios.schema import Completion, FieldType, check_definition, parse_definition
from app.settings import PROJECT_ROOT
from tests.scenario_helpers import booking_data
from tests.stage6_helpers import cancel_data, order_data, salon_data

SCENARIOS_DIR = PROJECT_ROOT / "scenarios"


def _check(data):
    return check_definition(parse_definition(data), ["uk", "en"])


def _code(data) -> str:
    with pytest.raises(AppError) as exc:
        _check(data)
    return exc.value.code


def _field(key, type_, **extra):
    return {"key": key, "type": type_, "question": {"uk": "?", "en": "?"}, **extra}


def test_stage6_helpers_are_valid():
    for data in (salon_data(), cancel_data(), order_data()):
        assert _check(data) == []


@pytest.mark.parametrize(
    ("name", "completion"),
    [
        ("beauty_booking", Completion.EXTERNAL_EVENT),
        ("reschedule_booking", Completion.EXTERNAL_EVENT),
        ("cancel_booking", Completion.EXPLICIT_YES),
        ("group_booking", Completion.EXTERNAL_EVENT),
        ("prepaid_order", Completion.RECEIPT),
    ],
)
def test_the_stage6_files_are_valid_without_warnings(name, completion):
    defn, warnings = prepare(read_file(SCENARIOS_DIR / f"{name}.yaml"), ["uk", "en"])
    assert defn.key == name
    assert defn.completion == completion
    assert defn.needs_confirm
    assert warnings == []


def test_the_booking_files_exclude_each_other():
    keys = ("beauty_booking", "reschedule_booking", "cancel_booking", "group_booking")
    defs = {k: prepare(read_file(SCENARIOS_DIR / f"{k}.yaml"), ["uk", "en"])[0] for k in keys}
    assert set(defs["beauty_booking"].incompatible_with) == set(keys) - {"beauty_booking"}
    order, _ = prepare(read_file(SCENARIOS_DIR / "prepaid_order.yaml"), ["uk", "en"])
    payment = order.field("receipt")
    assert payment.type == FieldType.RECEIPT
    assert (payment.receipt.amount_field, payment.receipt.quantity_field) == ("product", "quantity")


def test_a_setting_only_for_its_own_type():
    data = booking_data(fields=[_field("day", "text", date={"allow_past": True})])
    assert _code(data) == "scenario.field_setting_not_allowed"
    data = booking_data(fields=[_field("n", "date", number={"min": 1})])
    assert _code(data) == "scenario.field_setting_not_allowed"


def test_number_and_list_ranges():
    data = booking_data(fields=[_field("n", "number", number={"min": 5, "max": 1})])
    assert _code(data) == "scenario.number_range_invalid"
    data = booking_data(fields=[_field("l", "list", items={"min_items": 5, "max_items": 2})])
    assert _code(data) == "scenario.list_range_invalid"
    item = {"key": "name", "label": "Ім'я"}
    data = booking_data(fields=[_field("l", "list", items={"item_fields": [item, item]})])
    assert _code(data) == "scenario.list_item_duplicate"


def test_files_have_no_filters():
    flt = {"key": "ok", "ask": {"uk": "?", "en": "?"}}
    data = booking_data(fields=[_field("photo", "attachment", filters=[flt])])
    assert _code(data) == "scenario.filters_not_allowed"


def test_receipt_settings():
    def order_with(receipt, extra_fields=()):
        fields = [*extra_fields, _field("pay", "receipt", receipt=receipt)]
        return order_data(fields=fields)

    assert _code(order_with({"amount": 100})) == "scenario.receipt_currency_required"
    assert _code(order_with({"amount_field": "nope"})) == "scenario.receipt_amount_field_invalid"
    text_field = _field("item", "text")
    assert (
        _code(order_with({"amount_field": "item"}, [text_field]))
        == "scenario.receipt_amount_field_invalid"
    )
    catalog = _field("item", "catalog", catalog={})
    assert (
        _code(order_with({"amount_field": "item", "quantity_field": "item"}, [catalog]))
        == "scenario.receipt_quantity_field_invalid"
    )
    quantity = _field("qty", "number")
    ok = order_with({"amount_field": "item", "quantity_field": "qty"}, [catalog, quantity])
    assert _check(ok) == []


def test_a_receipt_without_an_amount_is_a_warning():
    warnings = _check(order_data(fields=[_field("pay", "receipt", receipt={"currency": "UAH"})]))
    assert warnings == [{"code": "scenario.receipt_amount_missing", "params": {"field": "pay"}}]


def test_completion_receipt_needs_a_receipt_field():
    assert _code(booking_data(completion="receipt")) == "scenario.receipt_field_required"


def test_events_go_with_external_event_only():
    assert _code(salon_data(events={})) == "scenario.events_required"
    data = booking_data(events={"booked": {"result": "completed"}})
    assert _code(data) == "scenario.events_not_allowed"
    assert _code(booking_data(waiting={"hours": 2})) == "scenario.events_not_allowed"


def test_reopen_rules():
    def with_event(rule):
        return salon_data(events={"x": rule})

    assert _code(with_event({"result": "reopen"})) == "scenario.event_reopen_fields_required"
    assert (
        _code(with_event({"result": "completed", "reopen_fields": ["date"]}))
        == "scenario.event_reopen_fields_not_allowed"
    )
    assert (
        _code(with_event({"result": "reopen", "reopen_fields": ["nope"]}))
        == "scenario.event_field_unknown"
    )


def test_a_scenario_is_not_incompatible_with_itself():
    assert _code(salon_data(incompatible_with=["salon"])) == "scenario.incompatible_self"


def test_confirm_and_approval_parse():
    defn = parse_definition(cancel_data())
    assert defn.needs_confirm  # explicit_yes asks even without `confirm`
    assert defn.actions[0].requires_approval is True
    assert parse_definition(booking_data()).needs_confirm is False
    assert parse_definition(booking_data(confirm={})).needs_confirm is True


def test_unknown_keys_in_new_blocks_are_rejected():
    assert _code(salon_data(waiting={"hours": 2, "x": 1})) == "scenario.invalid"
    assert _code(booking_data(confirm={"text": "?"})) == "scenario.invalid"
