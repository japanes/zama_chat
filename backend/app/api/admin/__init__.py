"""Admin API `/v1/admin/*`: signed in with the session cookie (see app/admin/auth.py)."""

from app.api.admin import (
    activity,
    auth,
    content,
    instagram,
    models,
    notifiers,
    operator,
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
]
