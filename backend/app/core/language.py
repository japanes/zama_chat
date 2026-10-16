"""Dialogue languages (docs/04 §5 «Языки»).

The client's language is detected by the LLM on the rewrite step; this module only decides
whether to accept it and which language the bot answers in:
- a short / neutral message ("ок", "+", an emoji, a phone number) keeps the previous one;
- the reply language comes from the tenant rule: explicit rule → "*" rule → the client's
  language if the bot speaks it → the first tenant language.
- a conversation started over POST /v1/runs/start with the client's language keeps the reply
  language the tenant rule gives for it (the pin).
"""

import re
from typing import Any

from app.core.tenant_settings import ANY_LANGUAGE, LANG_RE, TenantSettings

# ISO 639-1 → English name (for prompts: "Give the answer in Ukrainian language")
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "uk": "Ukrainian",
    "ru": "Russian",
    "pl": "Polish",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "cs": "Czech",
    "sk": "Slovak",
    "ro": "Romanian",
    "bg": "Bulgarian",
    "be": "Belarusian",
    "kk": "Kazakh",
    "ka": "Georgian",
    "hy": "Armenian",
    "az": "Azerbaijani",
    "tr": "Turkish",
    "he": "Hebrew",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "et": "Estonian",
    "hu": "Hungarian",
    "fi": "Finnish",
    "sv": "Swedish",
    "no": "Norwegian",
    "da": "Danish",
    "el": "Greek",
}
_BY_NAME = {name.lower(): code for code, name in LANGUAGE_NAMES.items()}

# messages that carry no language: they never switch the dialogue language
# TODO(review): extend the list from real dialogues
NEUTRAL_WORDS = {
    "ok", "okay", "ок", "окей", "оке", "ага", "угу", "так", "да", "ні", "нет", "не",
    "yes", "no", "yep", "nope", "+", "++", "-", "👍", "ok!", "ок!", "дякую", "спасибо",
    "thanks", "thx", "sure",
}  # fmt: skip
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.split("-", 1)[0], code)


def normalize_code(value: str | None) -> str | None:
    """'uk' / 'UK' / 'Ukrainian' / 'uk-UA' → 'uk'; unknown or empty → None."""
    if not value:
        return None
    value = value.strip().lower()
    if value in _BY_NAME:
        return _BY_NAME[value]
    code = value.replace("_", "-").split("-", 1)[0]
    return code if LANG_RE.match(code) else None


def is_neutral(text: str) -> bool:
    """Too short or a known neutral word: the message says nothing about its language.

    "дякую" and "спасибо" are neutral on purpose: a polite word does not switch a whole
    dialogue."""
    stripped = text.strip().lower()
    if stripped in NEUTRAL_WORDS:
        return True
    return len(_LETTER_RE.findall(stripped)) < 3


def detect_client_language(
    previous: str | None, detected: str | None, text: str, settings: TenantSettings
) -> str:
    """The client's language after this message."""
    code = normalize_code(detected)
    if code is None or is_neutral(text):
        return previous or settings.primary_language
    return code


# conversations.state key: the reply language of a conversation started over
# POST /v1/runs/start with the client's language (the language of the caller's interface),
# by the tenant rule for it. The bot keeps it, not the one guessed from a short answer.
REPLY_LANGUAGE_PIN = "reply_language_pin"


def pinned_reply_language(state: dict[str, Any] | None, settings: TenantSettings) -> str | None:
    """The pinned reply language of the conversation, when it is one of the tenant's."""
    code = normalize_code((state or {}).get(REPLY_LANGUAGE_PIN))
    return code if code in settings.languages else None


def reply_language_for(client_language: str, settings: TenantSettings) -> str:
    rules = settings.reply_language
    if client_language in rules:
        return rules[client_language]
    if ANY_LANGUAGE in rules:
        return rules[ANY_LANGUAGE]
    if client_language in settings.languages:
        return client_language
    return settings.primary_language
