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
from app.db.models.decisions import Decision, DecisionProvider, DecisionReason, DecisionType
from app.db.models.jobs import Job, JobStatus
from app.db.models.llm import ROLES_NEED_JSON_SCHEMA, LlmCall, LlmModel, LlmRole, ModelRole
from app.db.models.operator import (
    NOTIFY_EVENTS,
    Notifier,
    NotifierKind,
    OperatorTask,
    TaskKind,
    TaskStatus,
)
from app.db.models.prompts import Prompt
from app.db.models.results import Delivery, DeliveryStatus, DevSinkItem, Endpoint
from app.db.models.scenarios import (
    OPEN_STATUSES,
    ChannelScenario,
    RunStatus,
    Scenario,
    ScenarioRun,
)
from app.db.models.search import CatalogItem, IndexStatus, KnowledgeChunk, KnowledgeSource
from app.db.models.tenants import ApiKey, Channel, ChannelKind, ChannelStatus, Tenant
from app.db.models.users import (
    ROLE_RANK,
    AdminSession,
    AuditLog,
    LoginToken,
    Membership,
    User,
    UserRole,
)

__all__ = [
    "NOTIFY_EVENTS",
    "OPEN_STATUSES",
    "ROLES_NEED_JSON_SCHEMA",
    "ROLE_RANK",
    "AdminSession",
    "ApiKey",
    "AuditLog",
    "CatalogItem",
    "Channel",
    "ChannelKind",
    "ChannelScenario",
    "ChannelStatus",
    "Contact",
    "Conversation",
    "Decision",
    "DecisionProvider",
    "DecisionReason",
    "DecisionType",
    "Delivery",
    "DeliveryStatus",
    "DevSinkItem",
    "Endpoint",
    "IndexStatus",
    "Job",
    "JobStatus",
    "KnowledgeChunk",
    "KnowledgeSource",
    "LlmCall",
    "LlmModel",
    "LlmRole",
    "LoginToken",
    "Membership",
    "Message",
    "MessageRole",
    "ModelRole",
    "Notifier",
    "NotifierKind",
    "OperatorTask",
    "Prompt",
    "RunStatus",
    "Scenario",
    "ScenarioRun",
    "SendStatus",
    "TaskKind",
    "TaskStatus",
    "Tenant",
    "Turn",
    "TurnStatus",
    "User",
    "UserRole",
]
