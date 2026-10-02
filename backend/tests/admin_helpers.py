"""Admin users with a ready session: `headers` go straight into the test client."""

import uuid

from app.admin.auth import SESSION_COOKIE, create_session
from app.admin.tenants import ensure_user, set_membership
from app.db.models import UserRole


async def admin_headers(
    services,
    email: str = "admin@example.com",
    *,
    superadmin: bool = False,
    tenant_id: uuid.UUID | None = None,
    role: str = UserRole.ADMIN,
) -> dict[str, str]:
    """Create (or reuse) the user, add the membership, open a session → Cookie header."""
    async with services.sm() as session, session.begin():
        user, _ = await ensure_user(session, email.lower())
        user.is_superadmin = superadmin
        if tenant_id is not None:
            await set_membership(session, user_id=user.id, tenant_id=tenant_id, role=UserRole(role))
        raw = await create_session(
            session, user.id, services.clock.now(), services.settings.session_days
        )
    return {"Cookie": f"{SESSION_COOKIE}={raw}"}


async def make_user(services, email: str, *, active: bool = True) -> uuid.UUID:
    async with services.sm() as session, session.begin():
        user, _ = await ensure_user(session, email.lower())
        user.is_active = active
        await session.flush()
        return user.id
