"""Admin API `/v1/admin/*`: signed in with the session cookie (see app/admin/auth.py)."""

from app.api.admin import (
    activity,
    auth,
    content,
    decisions,
    instagram,
    models,
    notifiers,
    operator,
    search,
    tenants,
)

routers = [
    auth.router,
    tenants.router,
    content.router,
    activity.router,
    models.router,
    instagram.router,
    operator.router,
    notifiers.router,
    search.router,
    decisions.router,
]
