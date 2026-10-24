"""Step 1 — rewrite (the legacy "context manager"): the client's message → self-contained
questions + the language of the message.

Prompt `rewrite` (prompts/seed.py): a JSON document; the bot fills `dialogue`,
`selected_line`, `answer_language` and sends it as one user message. Strict JSON schema
`{question: [str], language: str}`. A scenario in the focus adds `scenario_card`: its
answers so far with how the bot asked them, and the rule to trust them over the dialogue
(prompt `scenario_card`, `rewrite_rules`).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.core.language import language_name
from app.db.models import LlmRole, MessageRole
from app.prompts.builtin import SCENARIO_CARD, SELECT_PRESENTED
from app.prompts.render import render_json, to_text
from app.prompts.store import PromptText
from app.search.presented import presented, rewrite_view

if TYPE_CHECKING:
    from app.services import Services

PROMPT_KEY = "rewrite"
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "array", "items": {"type": "string"}},
        "language": {"type": "string"},
    },
    "required": ["question", "language"],
    "additionalProperties": False,
}
SPEAKER = {
    MessageRole.CLIENT: "client",
    MessageRole.BOT: "bot",
    MessageRole.OPERATOR: "manager",
}


@dataclass(frozen=True)
class Rewrite:
    questions: list[str]
    text: str  # questions joined by "\n" — what route / search / respond see
    language: str | None  # as the model returned it (code, maybe empty)
    llm_call_id: str | None  # None — the text went on without the model (as_written)
    model: str | None


def skips_rewrite(ctx: TurnContext) -> bool:
    """The turn input is long enough to go on as the client wrote it (tenant setting
    `rewrite_skip_chars`; 0 — never)."""
    limit = ctx.settings.rewrite_skip_chars
    return limit > 0 and len(ctx.input_text.strip()) >= limit


def as_written(ctx: TurnContext) -> Rewrite:
    """The client's own text instead of the rewrite. No language: the previous one stays."""
    text = ctx.input_text.strip()
    return Rewrite(questions=[text], text=text, language=None, llm_call_id=None, model=None)


def dialogue_lines(ctx: TurnContext) -> list[str]:
    """History without system messages, then this turn's input."""
    lines = [f"{SPEAKER[item.role]}: {item.text}" for item in ctx.history if item.role in SPEAKER]
    lines.append(f"client: {ctx.input_text}")
    return lines


def answer_language_line(code: str) -> str:
    return f"Give the answer in {language_name(code)} language"


def build_messages(
    ctx: TurnContext,
    prompt: PromptText,
    answer_language: str,
    *,
    presented_rules: list[str] | None = None,
    card: list[dict[str, Any]] | None = None,
    card_rules: list[str] | None = None,
) -> list[dict]:
    data = render_json(
        prompt.body,
        {
            "dialogue": dialogue_lines(ctx),
            "selected_line": ctx.input_text,
            "answer_language": answer_language_line(answer_language),
        },
        key=prompt.key,
    )
    # stage 5: the options the bot showed last, so «давайте другий» gets its full title
    options = presented(ctx.state)
    if options:
        data["last_presented_options"] = rewrite_view(options)
        data["last_presented_rules"] = presented_rules or list(SELECT_PRESENTED["rules"])
    if card:
        data["scenario_card"] = {
            "rules": card_rules or list(SCENARIO_CARD["rewrite_rules"]),
            "answers": card,
        }
    return [{"role": "user", "content": to_text(data)}]


async def rewrite(
    services: "Services",
    ctx: TurnContext,
    prompt: PromptText,
    answer_language: str,
    *,
    presented_rules: list[str] | None = None,
    card: list[dict[str, Any]] | None = None,
    card_rules: list[str] | None = None,
) -> Rewrite:
    """`answer_language` is the reply language known BEFORE this message (the previous
    turn's, or the tenant's first). If this message switches the language, the rewritten
    text stays in the old one for this turn — the rewrite is not run twice (cost, time).
    TODO(review): check on mixed-language dialogues whether a second pass is worth it."""
    result = await services.llm.complete(
        LlmRole.REWRITE,
        build_messages(
            ctx,
            prompt,
            answer_language,
            presented_rules=presented_rules,
            card=card,
            card_rules=card_rules,
        ),
        tenant_id=ctx.tenant_id,
        turn_id=ctx.turn_id,
        schema=SCHEMA,
        schema_name="rewrite",
        prompt_key=prompt.key,
        prompt_version=prompt.version,
        overrides=ctx.overrides,
    )
    data = result.data or {}
    questions = [q.strip() for q in data.get("question") or [] if isinstance(q, str) and q.strip()]
    if not questions:
        # the model returned nothing usable: route on the client's own text
        questions = [ctx.input_text.strip()]
    language = data.get("language")
    return Rewrite(
        questions=questions,
        text="\n".join(questions),
        language=language if isinstance(language, str) else None,
        llm_call_id=str(result.call_id),
        model=result.model,
    )
