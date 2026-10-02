"""The turn pipeline used by run_turn() (docs/04 §5):

  1. rewrite  — self-contained question(s) + the client's language (LLM `rewrite`);
  2. language — accept the detected language, pick the reply language by the tenant rule;
  3. route    — one batch of questions to the decider (Jev) about the rewritten text;
  4. respond  — the reply text (LLM `reply`).

Stage 2 adds scenarios between route and respond.
"""

from typing import TYPE_CHECKING

from app.core import respond as respond_step
from app.core.context import TurnContext, TurnPlan
from app.core.language import detect_client_language, reply_language_for
from app.core.rewrite import PROMPT_KEY as REWRITE_KEY
from app.core.rewrite import rewrite
from app.core.routing import route
from app.prompts.store import load_prompts

if TYPE_CHECKING:
    from app.services import Services

PROMPT_KEYS = (REWRITE_KEY, *respond_step.PROMPT_KEYS)
REQUIRED_PROMPTS = (REWRITE_KEY, respond_step.SYSTEM_KEY, respond_step.USER_KEY)


async def full_pipeline(services: "Services", ctx: TurnContext) -> TurnPlan:
    prompts = await load_prompts(services.sm, ctx.tenant_id, PROMPT_KEYS, required=REQUIRED_PROMPTS)

    # the rewritten text is in the reply language known before this message
    before = ctx.reply_language or ctx.settings.primary_language
    with ctx.trace.step("rewrite", answer_language=before) as step:
        rw = await rewrite(services, ctx, prompts[REWRITE_KEY], before)
        step.update(
            llm_call_id=rw.llm_call_id,
            model=rw.model,
            questions=rw.questions,
            detected_language=rw.language,
        )

    client_language = detect_client_language(
        ctx.client_language, rw.language, ctx.input_text, ctx.settings
    )
    reply_language = reply_language_for(client_language, ctx.settings)
    ctx.trace.note(
        "language",
        {"detected": rw.language, "client": client_language, "reply": reply_language},
    )

    decided = await route(services, ctx, rw.text)
    reply = await respond_step.respond(services, ctx, prompts, decided, rw.text, reply_language)

    return TurnPlan(
        reply_text=reply.text,
        rewritten=rw.text,
        client_language=client_language,
        reply_language=reply_language,
        route=decided.to_dict(),
        handover=decided.wants_human,
        reply_meta={"prompt_key": reply.prompt_key, "prompt_version": reply.prompt_version},
    )
