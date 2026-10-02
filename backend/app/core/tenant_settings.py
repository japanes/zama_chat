"""Tenant settings (tenants.settings jsonb) and per-channel overrides.

A channel may override languages, reply_language and business_description; anything it
does not set is taken from the tenant.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.errors import AppError

LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$")
ANY_LANGUAGE = "*"

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
        if self.silence_minutes < 1:
            raise AppError("tenant.silence_minutes_invalid", {"value": self.silence_minutes})
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
