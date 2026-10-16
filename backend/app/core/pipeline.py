"""The turn pipeline used by run_turn() (docs/04 §5):

  1. rewrite  — self-contained question(s) + the client's language (LLM `rewrite`);
  2. language — accept the detected language, pick the reply language by the tenant rule;
  3. route    — one batch of questions to the decider (Jev) about the rewritten text,
                the scenario questions included;
  4. search   — catalog / knowledge when the route asked for it (search/step.py);
  5. scenarios — start / stop / extract / filters / next question (scenarios/runtime.py);
  6. respond  — the reply text (LLM `reply`) with the found context and the scenario step.

A channel without scenarios (and without open runs) goes exactly the stage 1 way.

An open run of a scenario with `only_scenario` makes the turn the scenarios' alone: the
route asks no service_type / wants_human, nothing is searched, no other scenario starts by
itself and there is no client_question — the reply is the scenario step and nothing else.

The scenario in the focus may want more or fewer last messages of the dialogue than the
default (`history_messages` of its file); the card of the dialogue goes to the rewrite
and to the reply (scenarios/plan.py, `known`).
"""

from typing import TYPE_CHECKING

from app.core import respond as respond_step
from app.core.context import TurnContext, TurnPlan
from app.core.language import (
    detect_client_language,
    pinned_reply_language,
    reply_language_for,
)
from app.core.rewrite import PROMPT_KEY as REWRITE_KEY
from app.core.rewrite import rewrite
from app.core.routing import Route, route
from app.prompts.builtin import (
    EXTRACT_KEY,
    RECEIPT_VISION_KEY,
    SCENARIO_CARD,
    SCENARIO_CARD_KEY,
    SELECT_PRESENTED,
    SELECT_PRESENTED_KEY,
    prompt_rules,
)
from app.prompts.store import load_prompts
from app.scenarios.plan import build_plan, view_card
from app.scenarios.questions import build_questions
from app.scenarios.runtime import run_scenarios
from app.scenarios.store import ScenarioView, load_view
from app.scenarios.work import WorkRun
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
    await fit_history(services, ctx, view)
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
            card=view_card(view, before),
            card_rules=prompt_rules(prompts, SCENARIO_CARD_KEY, SCENARIO_CARD, "rewrite_rules"),
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
    pinned = pinned_reply_language(ctx.state, ctx.settings)
    reply_language = pinned or reply_language_for(client_language, ctx.settings)
    note = {"detected": rw.language, "client": client_language, "reply": reply_language}
    if pinned is not None:
        note["pinned"] = True  # the caller of POST /v1/runs/start chose the language
    ctx.trace.note("language", note)

    # an open scenario with `only_scenario`: the turn is the scenarios' alone — no service
    # replies, no hand-over, no search (scenarios/schema.py)
    only = view.only_scenario
    sq = None if only else search_question(content)
    search_questions = [sq] if sq is not None else []
    if only:
        ctx.trace.note("only_scenario", True)

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
        general=not only,
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
    if only:
        route_info["scenarios"]["only_scenario"] = True
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


async def ask_pipeline(services: "Services", ctx: TurnContext) -> TurnPlan:
    """A bot turn (core/turn.py, `bot_only`): no client message, the bot speaks first. The
    run in the focus has its question set already (POST /v1/runs/{id}/fields); the reply
    model words it from the card and the dialogue. No rewrite, route or extract: there is
    nothing to read. Nothing to ask → no reply."""
    prompts = await load_prompts(services.sm, ctx.tenant_id, PROMPT_KEYS, required=REQUIRED_PROMPTS)
    view = await load_view(
        services.sm,
        tenant_id=ctx.tenant_id,
        channel_id=ctx.channel_id,
        conversation_id=ctx.conversation_id,
        now=ctx.now,
    )
    ctx.extra["scenarios"] = view
    await fit_history(services, ctx, view)
    reply_language = ctx.reply_language or ctx.settings.primary_language
    runs = [WorkRun.from_open(r) for r in view.runs]
    plan = build_plan(runs, language=reply_language, client_question=False)
    if plan is None or not plan.asks:
        ctx.trace.note("bot_turn", {"asks": False})
        return TurnPlan(reply_language=reply_language, route={"bot_turn": True})
    plan.bot_turn = True
    with ctx.trace.step("scenarios") as step:
        step["summary"] = {"bot_turn": True, "focus": plan.scenario}
    reply = await respond_step.respond(
        services, ctx, prompts, Route(), "", reply_language, plan=plan
    )
    return TurnPlan(
        reply_text=reply.text or None,
        reply_language=reply_language,
        route={"bot_turn": True},
        reply_meta={"prompt_key": reply.prompt_key, "prompt_version": reply.prompt_version},
        variants=reply.variants,
    )


async def fit_history(services: "Services", ctx: TurnContext, view: ScenarioView) -> None:
    """The last messages the scenario in the focus wants (`history_messages`): fewer — the
    loaded history is cut; more — loaded again with its number."""
    focus = view.focus
    wanted = focus.scenario.definition.history_messages if focus is not None else None
    if wanted is None or wanted == len(ctx.history):
        return
    if wanted < len(ctx.history):
        ctx.history = ctx.history[-wanted:]
    elif len(ctx.history) >= services.settings.history_messages:
        # the default limit cut it: there may be more
        from app.core.turn import load_history

        async with services.sm() as session:
            ctx.history = await load_history(session, ctx.conversation_id, ctx.turn_id, wanted)
    ctx.trace.note("history", {"messages": len(ctx.history), "wanted": wanted})


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
