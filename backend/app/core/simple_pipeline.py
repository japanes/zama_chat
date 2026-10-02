"""Stage 0 pipeline: history → reply LLM → answer. No rewrite, no route, no scenarios.

Kept after stage 1 as a fallback for checks of the bare LLM role (`zama` CLI, tests).
"""

from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext, TurnPlan
from app.db.models import LlmRole, MessageRole

if TYPE_CHECKING:
    from app.services import Services


def build_messages(ctx: TurnContext) -> list[dict[str, Any]]:
    s = ctx.settings
    lang = ctx.reply_language or s.primary_language
    parts = [
        f"You are {s.bot_name or 'the assistant'} of {s.company_title or ctx.tenant_title}.",
        s.business_description,
        f"Always answer in the language with code '{lang}'. Be brief and friendly.",
    ]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "\n\n".join(p for p in parts if p)}
    ]
    for item in ctx.history:
        role = "user" if item.role == MessageRole.CLIENT else "assistant"
        if item.role == MessageRole.SYSTEM:
            continue
        messages.append({"role": role, "content": item.text})
    messages.append({"role": "user", "content": ctx.input_text})
    return messages


async def simple_pipeline(services: "Services", ctx: TurnContext) -> TurnPlan:
    with ctx.trace.step("respond", mode="simple") as step:
        result = await services.llm.complete(
            LlmRole.REPLY,
            build_messages(ctx),
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            prompt_key="simple_reply",
            overrides=ctx.overrides,
        )
        step["llm_call_id"] = str(result.call_id)
        step["model"] = result.model
    lang = ctx.reply_language or ctx.settings.primary_language
    return TurnPlan(reply_text=result.text.strip(), reply_language=lang)
