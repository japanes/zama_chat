"""Tenant settings: language rules and channel overrides."""

import pytest

from app.core.tenant_settings import TenantSettings, effective_settings
from app.errors import AppError


def test_defaults_are_valid():
    s = TenantSettings().validate_rules()
    assert s.languages == ["en"]
    assert s.primary_language == "en"


def test_reply_rule_with_tenant_languages_is_valid():
    s = TenantSettings(languages=["uk", "en"], reply_language={"uk": "uk", "ru": "uk", "*": "en"})
    assert s.validate_rules() is s
    assert s.primary_language == "uk"


@pytest.mark.parametrize(
    ("data", "code", "params"),
    [
        ({"languages": []}, "tenant.languages_empty", {}),
        ({"languages": ["UK"]}, "tenant.language_invalid", {"language": "UK"}),
        ({"languages": ["ukrainian"]}, "tenant.language_invalid", {"language": "ukrainian"}),
        ({"languages": ["uk", "uk"]}, "tenant.language_duplicate", {}),
        (
            {"languages": ["uk"], "reply_language": {"R U": "uk"}},
            "tenant.language_invalid",
            {"language": "R U"},
        ),
        (
            {"languages": ["uk", "en"], "reply_language": {"ru": "pl"}},
            "tenant.reply_language_unknown",
            {"source": "ru", "target": "pl", "languages": ["uk", "en"]},
        ),
        ({"silence_minutes": 0}, "tenant.silence_minutes_invalid", {"value": 0}),
        ({"rewrite_skip_chars": -1}, "tenant.rewrite_skip_chars_invalid", {"value": -1}),
    ],
)
def test_invalid_rules(data, code, params):
    with pytest.raises(AppError) as err:
        TenantSettings(**data).validate_rules()
    assert err.value.code == code
    assert err.value.params == params


def test_region_codes_are_allowed():
    TenantSettings(languages=["pt-br", "en"]).validate_rules()


def test_channel_overrides_only_allowed_keys():
    tenant = {
        "languages": ["uk", "en"],
        "business_description": "Salon",
        "company_title": "Zama",
        "silence_minutes": 20,
    }
    channel = {
        "languages": ["en"],
        "business_description": "",  # empty → tenant value stays
        "company_title": "Other",  # not overridable
        "silence_minutes": 5,  # not overridable
        "operator_mode": True,
    }
    s = effective_settings(tenant, channel)
    assert s.languages == ["en"]
    assert s.business_description == "Salon"
    assert s.company_title == "Zama"
    assert s.silence_minutes == 20


def test_effective_settings_without_channel():
    s = effective_settings({"languages": ["ru"]}, None)
    assert s.languages == ["ru"]
    assert effective_settings({}, {}).languages == ["en"]


def test_unknown_keys_are_kept():
    s = effective_settings({"future_option": 1})
    assert s.model_dump()["future_option"] == 1
