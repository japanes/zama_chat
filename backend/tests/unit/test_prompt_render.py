"""%PLACEHOLDER% substitution and JSON prompts."""

import json

import pytest

from app.errors import AppError
from app.prompts.render import (
    parse_json_prompt,
    placeholders,
    render_json,
    render_text,
    to_text,
)


def test_placeholders_found():
    assert placeholders("Hi %BOT_NAME% of %COMPANY_TITLE%, %lower% %BOT_NAME%") == {
        "BOT_NAME",
        "COMPANY_TITLE",
    }


def test_render_text_substitutes_known_and_keeps_unknown():
    text = render_text("%A% and %B2%, %MISSING%", {"A": "one", "B2": "two"})
    assert text == "one and two, %MISSING%"


def test_render_text_strict_fails_on_unknown():
    with pytest.raises(AppError) as err:
        render_text("%A% %NOPE%", {"A": "x"}, strict=True)
    assert err.value.code == "prompt.placeholder_unknown"
    assert err.value.params == {"name": "NOPE"}


def test_render_text_value_is_not_rescanned():
    # a value that itself looks like a placeholder stays as is
    assert render_text("%A%", {"A": "%B%", "B": "no"}) == "%B%"


def test_render_json_substitutes_inside_nested_values_and_sets_fields():
    body = json.dumps(
        {
            "return": "json",
            "rules": {"common": ["talk as %BOT_NAME%"], "language": ["%LANGUAGE%"]},
            "dialogue": [],
            "answer_language": "",
        }
    )
    data = render_json(
        body,
        {"dialogue": ["client: hi"], "answer_language": "uk"},
        {"BOT_NAME": "Zama", "LANGUAGE": "uk"},
        key="rewrite",
    )
    assert data["rules"]["common"] == ["talk as Zama"]
    assert data["rules"]["language"] == ["uk"]
    assert data["dialogue"] == ["client: hi"]
    assert data["answer_language"] == "uk"
    assert data["return"] == "json"


def test_fields_are_not_substituted():
    # client text goes in through fields; a %X% inside it must not be replaced
    data = render_json('{"q": ""}', {"q": "%BOT_NAME%"}, {"BOT_NAME": "Zama"})
    assert data["q"] == "%BOT_NAME%"


def test_invalid_json_prompt():
    with pytest.raises(AppError) as err:
        parse_json_prompt("{not json", key="rewrite")
    assert err.value.code == "prompt.invalid_json"
    assert err.value.params["key"] == "rewrite"
    assert err.value.params["error"]


def test_json_prompt_must_be_an_object():
    with pytest.raises(AppError) as err:
        parse_json_prompt("[1, 2]", key="rewrite")
    assert err.value.code == "prompt.invalid_json"
    assert err.value.params["error"] == "not an object"


def test_to_text_keeps_unicode():
    text = to_text({"q": "привіт"})
    assert "привіт" in text
    assert json.loads(text) == {"q": "привіт"}
