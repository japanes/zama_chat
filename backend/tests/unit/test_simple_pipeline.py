"""Stage 0 pipeline prompt: system text from tenant settings, history, the new input."""

import uuid
from datetime import UTC, datetime

from app.core.context import HistoryItem, TurnContext
from app.core.simple_pipeline import build_messages
from app.core.tenant_settings import TenantSettings
from app.db.models import ChannelKind, MessageRole

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _ctx(**kw) -> TurnContext:
    data = {
        "turn_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "channel_id": uuid.uuid4(),
        "channel_kind": ChannelKind.HTTP,
        "contact_id": uuid.uuid4(),
        "contact_name": "Olha",
        "settings": TenantSettings(languages=["uk", "en"]),
        "tenant_title": "Acme",
        "pending_ids": [uuid.uuid4()],
        "input_text": "скільки коштує?",
        "history": [],
        "client_language": None,
        "reply_language": None,
        "state": {},
        "state_version": 0,
        "handover": False,
        "operator_mode": False,
        "now": T0,
    }
    data.update(kw)
    return TurnContext(**data)


def test_system_prompt_uses_tenant_title_and_primary_language():
    messages = build_messages(_ctx())
    system = messages[0]
    assert system["role"] == "system"
    assert "the assistant of Acme" in system["content"]
    assert "'uk'" in system["content"]
    assert messages[-1] == {"role": "user", "content": "скільки коштує?"}
    assert len(messages) == 2


def test_settings_win_over_tenant_title():
    settings = TenantSettings(
        company_title="Zama Salon", bot_name="Zama", business_description="Nail salon in Kyiv"
    )
    system = build_messages(_ctx(settings=settings, reply_language="en"))[0]["content"]
    assert "You are Zama of Zama Salon." in system
    assert "Nail salon in Kyiv" in system
    assert "'en'" in system


def test_history_roles_and_system_messages_are_skipped():
    history = [
        HistoryItem(MessageRole.CLIENT, "hi", T0),
        HistoryItem(MessageRole.BOT, "hello", T0),
        HistoryItem(MessageRole.SYSTEM, "internal note", T0),
        HistoryItem(MessageRole.OPERATOR, "operator here", T0),
    ]
    messages = build_messages(_ctx(history=history))
    assert messages[1:] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "assistant", "content": "operator here"},
        {"role": "user", "content": "скільки коштує?"},
    ]
