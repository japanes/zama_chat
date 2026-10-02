"""Prompt rendering: %PLACEHOLDER% substitution and filling fields of JSON prompts.

Legacy prompts come in two kinds: plain text with %NAME% placeholders (service scripts)
and JSON documents (system_prompt, user_prompt, context_manager_*), where the bot fills
top-level fields (user_question, answer_language, dialogue…) and sends the JSON as text.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

from app.errors import AppError

PLACEHOLDER_RE = re.compile(r"%([A-Z][A-Z0-9_]*)%")


def placeholders(body: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(body))


def render_text(body: str, values: Mapping[str, Any], *, strict: bool = False) -> str:
    """Replace %NAME% with values["NAME"]. Unknown placeholders stay as they are, or raise
    with `strict` (so a typo in a prompt is found before it reaches the model)."""

    def sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values:
            return str(values[name])
        if strict:
            raise AppError("prompt.placeholder_unknown", {"name": name})
        return match.group(0)

    return PLACEHOLDER_RE.sub(sub, body)


def _substitute(value: Any, values: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        return render_text(value, values)
    if isinstance(value, list):
        return [_substitute(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, values) for k, v in value.items()}
    return value


def parse_json_prompt(body: str, key: str = "") -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise AppError("prompt.invalid_json", {"key": key, "error": str(exc)[:200]}) from exc
    if not isinstance(data, dict):
        raise AppError("prompt.invalid_json", {"key": key, "error": "not an object"})
    return data


def render_json(
    body: str,
    fields: Mapping[str, Any] | None = None,
    values: Mapping[str, Any] | None = None,
    *,
    key: str = "",
) -> dict[str, Any]:
    """JSON prompt → dict: placeholders substituted in every string, then top-level
    `fields` set (added when the prompt does not have them yet)."""
    data = parse_json_prompt(body, key)
    if values:
        data = _substitute(data, values)
    for name, value in (fields or {}).items():
        data[name] = value
    return data


def to_text(data: Mapping[str, Any]) -> str:
    """The JSON prompt as the model sees it (kept readable, non-ASCII as is)."""
    return json.dumps(data, ensure_ascii=False, indent=1)
