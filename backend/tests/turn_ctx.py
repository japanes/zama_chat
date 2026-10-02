"""TurnContext for unit tests: every field has a sane default, override what matters."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.context import TurnContext
from app.core.tenant_settings import TenantSettings
from app.db.models import ChannelKind
from app.decisions.policy import DecisionPolicy

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def make_ctx(**kw: Any) -> TurnContext:
    data: dict[str, Any] = {
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
        # no DB in unit tests: the default rules, not the decision_rules table
        "policy": DecisionPolicy(),
    }
    data.update(kw)
    return TurnContext(**data)
