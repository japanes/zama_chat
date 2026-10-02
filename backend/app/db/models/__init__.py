"""All ORM models — imported here so Alembic sees the whole metadata."""

from app.db.models.conversations import (
    Contact,
    Conversation,
    Message,
    MessageRole,
    SendStatus,
    Turn,
    TurnStatus,
)
from app.db.models.jobs import Job, JobStatus
from app.db.models.llm import ROLES_NEED_JSON_SCHEMA, LlmCall, LlmModel, LlmRole, ModelRole
from app.db.models.tenants import ApiKey, Channel, ChannelKind, ChannelStatus, Tenant

__all__ = [
    "ROLES_NEED_JSON_SCHEMA",
    "ApiKey",
    "Channel",
    "ChannelKind",
    "ChannelStatus",
    "Contact",
    "Conversation",
    "Job",
    "JobStatus",
    "LlmCall",
    "LlmModel",
    "LlmRole",
    "Message",
    "MessageRole",
    "ModelRole",
    "SendStatus",
    "Tenant",
    "Turn",
    "TurnStatus",
]
