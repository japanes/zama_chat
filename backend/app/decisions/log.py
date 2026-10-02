"""Write decision rows. Own short transaction, like llm_calls: the log stays even when
the turn fails afterwards, and a log failure never fails the turn."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Decision
from app.logging import get_logger

log = get_logger(__name__)


async def save_decisions(sm: async_sessionmaker[AsyncSession], rows: list[Decision]) -> None:
    if not rows:
        return
    try:
        async with sm() as session, session.begin():
            session.add_all(rows)
    except Exception:
        log.exception("decisions.record_failed", count=len(rows))
