"""Shared prompts from docs/legacy-prompts → the prompts table (tenant_id NULL).

Every *.txt becomes a prompt with key = file stem, as is. On top of that:
- service scripts 2–5 (`scripts/<n>_<type>.json`, the prompt of the first step) become
  `service_<type>` — the answers to greeting / goodbye / ethical violation / "are you a bot";
- `rewrite` is assembled from context_manager_question + the question rules and the
  language rule of context_manager (docs/03 §4), without the role/type parts.

Idempotent: a new version is added only when the body differs from the active one.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.prompts.store import active_prompts, save_version
from app.settings import PROJECT_ROOT

LEGACY_DIR = PROJECT_ROOT / "docs" / "legacy-prompts"
SERVICE_SCRIPTS = {
    "2_greeting_only.json": "service_greeting_only",
    "3_goodbye_only.json": "service_goodbye_only",
    "4_ethical_violation.json": "service_ethical_violation",
    "5_not_a_bot.json": "service_not_a_bot",
}
REWRITE_KEY = "rewrite"


@dataclass(frozen=True)
class SeedResult:
    created: list[str]
    unchanged: list[str]


def _service_prompt(path: Path) -> str:
    script = json.loads(path.read_text(encoding="utf-8"))
    steps = script["json"]
    if isinstance(steps, str):
        steps = json.loads(steps)
    return steps[0]["prompt"]


def build_rewrite(directory: Path) -> str:
    """The rewrite prompt: dialogue + selected line → self-contained questions + language.

    The bot fills `dialogue`, `selected_line`, `answer_language` (see core/rewrite.py)."""
    # TODO(review): merged from two legacy prompts; check the wording on real dialogues
    question = json.loads((directory / "context_manager_question.txt").read_text("utf-8"))
    manager = json.loads((directory / "context_manager.txt").read_text("utf-8"))
    common = [
        rule.replace("to standard English", "to standard language")
        for rule in question["rules"]["common"]
        if "'type' and 'role'" not in rule
    ]
    rules: dict[str, Any] = {
        "common": common,
        "question": {
            "rule": question["rules"]["question"]["rule"],
            **{k: v for k, v in manager["rules"]["question"].items()},
            "answer_language": [
                "Write every item of 'question' in the language named in the "
                "'answer_language' field; translate if the client wrote in another language",
                "Keep names, numbers, dates, prices, phone numbers and e-mails exactly as "
                "the client wrote them",
            ],
        },
        "language": [
            "Detect the language of the client's latest message (the 'selected_line' field) "
            "and set the 'language' field to its ISO 639-1 code (e.g. 'en', 'uk', 'ru', 'de')",
            "Do not infer or guess — rely strictly on the client's input. If the language is "
            "unclear (a number, an emoji, 'ok', '+'), set 'language' to an empty string",
        ],
    }
    prompt = {
        "return": {
            "title": "Return JSON Object",
            "description": (
                "Return only a JSON object with fields 'question' and 'language' without "
                "additional text or formatting"
            ),
            "example": {"question": ["user message"], "language": "en"},
        },
        "rules": rules,
        "dialogue": [],
        "selected_line": "",
        "answer_language": "",
    }
    return json.dumps(prompt, ensure_ascii=False, indent=4)


def legacy_prompts(directory: Path = LEGACY_DIR) -> dict[str, str]:
    prompts = {path.stem: path.read_text("utf-8") for path in sorted(directory.glob("*.txt"))}
    for name, key in SERVICE_SCRIPTS.items():
        prompts[key] = _service_prompt(directory / "scripts" / name)
    prompts[REWRITE_KEY] = build_rewrite(directory)
    return prompts


async def seed_prompts(
    session: AsyncSession, directory: Path = LEGACY_DIR, *, author: str = "seed"
) -> SeedResult:
    prompts = legacy_prompts(directory)
    active = await active_prompts(session, None, prompts)
    created: list[str] = []
    unchanged: list[str] = []
    for key, body in prompts.items():
        current = active.get(key)
        if current is not None and current.tenant_id is None and current.body == body:
            unchanged.append(key)
            continue
        await save_version(session, key=key, body=body, author=author, note="docs/legacy-prompts")
        created.append(key)
    return SeedResult(created=created, unchanged=unchanged)
