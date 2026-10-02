"""Scenario fields of type `catalog`: the schema rules and the extract contract (an enum
of the candidates' external_ids, an unknown id rejected). Plus the admin CSV import
parser and the `unknown_answer` tenant setting."""

import uuid
from decimal import Decimal

import pytest

from app.api.admin.search import _csv_rows
from app.core.tenant_settings import TenantSettings
from app.errors import AppError
from app.scenarios.extract import Target, extract_schema, field_spec, parse_values
from app.scenarios.schema import check_definition, parse_definition
from tests.scenario_helpers import interview_data, make_def

SERVICE = {
    "key": "service",
    "type": "catalog",
    "question": {"uk": "Яка послуга?", "en": "Which service?"},
    "catalog": {"categories": ["manicure"], "max_options": 3},
}


def _data(field: dict) -> dict:
    return interview_data(fields=[field], start={"questions": [{"key": "go", "ask": "Go?"}]})


# --- schema -----------------------------------------------------------------------------


def test_catalog_field_is_valid():
    defn = parse_definition(_data(SERVICE))
    assert check_definition(defn, ["uk", "en"]) == []
    f = defn.field("service")
    assert f.catalog is not None
    assert f.catalog.categories == ["manicure"]
    assert f.catalog.max_options == 3


def test_catalog_settings_on_another_type_are_an_error():
    field = {**SERVICE, "type": "text"}
    with pytest.raises(AppError) as exc:
        check_definition(parse_definition(_data(field)), ["uk", "en"])
    assert exc.value.code == "scenario.catalog_not_allowed"
    assert exc.value.params == {"field": "service", "type": "text"}


@pytest.mark.parametrize("value", [1, 11])
def test_max_options_bounds(value):
    field = {**SERVICE, "catalog": {"max_options": value}}
    with pytest.raises(AppError) as exc:
        parse_definition(_data(field))
    assert exc.value.code == "scenario.invalid"


def test_catalog_without_settings_means_the_whole_catalog():
    defn = parse_definition(_data({k: v for k, v in SERVICE.items() if k != "catalog"}))
    assert defn.field("service").catalog is None


# --- extract ----------------------------------------------------------------------------

DEFN = make_def(_data(SERVICE))
RUN = uuid.uuid4()
CHOICES = (("sku-1", "Манікюр класичний"), ("sku-2", "Манікюр з покриттям"))
TARGET = Target(RUN, DEFN, DEFN.field("service"), choices=CHOICES)


def test_schema_enum_is_the_candidates_refs():
    schema = extract_schema([TARGET])
    assert schema["properties"]["interview__service"]["enum"] == ["sku-1", "sku-2", None]


def test_field_spec_lists_the_candidates():
    spec = field_spec(TARGET, "uk")
    assert spec["options"] == [
        {"key": "sku-1", "label": "Манікюр класичний"},
        {"key": "sku-2", "label": "Манікюр з покриттям"},
    ]


def test_parse_values_keeps_a_known_ref_without_the_grounding_check():
    # «давайте другий» has no words of the title: the ref is enough
    values, rejected = parse_values({"interview__service": "sku-2"}, [TARGET], "давайте другий")
    assert values == {("interview", "service"): "sku-2"}
    assert rejected == []


def test_parse_values_rejects_an_unknown_ref():
    values, rejected = parse_values({"interview__service": "sku-9"}, [TARGET], "давайте другий")
    assert values == {}
    assert rejected == [{"field": "interview.service", "value": "sku-9", "reason": "unknown_item"}]


# --- CSV import -------------------------------------------------------------------------


def test_csv_with_semicolons_attributes_and_flags():
    text = (
        "external_id;title;price;category;is_active;attr:color;attr:duration\n"
        "sku-1;Манікюр;450,50;manicure;так;red|nude;90\n"
        "sku-2;Педикюр;;pedicure;ні;;\n"
    )
    rows = _csv_rows(text)
    assert rows[0] == {
        "external_id": "sku-1",
        "title": "Манікюр",
        "price": Decimal("450.50"),
        "category": "manicure",
        "is_active": True,
        "attributes": {"color": ["red", "nude"], "duration": "90"},
    }
    # empty cells are left out (the model default applies), «ні» switches off
    assert rows[1] == {
        "external_id": "sku-2",
        "title": "Педикюр",
        "category": "pedicure",
        "is_active": False,
        "attributes": {},
    }


def test_csv_with_commas_and_quotes():
    text = 'external_id,title,description\nsku-1,"Манікюр, класичний","Опис"\n'
    rows = _csv_rows(text)
    assert rows[0]["title"] == "Манікюр, класичний"
    assert rows[0]["description"] == "Опис"


def test_csv_unknown_columns_are_an_error():
    with pytest.raises(AppError) as exc:
        _csv_rows("external_id,title,colour\nsku-1,A,red\n")
    assert exc.value.code == "catalog.import_columns"
    assert exc.value.params == {"columns": ["colour"]}


def test_csv_bad_price_is_left_for_the_model_check():
    rows = _csv_rows("external_id,title,price\nsku-1,A,abc\n")
    assert rows[0]["price"] == "abc"


# --- tenant setting ---------------------------------------------------------------------


def test_unknown_answer_setting():
    assert TenantSettings().unknown_answer == "say"
    assert TenantSettings(unknown_answer="handover").validate_rules().unknown_answer == "handover"
    with pytest.raises(AppError) as exc:
        TenantSettings(unknown_answer="ignore").validate_rules()
    assert exc.value.code == "tenant.unknown_answer_invalid"
    assert exc.value.params == {"value": "ignore"}
