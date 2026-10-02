"""Errors with a code and params instead of ready text.

The admin builds the message in the user's language from the code, so the backend
does not depend on admin languages: {"code": "scenario.threshold_too_high",
"params": {"threshold": 5, "max": 3}}. Every code must have a translation in
admin/i18n/locales/*.json (checked by `make i18n-check`).
"""

from typing import Any


class AppError(Exception):
    status: int = 400

    def __init__(
        self, code: str, params: dict[str, Any] | None = None, *, status: int | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.params = params or {}
        if status is not None:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "params": self.params}

    def __str__(self) -> str:
        return f"{self.code} {self.params}" if self.params else self.code


class NotFound(AppError):
    status = 404


class Forbidden(AppError):
    status = 403


class Unauthorized(AppError):
    status = 401


class Conflict(AppError):
    status = 409
