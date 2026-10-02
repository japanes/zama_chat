"""LLMService on a fake chat backend: model by role, tenant override, retries,
fallback, cost and the llm_calls log."""

from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.db.models import LlmCall, LlmModel, LlmRole
from app.errors import AppError
from app.llm.openai_client import LLMRequestError, LLMTransportError
from app.llm.service import Overrides
from tests.conftest import chat_reply
from tests.factories import make_model, make_tenant

pytestmark = pytest.mark.db

MSG = [{"role": "user", "content": "hello"}]
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


async def _calls(sm) -> list[LlmCall]:
    async with sm() as session:
        return list((await session.execute(select(LlmCall).order_by(LlmCall.attempt))).scalars())


async def test_model_comes_from_role(services, fake_llm):
    await make_model(services.sm, "openai/a")
    fake_llm.push(chat_reply("hi there", tokens_in=1000, tokens_out=200))
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert result.text == "hi there"
    assert result.model == "openai/a"
    assert result.role == "reply"
    assert result.tokens_in == 1000
    assert result.tokens_out == 200
    assert result.fallback_used is False
    assert result.attempts == 1
    assert fake_llm.models_called() == ["openai/a"]
    assert fake_llm.requests[0]["messages"] == MSG
    assert "response_format" not in fake_llm.requests[0]


async def test_role_timeout_and_reasoning_effort_go_to_request(services, fake_llm):
    await make_model(
        services.sm,
        "openai/thinker",
        supports_reasoning=True,
        reasoning_efforts=["low", "high"],
        reasoning_effort="high",
        timeout_s=12,
    )
    fake_llm.push(chat_reply("ok"))
    await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert fake_llm.requests[0]["reasoning"] == {"effort": "high"}
    assert fake_llm.timeouts == [12.0]


async def test_tenant_assignment_wins_over_shared(services, fake_llm):
    acme = await make_tenant(services.sm, slug="acme")
    other = await make_tenant(services.sm, slug="other")
    await make_model(services.sm, "openai/shared")
    await make_model(services.sm, "openai/own", tenant_id=acme.tenant_id)
    fake_llm.default = chat_reply("ok")
    await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=acme.tenant_id)
    await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=other.tenant_id)
    await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert fake_llm.models_called() == ["openai/own", "openai/shared", "openai/shared"]


async def test_role_without_model_is_a_clear_error(services, fake_llm):
    await make_model(services.sm, "openai/a", roles=(LlmRole.REPLY,))
    with pytest.raises(AppError) as err:
        await services.llm.complete(LlmRole.EXTRACT, MSG, tenant_id=None, schema=SCHEMA)
    assert err.value.code == "llm.role_not_assigned"
    assert err.value.params == {"role": "extract"}
    assert fake_llm.calls == 0


async def test_inactive_model_is_a_clear_error(services, fake_llm):
    await make_model(services.sm, "openai/a")
    async with services.sm() as session, session.begin():
        await session.execute(
            update(LlmModel).where(LlmModel.name == "openai/a").values(is_active=False)
        )
    with pytest.raises(AppError) as err:
        await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert err.value.code == "llm.model_inactive"
    assert fake_llm.calls == 0


async def test_transport_errors_retry_then_fallback(services, fake_llm, sm):
    await make_model(services.sm, "openai/fb", roles=())
    await make_model(services.sm, "openai/main", fallback="openai/fb")
    fake_llm.push(
        LLMTransportError("timeout"),
        LLMTransportError("502: bad gateway", status=502),
        LLMTransportError("429: slow down", status=429),
        chat_reply("from fallback"),
    )
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert result.text == "from fallback"
    assert result.model == "openai/fb"
    assert result.fallback_used is True
    assert result.attempts == 4
    assert fake_llm.models_called() == ["openai/main"] * 3 + ["openai/fb"]

    calls = await _calls(sm)
    assert [c.attempt for c in calls] == [1, 2, 3, 4]
    assert [c.model for c in calls] == ["openai/main"] * 3 + ["openai/fb"]
    assert all("LLMTransportError" in (c.error or "") for c in calls[:3])
    assert calls[3].error is None
    assert calls[3].id == result.call_id


async def test_request_error_goes_straight_to_fallback(services, fake_llm):
    await make_model(services.sm, "openai/fb", roles=())
    await make_model(services.sm, "openai/main", fallback="openai/fb")
    fake_llm.push(LLMRequestError("400: unknown parameter", status=400), chat_reply("ok"))
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert fake_llm.models_called() == ["openai/main", "openai/fb"]
    assert result.fallback_used is True
    assert result.attempts == 2


async def test_all_models_fail_is_unavailable(services, fake_llm, sm):
    await make_model(services.sm, "openai/main")
    fake_llm.push(*(LLMTransportError("timeout") for _ in range(3)))
    with pytest.raises(AppError) as err:
        await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert err.value.code == "llm.unavailable"
    assert err.value.status == 503
    assert err.value.params["role"] == "reply"
    assert len(await _calls(sm)) == 3


async def test_model_without_json_schema_is_skipped_for_schema_calls(services, fake_llm):
    await make_model(services.sm, "openai/fb", roles=())
    await make_model(services.sm, "openai/plain", supports_json_schema=False, fallback="openai/fb")
    fake_llm.push(chat_reply('{"ok": true}'))
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None, schema=SCHEMA)
    assert fake_llm.models_called() == ["openai/fb"]
    assert result.data == {"ok": True}
    assert result.fallback_used is True


