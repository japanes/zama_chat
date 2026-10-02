"""Fernet encryption for secrets stored in the DB (channel tokens, endpoint secrets)."""

import hashlib
import secrets

from cryptography.fernet import Fernet, InvalidToken

from app.errors import AppError


class Crypto:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    def _f(self) -> Fernet:
        if self._fernet is None:
            raise AppError("config.secret_key_missing", status=500)
        return self._fernet

    def encrypt(self, value: str) -> bytes:
        return self._f().encrypt(value.encode())

    def decrypt(self, value: bytes) -> str:
        try:
            return self._f().decrypt(value).decode()
        except InvalidToken as exc:
            raise AppError("config.secret_undecryptable", status=500) from exc


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)
