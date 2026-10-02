import pytest

from app.core.language import (
    detect_client_language,
    is_neutral,
    language_name,
    normalize_code,
    reply_language_for,
)
from app.core.tenant_settings import TenantSettings
from app.errors import AppError

RULED = TenantSettings(languages=["uk", "en"], reply_language={"uk": "uk", "ru": "uk", "*": "en"})
PLAIN = TenantSettings(languages=["uk", "en"])


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("uk", "uk"),
        ("UK", "uk"),
        (" Ukrainian ", "uk"),
        ("russian", "ru"),
        ("uk-UA", "uk"),
        ("en_GB", "en"),
        ("", None),
        (None, None),
        ("123", None),
        ("not a language", None),
    ],
)
def test_normalize_code(value, code):
    assert normalize_code(value) == code


def test_language_name():
    assert language_name("uk") == "Ukrainian"
    assert language_name("en-GB") == "English"
    assert language_name("xx") == "xx"


@pytest.mark.parametrize("text", ["ок", "OK", "+", "👍", "так", "дякую", "+380 67 123 45 67", " "])
def test_neutral_messages(text):
    assert is_neutral(text)


@pytest.mark.parametrize("text", ["скільки коштує манікюр?", "сколько стоит", "how much is it"])
def test_real_messages_are_not_neutral(text):
    assert not is_neutral(text)


def test_detected_language_is_accepted():
    assert detect_client_language("uk", "ru", "сколько стоит маникюр?", RULED) == "ru"


def test_neutral_message_keeps_previous_language():
    assert detect_client_language("ru", "uk", "ок", RULED) == "ru"


def test_first_neutral_message_gets_primary_language():
    assert detect_client_language(None, "en", "+", RULED) == "uk"


def test_no_detected_language_keeps_previous_or_primary():
    assert detect_client_language("en", None, "some long message", RULED) == "en"
    assert detect_client_language(None, "", "some long message", RULED) == "uk"
    assert detect_client_language(None, "???", "some long message", RULED) == "uk"


def test_detected_language_name_is_normalized():
    assert detect_client_language(None, "Russian", "сколько стоит маникюр?", RULED) == "ru"


@pytest.mark.parametrize(
    ("client", "reply"),
    [("uk", "uk"), ("ru", "uk"), ("de", "en"), ("en", "en"), ("pl", "en")],
)
def test_reply_language_rule(client, reply):
    assert reply_language_for(client, RULED) == reply


@pytest.mark.parametrize(("client", "reply"), [("uk", "uk"), ("en", "en"), ("de", "uk")])
def test_reply_language_without_rules(client, reply):
    # the client's language when the bot speaks it, else the first tenant language
    assert reply_language_for(client, PLAIN) == reply


def test_explicit_rule_beats_star():
    settings = TenantSettings(languages=["uk", "en"], reply_language={"de": "uk", "*": "en"})
    assert reply_language_for("de", settings) == "uk"
    assert reply_language_for("fr", settings) == "en"


def test_rule_target_must_be_a_tenant_language():
    settings = TenantSettings(languages=["uk", "en"], reply_language={"ru": "ru"})
    with pytest.raises(AppError) as err:
        settings.validate_rules()
    assert err.value.code == "tenant.reply_language_unknown"
    assert err.value.params == {"source": "ru", "target": "ru", "languages": ["uk", "en"]}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({"languages": []}, "tenant.languages_empty"),
        ({"languages": ["Ukrainian"]}, "tenant.language_invalid"),
        ({"languages": ["uk", "uk"]}, "tenant.language_duplicate"),
        ({"languages": ["uk"], "reply_language": {"RU": "uk"}}, "tenant.language_invalid"),
        ({"languages": ["uk"], "silence_minutes": 0}, "tenant.silence_minutes_invalid"),
    ],
)
def test_invalid_settings(data, code):
    with pytest.raises(AppError) as err:
        TenantSettings(**data).validate_rules()
    assert err.value.code == code


def test_valid_rules_pass():
    assert RULED.validate_rules() is RULED
    assert RULED.primary_language == "uk"