async def test_schema_request_and_bad_json_retry(services, fake_llm, sm):
    await make_model(services.sm, "openai/a")
    fake_llm.push(chat_reply("not json at all"), chat_reply("[1, 2]"), chat_reply('{"ok": false}'))
    result = await services.llm.complete(
        LlmRole.REPLY, MSG, tenant_id=None, schema=SCHEMA, schema_name="check"
    )
    assert result.data == {"ok": False}
    assert result.attempts == 3
    fmt = fake_llm.requests[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"] == {"name": "check", "schema": SCHEMA, "strict": True}
    calls = await _calls(sm)
    assert "invalid JSON" in (calls[0].error or "")
    assert "not an object" in (calls[1].error or "")


async def test_empty_text_answer_is_retried(services, fake_llm):
    await make_model(services.sm, "openai/a")
    fake_llm.push(chat_reply("   "), chat_reply("real answer"))
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert result.text == "real answer"
    assert result.attempts == 2


async def test_cost_from_prices(services, fake_llm, sm):
    await make_model(
        services.sm, "openai/a", price_input="0.10", price_output="0.50", price_cached_input="0.05"
    )
    fake_llm.push(chat_reply("ok", tokens_in=1000, cached=400, tokens_out=200))
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    # (600 × 0.10 + 400 × 0.05 + 200 × 0.50) / 1M
    assert result.cost == Decimal("0.00018")
    assert result.tokens_cached == 400
    (call,) = await _calls(sm)
    assert call.cost == Decimal("0.00018")
    assert call.tokens_in == 1000
    assert call.tokens_cached == 400
    assert call.tokens_out == 200


async def test_cost_from_gateway_response_wins(services, fake_llm):
    await make_model(services.sm, "openai/a")
    fake_llm.push(chat_reply("ok", cost=0.0123, gen_id="gen-42"))
    result = await services.llm.complete(LlmRole.REPLY, MSG, tenant_id=None)
    assert result.cost == Decimal("0.0123")
    assert result.generation_id == "gen-42"


async def test_call_log_keeps_prompt_and_turn(services, fake_llm, sm):
    bundle = await make_tenant(services.sm)
    await make_model(services.sm, "openai/a")
    fake_llm.push(chat_reply("ok", gen_id="gen-7"))
    await services.llm.complete(
        LlmRole.REPLY,
        MSG,
        tenant_id=bundle.tenant_id,
        prompt_key="simple_reply",
        prompt_version=3,
        max_tokens=50,
        temperature=0.2,
    )
    (call,) = await _calls(sm)
    assert call.tenant_id == bundle.tenant_id
    assert call.role == "reply"
    assert call.prompt_key == "simple_reply"
    assert call.prompt_version == 3
    assert call.generation_id == "gen-7"
    assert call.request["max_tokens"] == 50
    assert call.request["temperature"] == 0.2
    assert call.response is not None
    assert call.finished_at is not None


async def test_override_model_and_effort(services, fake_llm):
    await make_model(services.sm, "openai/fb", roles=())
    await make_model(services.sm, "openai/a", fallback="openai/fb")
    await make_model(
        services.sm, "openai/b", roles=(), supports_reasoning=True, reasoning_efforts=["low"]
    )
    fake_llm.push(LLMRequestError("400: nope", status=400))
    # an overridden model has no fallback: the sandbox tries exactly that model
    with pytest.raises(AppError) as err:
        await services.llm.complete(
            LlmRole.REPLY, MSG, tenant_id=None, overrides=Overrides(model="openai/b")
        )
    assert err.value.code == "llm.unavailable"
    assert fake_llm.models_called() == ["openai/b"]

    fake_llm.push(chat_reply("ok"))
    result = await services.llm.complete(
        LlmRole.REPLY,
        MSG,
        tenant_id=None,
        overrides=Overrides(model="openai/b", reasoning_effort="low"),
    )
    assert result.model == "openai/b"
    assert fake_llm.requests[-1]["reasoning"] == {"effort": "low"}


async def test_effort_is_not_sent_to_model_without_reasoning(services, fake_llm):
    await make_model(services.sm, "openai/a")
    fake_llm.push(chat_reply("ok"))
    await services.llm.complete(
        LlmRole.REPLY, MSG, tenant_id=None, overrides=Overrides(reasoning_effort="high")
    )
    assert "reasoning" not in fake_llm.requests[0]


async def test_unknown_override_model(services, fake_llm):
    await make_model(services.sm, "openai/a")
    with pytest.raises(AppError) as err:
        await services.llm.complete(
            LlmRole.REPLY, MSG, tenant_id=None, overrides=Overrides(model="openai/nope")
        )
    assert err.value.code == "models.not_found"


async def test_embed_uses_embed_role(services, fake_llm):
    await make_model(
        services.sm,
        "openai/text-embedding-3-large",
        roles=(LlmRole.EMBED,),
        kind="embedding",
        price_input="0.13",
        price_output="0",
    )
    vectors, result = await services.llm.embed(["one two", "three"], tenant_id=None)
    assert len(vectors) == 2
    assert len(vectors[0]) == fake_llm.embed_dim
    assert fake_llm.embed_calls == [("openai/text-embedding-3-large", ["one two", "three"])]
    assert result.tokens_in == 3
    assert result.cost == Decimal("0.13") * 3 / Decimal(1_000_000)
