import json
from datetime import timedelta
from types import SimpleNamespace

from app.core import respond as respond_module
from app.core.context import HistoryItem
from app.core.respond import (
    HANDOVER_RULE,
    SERVICE_KEYS,
    SYSTEM_KEY,
    USER_KEY,
    answer_messages,
    history_messages,
    pick_prompt,
    prompt_values,
    respond,
    service_messages,
)
from app.core.routing import Route
from app.core.tenant_settings import TenantSettings
from app.db.models import LlmRole, MessageRole
from app.prompts.store import PromptText
from tests.turn_ctx import T0, make_ctx

SYSTEM = PromptText(SYSTEM_KEY, 1, "You work for %COMPANY_TITLE%. Answer in %LANGUAGE%.")
USER = PromptText(
    USER_KEY,
    4,
    json.dumps({"user_question": "", "rules": ["Speak for %COMPANY_TITLE%"], "context": None}),
)
GREETING = PromptText(SERVICE_KEYS["greeting_only"], 2, "Greet the client of %COMPANY_TITLE%.")
PROMPTS = {SYSTEM_KEY: SYSTEM, USER_KEY: USER, GREETING.key: GREETING}


def history(*items: tuple[str, str]) -> list[HistoryItem]:
    return [
        HistoryItem(role, text, T0 + timedelta(minutes=i)) for i, (role, text) in enumerate(items)
    ]


class RecordingLog:
    def __init__(self):
        self.events = []

    def warning(self, event, **kw):
        self.events.append((event, kw))


def test_service_types_have_prompt_keys():
    assert SERVICE_KEYS == {
        "greeting_only": "service_greeting_only",
        "goodbye_only": "service_goodbye_only",
        "not_a_bot": "service_not_a_bot",
        "ethical_violation": "service_ethical_violation",
    }


def test_prompt_values_fall_back_to_the_tenant_title():
    ctx = make_ctx(tenant_title="Acme")
    assert prompt_values(ctx, "uk", "q") == {
        "COMPANY_TITLE": "Acme",
        "BOT_NAME": "",
        "LANGUAGE": "Ukrainian",
        "QUESTION": "q",
    }
    ctx = make_ctx(settings=TenantSettings(company_title="Zama Salon", bot_name="Olya"))
    values = prompt_values(ctx, "en")
    assert values["COMPANY_TITLE"] == "Zama Salon"
    assert values["BOT_NAME"] == "Olya"


def test_history_roles():
    ctx = make_ctx(
        history=history(
            (MessageRole.CLIENT, "hi"),
            (MessageRole.SYSTEM, "reset"),
            (MessageRole.BOT, "hello"),
            (MessageRole.OPERATOR, "I am a manager"),
        )
    )
    assert history_messages(ctx) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "assistant", "content": "I am a manager"},
    ]


def test_answer_messages():
    ctx = make_ctx(
        settings=TenantSettings(languages=["uk", "en"], business_description="Nail salon"),
        history=history((MessageRole.CLIENT, "привіт"), (MessageRole.BOT, "Вітаю!")),
        input_text="скільки?",
    )
    messages = answer_messages(ctx, SYSTEM, USER, "How much is a manicure?", "uk")

    assert messages[0] == {"role": "system", "content": "You work for Acme. Answer in Ukrainian."}
    assert messages[1:3] == [
        {"role": "user", "content": "привіт"},
        {"role": "assistant", "content": "Вітаю!"},
    ]
    assert messages[-1]["role"] == "user"
    data = json.loads(messages[-1]["content"])
    assert data["user_question"] == "How much is a manicure?"
    assert data["answer_language"] == "Give the answer in Ukrainian language"
    assert data["client_description"] == "Nail salon"
    assert data["context"] == []
    assert data["rules"] == ["Speak for Acme"]
    assert "handover_note" not in data
    # the original text is not sent: the model answers the rewritten question
    assert "скільки?" not in messages[-1]["content"]


def test_answer_messages_with_handover_note():
    messages = answer_messages(make_ctx(), SYSTEM, USER, "Call a manager", "en", wants_human=True)
    assert json.loads(messages[-1]["content"])["handover_note"] == HANDOVER_RULE


def test_service_messages_use_the_client_text():
    ctx = make_ctx(input_text="Привіт!", history=history((MessageRole.BOT, "Вітаю!")))
    messages = service_messages(ctx, GREETING, "Hello", "uk")
    assert messages == [
        {"role": "system", "content": "Greet the client of Acme."},
        {"role": "assistant", "content": "Вітаю!"},
        {"role": "user", "content": "Привіт!"},
    ]


def test_pick_prompt_for_a_service_type():
    assert pick_prompt(Route(service_type="greeting_only"), PROMPTS) == (GREETING, True)


def test_pick_prompt_for_a_normal_message():
    assert pick_prompt(Route(), PROMPTS) == (SYSTEM, False)


def test_missing_service_prompt_answers_as_usual(monkeypatch):
    recorder = RecordingLog()
    monkeypatch.setattr(respond_module, "log", recorder)
    assert pick_prompt(Route(service_type="goodbye_only"), PROMPTS) == (SYSTEM, False)
    assert recorder.events == [("respond.service_prompt_missing", {"service_type": "goodbye_only"})]


class RecordingLLM:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def complete(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        return SimpleNamespace(text=self.text, call_id="call-9", model="openai/a")


async def test_respond_normal_reply_logs_the_user_prompt():
    llm = RecordingLLM("  Манікюр коштує 500 грн.  ")
    ctx = make_ctx()
    reply = await respond(SimpleNamespace(llm=llm), ctx, PROMPTS, Route(), "Price?", "uk")

    role, messages, kw = llm.calls[0]
    assert role == LlmRole.REPLY
    assert messages[0]["role"] == "system"
    assert kw["prompt_key"] == USER_KEY
    assert kw["prompt_version"] == 4
    assert reply.text == "Манікюр коштує 500 грн."
    assert (reply.prompt_key, reply.prompt_version) == (USER_KEY, 4)
    assert reply.llm_call_id == "call-9"

    step = ctx.trace.steps[-1]
    assert step["step"] == "respond"
    assert step["prompt_key"] == USER_KEY
    assert step["service"] is False
    assert step["llm_call_id"] == "call-9"
    assert step["model"] == "openai/a"


async def test_respond_service_reply_logs_the_service_prompt():
    llm = RecordingLLM("Вітаю!")
    ctx = make_ctx(input_text="Привіт")
    route = Route(service_type="greeting_only")
    reply = await respond(SimpleNamespace(llm=llm), ctx, PROMPTS, route, "Hello", "uk")

    _, messages, kw = llm.calls[0]
    assert messages[-1] == {"role": "user", "content": "Привіт"}
    assert kw["prompt_key"] == GREETING.key
    assert (reply.prompt_key, reply.prompt_version) == (GREETING.key, 2)
    assert ctx.trace.steps[-1]["service"] is True
