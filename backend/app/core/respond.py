"""Step 5 — respond: the reply text from the reply LLM role.

Two kinds of answers (stage 1):
- normal: legacy `system_prompt` (%COMPANY_TITLE%…) + history + legacy `user_prompt` JSON
  with `user_question` = the rewritten text, `answer_language`, `client_description` =
  the tenant's business description, `context` = what the search found (stage 5,
  search/step.py; empty → the legacy "I don't know" rules) + `search_rules`;
- service (greeting / goodbye / "are you a bot" / ethical violation): the prompt
  `service_<type>` (legacy scripts 2–5) as the system message + history + the client's text.

From stage 2 a RespondPlan (scenarios/plan.py) adds `scenario_step` to the user prompt:
answer the client's own question first (if any), then the next scenario question /
the clarification / "everything is collected". The rules for it are the prompt
`scenario_step` (prompts/builtin.py). A bare greeting in the middle of a scenario goes
the normal way, so the bot greets back and asks the next question.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.context import TurnContext
from app.core.language import language_name
from app.core.rewrite import answer_language_line
from app.core.routing import SERVICE_TYPES, Route
from app.db.models import LlmRole, MessageRole
from app.logging import get_logger
from app.operator import variants as op_variants
from app.prompts.builtin import (
    SCENARIO_STEP,
    SCENARIO_STEP_KEY,
    SEARCH_CONTEXT,
    SEARCH_CONTEXT_KEY,
    prompt_rules,
)
from app.prompts.render import parse_json_prompt, render_json, render_text, to_text
from app.prompts.store import PromptText

if TYPE_CHECKING:
    from app.scenarios.plan import RespondPlan
    from app.search.step import SearchOutcome
    from app.services import Services

log = get_logger(__name__)

SYSTEM_KEY = "system_prompt"
USER_KEY = "user_prompt"
SERVICE_KEYS = {t: f"service_{t}" for t in SERVICE_TYPES if t != "none"}
PROMPT_KEYS = (
    SYSTEM_KEY,
    USER_KEY,
    *SERVICE_KEYS.values(),
    SCENARIO_STEP_KEY,
    SEARCH_CONTEXT_KEY,
)
# service types answered the normal way when a scenario step is due
# TODO(review): goodbye / "are you a bot" in the middle of a scenario use their prompts
SCENARIO_OVER_SERVICE = ("greeting_only",)

# TODO(review): wording of the note added when the client asks for a human. After this
# reply the bot goes quiet in the conversation and an operator gets a handover task
# (core/turn.py, operator/tasks.py).
HANDOVER_RULE = (
    "The client asked to talk to a real person. Say briefly that a manager will reply in "
    "this chat soon. If the message also has a question you can answer, answer it too"
)


@dataclass(frozen=True)
class Reply:
    text: str
    prompt_key: str
    prompt_version: int
    llm_call_id: str
    model: str
    # operator mode: the variants for the operator (text is empty then)
    variants: list[str] | None = None


def prompt_values(ctx: TurnContext, reply_language: str, question: str = "") -> dict[str, str]:
    """%PLACEHOLDER% values shared by the legacy prompts."""
    s = ctx.settings
    return {
        "COMPANY_TITLE": s.company_title or ctx.tenant_title,
        "BOT_NAME": s.bot_name or "",
        "LANGUAGE": language_name(reply_language),
        "QUESTION": question,
    }


def history_messages(ctx: TurnContext) -> list[dict[str, Any]]:
    """Earlier messages as chat turns: client → user, bot / operator → assistant."""
    out: list[dict[str, Any]] = []
    for item in ctx.history:
        if item.role == MessageRole.SYSTEM:
            continue
        role = "user" if item.role == MessageRole.CLIENT else "assistant"
        out.append({"role": role, "content": item.text})
    return out


def answer_messages(
    ctx: TurnContext,
    system: PromptText,
    user: PromptText,
    rewritten: str,
    reply_language: str,
    *,
    wants_human: bool = False,
    context: list[Any] | None = None,
    search_rules: list[str] | None = None,
    scenario_step: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    values = prompt_values(ctx, reply_language, rewritten)
    data = render_json(
        user.body,
        {
            "user_question": rewritten,
            "answer_language": answer_language_line(reply_language),
            "client_description": ctx.settings.business_description,
            "context": context or [],
        },
        values,
        key=user.key,
    )
    if search_rules:
        data["search_rules"] = search_rules
    if wants_human:
        data["handover_note"] = HANDOVER_RULE
    if scenario_step is not None:
        data["scenario_step"] = scenario_step
    return [
        {"role": "system", "content": render_text(system.body, values)},
        *history_messages(ctx),
        {"role": "user", "content": to_text(data)},
    ]


def service_messages(
    ctx: TurnContext, prompt: PromptText, rewritten: str, reply_language: str
) -> list[dict[str, Any]]:
    values = prompt_values(ctx, reply_language, rewritten)
    return [
        {"role": "system", "content": render_text(prompt.body, values)},
        *history_messages(ctx),
        {"role": "user", "content": ctx.input_text},
    ]


def scenario_rules(prompts: dict[str, PromptText]) -> list[str]:
    prompt = prompts.get(SCENARIO_STEP_KEY)
    if prompt is not None:
        rules = parse_json_prompt(prompt.body, prompt.key).get("rules")
        if isinstance(rules, list) and rules:
            return [str(r) for r in rules]
        log.warning("respond.scenario_rules_missing", version=prompt.version)
    return list(SCENARIO_STEP["rules"])


def pick_prompt(
    route: Route, prompts: dict[str, PromptText], plan: "RespondPlan | None" = None
) -> tuple[PromptText, bool]:  # (prompt, is_service)
    if route.is_service and not (
        plan is not None and plan.asks and route.service_type in SCENARIO_OVER_SERVICE
    ):
        key = SERVICE_KEYS.get(route.service_type)
        prompt = prompts.get(key) if key else None
        if prompt is not None:
            return prompt, True
        # the tenant has no prompt for this service type: answer as usual
        log.warning("respond.service_prompt_missing", service_type=route.service_type)
    return prompts[SYSTEM_KEY], False


async def respond(
    services: "Services",
    ctx: TurnContext,
    prompts: dict[str, PromptText],
    route: Route,
    rewritten: str,
    reply_language: str,
    *,
    plan: "RespondPlan | None" = None,
    search: "SearchOutcome | None" = None,
    handover: bool | None = None,
) -> Reply:
    """`search` — what the search step found (stage 5): its context goes into the user
    prompt. `handover` — the bot hands the conversation over (the client asked, or the
    answer was not found and the tenant wants a human then); default route.wants_human."""
    prompt, is_service = pick_prompt(route, prompts, plan)
    if search is not None and search.context and is_service:
        # "hi, how much is the manicure?" may be classed as a greeting: found
        # information wins over the service prompt
        prompt, is_service = prompts[SYSTEM_KEY], False
    wants_human = route.wants_human if handover is None else handover
    step_data = None
    if is_service:
        messages = service_messages(ctx, prompt, rewritten, reply_language)
    else:
        if plan is not None:
            step_data = plan.to_prompt(scenario_rules(prompts))
        messages = answer_messages(
            ctx,
            prompts[SYSTEM_KEY],
            prompts[USER_KEY],
            rewritten,
            reply_language,
            wants_human=wants_human,
            context=search.context if search is not None else None,
            search_rules=(
                prompt_rules(prompts, SEARCH_CONTEXT_KEY, SEARCH_CONTEXT)
                if search is not None and search.searched
                else None
            ),
            scenario_step=step_data,
        )
    # the user prompt carries the turn-specific part; its version is the one to log
    logged = prompt if is_service else prompts[USER_KEY]
    info: dict[str, Any] = {}
    if step_data is not None:
        rules = prompts.get(SCENARIO_STEP_KEY)
        info["scenario_step_version"] = rules.version if rules is not None else None
    info["service"] = is_service
    if search is not None and search.searched:
        info["context"] = len(search.context)
    if ctx.operator_mode and not ctx.sandbox:
        return await _variants(services, ctx, messages, logged, info)
    with ctx.trace.step(
        "respond",
        prompt_key=logged.key,
        prompt_version=logged.version,
        **info,
    ) as step:
        result = await services.llm.complete(
            LlmRole.REPLY,
            messages,
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            prompt_key=logged.key,
            prompt_version=logged.version,
            overrides=ctx.overrides,
        )
        step["llm_call_id"] = str(result.call_id)
        step["model"] = result.model
    return Reply(
        text=result.text.strip(),
        prompt_key=logged.key,
        prompt_version=logged.version,
        llm_call_id=str(result.call_id),
        model=result.model,
    )


async def _variants(
    services: "Services",
    ctx: TurnContext,
    messages: list[dict[str, Any]],
    logged: PromptText,
    info: dict[str, Any],
) -> Reply:
    """Operator mode: one call for several variants of the reply."""
    n = ctx.operator_variants
    config = await services.llm.role_config(LlmRole.REPLY, ctx.tenant_id, ctx.overrides)
    with_schema = config.model.supports_json_schema
    messages = [
        *messages,
        {"role": "system", "content": op_variants.instruction(n, with_schema)},
    ]
    with ctx.trace.step(
        "respond",
        prompt_key=logged.key,
        prompt_version=logged.version,
        operator_variants=n,
        **info,
    ) as step:
        result = await services.llm.complete(
            LlmRole.REPLY,
            messages,
            tenant_id=ctx.tenant_id,
            turn_id=ctx.turn_id,
            schema=op_variants.schema(n) if with_schema else None,
            schema_name="variants",
            prompt_key=logged.key,
            prompt_version=logged.version,
            overrides=ctx.overrides,
        )
        variants = op_variants.parse(result.text, result.data, n)
        step["llm_call_id"] = str(result.call_id)
        step["model"] = result.model
        step["variants"] = len(variants)
    # no reply to the client: the variants wait for an operator
    return Reply(
        text="",
        prompt_key=logged.key,
        prompt_version=logged.version,
        llm_call_id=str(result.call_id),
        model=result.model,
        variants=variants,
    )
