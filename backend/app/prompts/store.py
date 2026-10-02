"""Active prompt versions. A tenant's own active version wins over the shared one
(tenant_id NULL). New versions are appended; the previous one is deactivated."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Prompt
from app.errors import AppError


@dataclass(frozen=True)
class PromptText:
    key: str
    version: int
    body: str
    tenant_id: uuid.UUID | None = None


async def active_prompts(
    session: AsyncSession, tenant_id: uuid.UUID | None, keys: Iterable[str]
) -> dict[str, PromptText]:
    """{key: active version}; keys without any active version are missing."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return {}
    owner = Prompt.tenant_id.is_(None)
    if tenant_id is not None:
        owner = or_(owner, Prompt.tenant_id == tenant_id)
    rows = (
        await session.execute(select(Prompt).where(Prompt.key.in_(keys), Prompt.is_active, owner))
    ).scalars()
    found: dict[str, PromptText] = {}
    for row in rows:
        current = found.get(row.key)
        # the tenant's own version wins over the shared one
        if current is None or (current.tenant_id is None and row.tenant_id is not None):
            found[row.key] = PromptText(row.key, row.version, row.body, row.tenant_id)
    return found


async def load_prompts(
    sm: async_sessionmaker[AsyncSession],
    tenant_id: uuid.UUID | None,
    keys: Iterable[str],
    *,
    required: Iterable[str] = (),
) -> dict[str, PromptText]:
    """One short read outside the turn transaction. A missing required prompt is a clear
    error: the shared prompts are loaded with `zama seed-prompts`."""
    keys = list(keys)
    async with sm() as session:
        found = await active_prompts(session, tenant_id, keys)
    for key in required:
        if key not in found:
            raise AppError("prompt.not_found", {"key": key}, status=500)
    return found


async def save_version(
    session: AsyncSession,
    *,
    key: str,
    body: str,
    tenant_id: uuid.UUID | None = None,
    author: str | None = None,
    note: str | None = None,
    activate: bool = True,
) -> Prompt:
    """Append a new version (max + 1). With `activate` the previous active one is switched
    off in the same transaction."""
    owner = Prompt.tenant_id.is_(None) if tenant_id is None else Prompt.tenant_id == tenant_id
    last = (
        await session.execute(select(func.max(Prompt.version)).where(Prompt.key == key, owner))
    ).scalar_one()
    if activate:
        await session.execute(
            update(Prompt).where(Prompt.key == key, owner, Prompt.is_active).values(is_active=False)
        )
    prompt = Prompt(
        tenant_id=tenant_id,
        key=key,
        version=(last or 0) + 1,
        body=body,
        is_active=activate,
        author=author,
        note=note,
    )
    session.add(prompt)
    await session.flush()
    return prompt


async def activate_version(
    session: AsyncSession, *, key: str, version: int, tenant_id: uuid.UUID | None = None
) -> Prompt:
    """Roll back / forward to an existing version."""
    owner = Prompt.tenant_id.is_(None) if tenant_id is None else Prompt.tenant_id == tenant_id
    prompt = (
        await session.execute(
            select(Prompt).where(Prompt.key == key, owner, Prompt.version == version)
        )
    ).scalar_one_or_none()
    if prompt is None:
        raise AppError("prompt.version_not_found", {"key": key, "version": version}, status=404)
    await session.execute(
        update(Prompt).where(Prompt.key == key, owner, Prompt.is_active).values(is_active=False)
    )
    prompt.is_active = True
    await session.flush()
    return prompt
