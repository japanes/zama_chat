"""Meta's messaging window: a business may write to a client only within 24 hours of
the client's last message. With the HUMAN_AGENT tag (a separate Meta permission) a
person may answer up to 7 days after it; the bot never may.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from app.db.models import MessageRole

WINDOW = timedelta(hours=24)
HUMAN_AGENT_WINDOW = timedelta(days=7)


class SendMode(StrEnum):
    STANDARD = "standard"
    HUMAN_AGENT = "human_agent"


def send_mode(
    role: str, last_client_at: datetime | None, now: datetime, *, human_agent_tag: bool
) -> SendMode | None:
    """How a message of `role` may go out now; None — it may not."""
    if last_client_at is None:
        # the client never wrote: Instagram does not let a business start a dialog
        return None
    age = now - last_client_at
    if age <= WINDOW:
        return SendMode.STANDARD
    if human_agent_tag and role == MessageRole.OPERATOR and age <= HUMAN_AGENT_WINDOW:
        return SendMode.HUMAN_AGENT
    return None


def window_closes_at(last_client_at: datetime | None) -> datetime | None:
    return last_client_at + WINDOW if last_client_at is not None else None
