"""Tenant settings (tenants.settings jsonb) and per-channel overrides.

A channel may override languages, reply_language and business_description; anything it
does not set is taken from the tenant.
"""

import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.errors import AppError

LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
ANY_LANGUAGE = "*"
UNKNOWN_ANSWER = ("say", "handover")
RECEIPT_REVIEW = ("always", "issues_only")
REGION_RE = re.compile(r"^[A-Z]{2}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

# keys a channel may override on top of the tenant
CHANNEL_OVERRIDABLE = ("languages", "reply_language", "business_description", "bot_name")


class TenantSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    # languages the bot answers in; scenarios are written in them too
    languages: list[str] = Field(default_factory=lambda: ["en"])
    # client language → reply language (forced); "*" = every other language.
    # Example: {"uk": "uk", "ru": "uk", "*": "en"}
    reply_language: dict[str, str] = Field(default_factory=dict)
    business_description: str = ""
    company_title: str = ""
    bot_name: str = ""
    timezone: str = "Europe/Kyiv"
    silence_minutes: int = 15
    # language of service questions to Jev (route); the probe decides — see docs/04 §5
    jev_question_language: str = "en"
    # the client asked something the search did not find (stage 5): "say" — the bot
    # says it has no answer (legacy i_dont_know_rules); "handover" — also hands the
    # conversation to a human. TODO(review): default
    unknown_answer: str = "say"
    # phone numbers without a country code are read as numbers of this country
    phone_region: str = "UA"
    # payment receipts (docs/03 §8): "always" — a person checks every receipt (default);
    # "issues_only" — code accepts a receipt without issues. Only an owner / admin may
    # switch it (the settings API is theirs); the change goes to the audit log.
    receipt_review: str = "always"
    # even with issues_only a person checks receipts above these amounts, per currency:
    # {"UAH": "5000", "USD": "200"}
    receipt_review_above: dict[str, Decimal] = Field(default_factory=dict)

    def validate_rules(self) -> "TenantSettings":
        if not self.languages:
            raise AppError("tenant.languages_empty")
        for lang in self.languages:
            if not LANG_RE.match(lang):
                raise AppError("tenant.language_invalid", {"language": lang})
        if len(set(self.languages)) != len(self.languages):
            raise AppError("tenant.language_duplicate")
        for source, target in self.reply_language.items():
            if source != ANY_LANGUAGE and not LANG_RE.match(source):
                raise AppError("tenant.language_invalid", {"language": source})
            if target not in self.languages:
                raise AppError(
                    "tenant.reply_language_unknown",
                    {"source": source, "target": target, "languages": self.languages},
                )
        if self.unknown_answer not in UNKNOWN_ANSWER:
            raise AppError("tenant.unknown_answer_invalid", {"value": self.unknown_answer})
        if self.silence_minutes < 1:
            raise AppError("tenant.silence_minutes_invalid", {"value": self.silence_minutes})
        if not REGION_RE.match(self.phone_region):
            raise AppError("tenant.phone_region_invalid", {"value": self.phone_region})
        if self.receipt_review not in RECEIPT_REVIEW:
            raise AppError("tenant.receipt_review_invalid", {"value": self.receipt_review})
        for currency, amount in self.receipt_review_above.items():
            if not CURRENCY_RE.match(currency) or amount < 0:
                raise AppError("tenant.receipt_threshold_invalid", {"currency": currency})
        return self

    @property
    def primary_language(self) -> str:
        return self.languages[0]


def effective_settings(
    tenant_settings: dict[str, Any], channel_settings: dict[str, Any] | None = None
) -> TenantSettings:
    """Tenant settings with the channel's overrides applied."""
    data = dict(tenant_settings or {})
    for key in CHANNEL_OVERRIDABLE:
        value = (channel_settings or {}).get(key)
        if value not in (None, "", [], {}):
            data[key] = value
    return TenantSettings.model_validate(data)
