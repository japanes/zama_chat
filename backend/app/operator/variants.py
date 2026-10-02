"""Operator mode: the bot does not answer by itself, it prepares several reply
variants; an operator picks one (or edits it) in the admin queue.

One LLM call of the `reply` role: with a JSON schema when the model supports it,
otherwise variants separated by a line `---`.
"""

import json
import re
from typing import Any

MIN_VARIANTS = 2
MAX_VARIANTS = 5
DEFAULT_VARIANTS = 3
SEPARATOR = "---"

# TODO(review): wording of the instruction for operator variants
RULE_SCHEMA = (
    "An operator will choose the reply. Write {n} different variants of the reply to the "
    "client, each one complete and ready to send as is: different in wording and approach "
    "(for example shorter / more detailed / with a question back), same facts and the "
    'same language. Return JSON: {{"variants": ["…", …]}}.'
)
RULE_TEXT = (
    "An operator will choose the reply. Write {n} different variants of the reply to the "
    "client, each one complete and ready to send as is: different in wording and approach, "
    "same facts and the same language. Separate the variants with a line containing only "
    "---. No numbering, no titles, nothing else."
)


def variant_count(channel_settings: dict[str, Any]) -> int:
    value = channel_settings.get("operator_variants")
    if isinstance(value, int) and not isinstance(value, bool):
        return min(max(value, MIN_VARIANTS), MAX_VARIANTS)
    return DEFAULT_VARIANTS


def schema(n: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "variants": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": n,
            }
        },
        "required": ["variants"],
        "additionalProperties": False,
    }


def instruction(n: int, with_schema: bool) -> str:
    return (RULE_SCHEMA if with_schema else RULE_TEXT).format(n=n)


_NUMBER = re.compile(r"^\s*(?:variant\s*)?\d+[.)\]:]\s+", re.IGNORECASE)


def parse(text: str, data: dict[str, Any] | None, n: int) -> list[str]:
    """Variants from a structured answer or from text split by `---`. Never empty when
    the model wrote anything: one variant is better than none."""
    items: list[Any] = []
    if isinstance(data, dict) and isinstance(data.get("variants"), list):
        items = data["variants"]
    else:
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                loaded = json.loads(stripped)
            except ValueError:
                loaded = None
            if isinstance(loaded, dict) and isinstance(loaded.get("variants"), list):
                items = loaded["variants"]
        if not items:
            items = re.split(r"(?m)^\s*-{3,}\s*$", stripped)
    out: list[str] = []
    for item in items:
        value = _NUMBER.sub("", str(item)).strip()
        if value and value not in out:
            out.append(value)
    return out[:n]
