"""Notification texts in en / uk / ru (templates/<language>.json).

`make i18n-check` checks that every language has the same keys. Values are plain text
(Telegram without parse_mode, email escaped), so client words need no escaping here.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

TEMPLATES = Path(__file__).parent / "templates"
DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "uk", "ru")


@lru_cache
def _load(language: str) -> dict[str, Any]:
    path = TEMPLATES / f"{language}.json"
    if not path.exists():
        path = TEMPLATES / f"{DEFAULT_LANGUAGE}.json"
    return json.loads(path.read_text("utf-8"))


class _Safe(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return ""


def lookup(language: str, path: str) -> str:
    node: Any = _load(language)
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, str) and language != DEFAULT_LANGUAGE:
        return lookup(DEFAULT_LANGUAGE, path)
    return node if isinstance(node, str) else path


def render(language: str, template: str, values: dict[str, Any]) -> tuple[str, str]:
    """(subject, body) of a template in a language."""
    data = _Safe(values)
    if template == "scenario" and values.get("event"):
        data["event"] = lookup(language, f"event.{values['event']}")
        if values.get("field"):
            data["event"] = f"{data['event']} ({values['field']})"
    if template == "scenario":
        data["fields"] = values.get("fields") or lookup(language, "no_fields")
    if template == "channel_error" and values.get("error"):
        code = str(values["error"])
        found = lookup(language, f"error.{code}")
        data["error"] = code if found == f"error.{code}" else found
    if not values.get("text"):
        data["text"] = lookup(language, "no_text")
    subject = lookup(language, f"subject.{template}").format_map(data)
    body = lookup(language, f"body.{template}").format_map(data)
    # a template without {link} still gets nothing dangling
    return subject, body.strip()
