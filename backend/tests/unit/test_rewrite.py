import json
from datetime import timedelta
from types import SimpleNamespace

from app.core.context import HistoryItem
from app.core.rewrite import (
    SCHEMA,
    answer_language_line,
    as_written,
    build_messages,
    dialogue_lines,
    rewrite,
    skips_rewrite,
)
from app.core.tenant_settings import TenantSettings
from app.db.models import LlmRole, MessageRole
from app.prompts.store import PromptText
from tests.turn_ctx import T0, make_ctx

PROMPT = PromptText("rewrite", 3, json.dumps({"rules": {"common": ["Keep it short"]}}))


def history(*items: tuple[str, str]) -> list[HistoryItem]:
    return [
        HistoryItem(role, text, T0 + timedelta(minutes=i)) for i, (role, text) in enumerate(items)
    ]


def test_dialogue_lines():
    ctx = make_ctx(
        input_text="а на завтра?",
        history=history(
            (MessageRole.CLIENT, "привіт"),
            (MessageRole.SYSTEM, "conversation reset"),
            (MessageRole.BOT, "Вітаю! Чим допомогти?"),
            (MessageRole.OPERATOR, "Є вікно о 10:00"),
        ),
    )
    assert dialogue_lines(ctx) == [
        "client: привіт",
        "bot: Вітаю! Чим допомогти?",
        "manager: Є вікно о 10:00",
        "client: а на завтра?",
    ]


def test_answer_language_line():
    assert answer_language_line("uk") == "Give the answer in Ukrainian language"
    assert answer_language_line("en") == "Give the answer in English language"


def test_build_messages_fills_the_prompt_fields():
    ctx = make_ctx(input_text="скільки коштує?", history=history((MessageRole.BOT, "Вітаю!")))
    messages = build_messages(ctx, PROMPT, "uk")
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    data = json.loads(messages[0]["content"])
    assert data["rules"] == {"common": ["Keep it short"]}
    assert data["dialogue"] == ["bot: Вітаю!", "client: скільки коштує?"]
    assert data["selected_line"] == "скільки коштує?"
    assert data["answer_language"] == "Give the answer in Ukrainian language"
    # non-ASCII is sent as is, not as \u escapes
    assert "скільки" in messages[0]["content"]


class RecordingLLM:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def complete(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        return SimpleNamespace(data=self.data, call_id="call-1", model="openai/a")


async def test_rewrite_calls_the_rewrite_role_with_the_schema():
    llm = RecordingLLM({"question": [" How much is a manicure? ", ""], "language": "uk"})
    ctx = make_ctx()
    result = await rewrite(SimpleNamespace(llm=llm), ctx, PROMPT, "uk")

    role, _, kw = llm.calls[0]
    assert role == LlmRole.REWRITE
    assert kw["schema"] == SCHEMA
    assert kw["schema_name"] == "rewrite"
    assert kw["prompt_key"] == "rewrite"
    assert kw["prompt_version"] == 3
    assert kw["tenant_id"] == ctx.tenant_id
    assert kw["turn_id"] == ctx.turn_id

    assert result.questions == ["How much is a manicure?"]
    assert result.text == "How much is a manicure?"
    assert result.language == "uk"
    assert result.llm_call_id == "call-1"
    assert result.model == "openai/a"


async def test_several_questions_are_joined_by_lines():
    llm = RecordingLLM({"question": ["Price of a manicure?", "Open on Sunday?"], "language": "en"})
    result = await rewrite(SimpleNamespace(llm=llm), make_ctx(), PROMPT, "en")
    assert result.text == "Price of a manicure?\nOpen on Sunday?"


async def test_nothing_usable_falls_back_to_the_client_text():
    llm = RecordingLLM({"question": [], "language": 5})
    ctx = make_ctx(input_text="  скільки коштує?  ")
    result = await rewrite(SimpleNamespace(llm=llm), ctx, PROMPT, "uk")
    assert result.questions == ["скільки коштує?"]
    assert result.language is None


async def test_no_data_at_all_falls_back_too():
    llm = RecordingLLM(None)
    result = await rewrite(SimpleNamespace(llm=llm), make_ctx(input_text="hi"), PROMPT, "en")
    assert result.text == "hi"


def test_a_long_turn_input_skips_the_rewrite():
    settings = TenantSettings(languages=["uk"], rewrite_skip_chars=10)
    assert skips_rewrite(make_ctx(input_text="x" * 10, settings=settings))
    assert not skips_rewrite(make_ctx(input_text="x" * 9, settings=settings))
    # the spaces around do not make a «да» long
    assert not skips_rewrite(make_ctx(input_text="   так   " + " " * 20, settings=settings))
    # 0 — every message is rewritten
    off = TenantSettings(languages=["uk"], rewrite_skip_chars=0)
    assert not skips_rewrite(make_ctx(input_text="x" * 5000, settings=off))
    # the default: 500 characters
    assert skips_rewrite(make_ctx(input_text="x" * 500))
    assert not skips_rewrite(make_ctx(input_text="x" * 499))


def test_as_written_keeps_the_client_text_and_no_language():
    rw = as_written(make_ctx(input_text="  Мене звати Олена.\nМи робимо торти.  "))
    assert rw.text == "Мене звати Олена.\nМи робимо торти."
    assert rw.questions == [rw.text]
    assert rw.language is None
    assert rw.llm_call_id is None
