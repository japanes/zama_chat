"""The turn pipeline used by run_turn() (docs/04 §5):

  1. rewrite  — self-contained question(s) + the client's language (LLM `rewrite`);
  2. language — accept the detected language, pick the reply language by the tenant rule;
  3. route    — one batch of questions to the decider (Jev) about the rewritten text,
                the scenario questions included;
  4. search   — catalog / knowledge when the route asked for it (search/step.py);
  5. scenarios — start / stop / extract / filters / next question (scenarios/runtime.py);
  6. respond  — the reply text (LLM `reply`) with the found context and the scenario step.

A channel without scenarios (and without open runs) goes exactly the stage 1 way.
"""

from typing import TYPE_CHECKING

from app.core import respond as respond_step
from app.core.context import TurnContext, TurnPlan
from app.core.language import detect_client_language, reply_language_for
from app.core.rewrite import PROMPT_KEY as REWRITE_KEY
from app.core.rewrite import rewrite
from app.core.routing import route
from app.prompts.builtin import (
    EXTRACT_KEY,
    RECEIPT_VISION_KEY,
    SELECT_PRESENTED,
    SELECT_PRESENTED_KEY,
    prompt_rules,
)
from app.prompts.store import load_prompts
from app.scenarios.questions import build_questions
from app.scenarios.runtime import run_scenarios
from app.scenarios.store import load_view
from app.search.presented import with_presented
from app.search.service import tenant_content
from app.search.step import UNKNOWN_HANDOVER, SearchOutcome, search_question, search_step

if TYPE_CHECKING:
    from app.services import Services

PROMPT_KEYS = (
    REWRITE_KEY,
    *respond_step.PROMPT_KEYS,
    EXTRACT_KEY,
    SELECT_PRESENTED_KEY,
    RECEIPT_VISION_KEY,
)
REQUIRED_PROMPTS = (REWRITE_KEY, respond_step.SYSTEM_KEY, respond_step.USER_KEY)


async def full_pipeline(services: "Services", ctx: TurnContext) -> TurnPlan:
    prompts = await load_prompts(services.sm, ctx.tenant_id, PROMPT_KEYS, required=REQUIRED_PROMPTS)
    view = await load_view(
        services.sm,
        tenant_id=ctx.tenant_id,
        channel_id=ctx.channel_id,
        conversation_id=ctx.conversation_id,
        now=ctx.now,
    )
    ctx.extra["scenarios"] = view
    content = await tenant_content(services, ctx.tenant_id)

    # the rewritten text is in the reply language known before this message
    before = ctx.reply_language or ctx.settings.primary_language
    with ctx.trace.step("rewrite", answer_language=before) as step:
        rw = await rewrite(
            services,
            ctx,
            prompts[REWRITE_KEY],
            before,
            presented_rules=prompt_rules(prompts, SELECT_PRESENTED_KEY, SELECT_PRESENTED),
        )
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

    sq = search_question(content)
    search_questions = [sq] if sq is not None else []

    if not view.active:
        decided = await route(services, ctx, rw.text, extra_questions=search_questions)
        found = await search_step(
            services, ctx, rw.text, decided.decisions, reply_language, content
        )
        handover = _handover(ctx, decided.wants_human, found)
        reply = await respond_step.respond(
            services,
            ctx,
            prompts,
            decided,
            rw.text,
            reply_language,
            search=found,
            handover=handover,
        )
        return TurnPlan(
            reply_text=reply.text or None,
            rewritten=rw.text,
            client_language=client_language,
            reply_language=reply_language,
            route=_route_info(decided.to_dict(), found),
            handover=handover,
            state=_state(ctx, found),
            reply_meta={"prompt_key": reply.prompt_key, "prompt_version": reply.prompt_version},
            variants=reply.variants,
        )

    # scenario questions and texts are in the language of the rewritten text
    asked = build_questions(view, before)
    decided = await route(
        services,
        ctx,
        rw.text,
        extra_questions=[*search_questions, *asked.questions],
        sections=asked.sections,
    )
    found = await search_step(services, ctx, rw.text, decided.decisions, reply_language, content)
    # the catalog fields of scenarios take their options from here (scenarios/catalog.py)
    ctx.extra["search"] = found
    with ctx.trace.step("scenarios") as step:
        scenarios = await run_scenarios(
            services,
            ctx,
            view,
            decided.decisions,
            prompts=prompts,
            rewritten=rw.text,
            question_language=before,
            client_language=client_language,
            reply_language=reply_language,
            wants_human=decided.wants_human,
            step=step,
        )
        step["summary"] = scenarios.summary
    # a scenario step answers the client; "not found" matters only for a real question
    handover = _handover(ctx, decided.wants_human, found)
    reply = await respond_step.respond(
        services,
        ctx,
        prompts,
        decided,
        rw.text,
        reply_language,
        plan=scenarios.plan,
        search=found,
        handover=handover,
    )
    route_info = _route_info(decided.to_dict(), found)
    route_info["scenarios"] = {
        k: scenarios.summary[k] for k in ("stop", "start", "started", "focus")
    }
    return TurnPlan(
        reply_text=reply.text or None,
        rewritten=rw.text,
        client_language=client_language,
        reply_language=reply_language,
        route=route_info,
        handover=handover,
        state=_state(ctx, found),
        reply_meta={"prompt_key": reply.prompt_key, "prompt_version": reply.prompt_version},
        variants=reply.variants,
        writes=[scenarios.write] if scenarios.write is not None else [],
    )


def _handover(ctx: TurnContext, wants_human: bool, found: SearchOutcome) -> bool:
    if wants_human:
        return True
    # TODO(review): hand over when the answer is not found (tenant setting unknown_answer)
    return found.not_found and ctx.settings.unknown_answer == UNKNOWN_HANDOVER


def _route_info(info: dict, found: SearchOutcome) -> dict:
    if found.searched:
        info["search"] = {
            "wanted": found.wanted,
            "items": [f.ref for f in found.items],
            "knowledge": [f.ref for f in found.knowledge],
            "not_found": found.not_found,
        }
    return info


def _state(ctx: TurnContext, found: SearchOutcome) -> dict | None:
    """New conversations.state when the reply shows new catalog options (or a scenario
    catalog field asked with options — ctx.extra["presented"])."""
    presented = ctx.extra.get("presented") or found.presented
    if not presented:
        return None
    return with_presented(ctx.state, presented, at=ctx.now.isoformat())
