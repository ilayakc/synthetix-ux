"""Parola hashleme, JWT (access token) ve firsat token (refresh) yardimcilari.

Parolalar argon2id (memory-hard) ile hashlenir; duz metin parola veya token
hicbir zaman loglanmaz ya da saklanmaz. Refresh token'lar JWT degildir:
rastgele, yuksek entropili opak dizgelerdir (`secrets.token_urlsafe`); veritabaninda
yalnizca sha256 hash'leri tutulur, boylece bir DB sizintisi tek basina
oturumlari ele gecirmeye yetmez. Access token ise durum tutmayan (stateless)
kisa omurlu bir JWT'dir; her istekte veritabanina gitmeden dogrulanabilir.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from app.config import settings

_password_hasher = PasswordHasher()

ACCESS_TOKEN_TYPE = "access"


def hash_password(plain_password: str) -> str:
    return _password_hasher.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


def generate_refresh_token() -> str:
    """Yuksek entropili, opak bir refresh token uretir (JWT degildir)."""

    return secrets.token_urlsafe(48)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_access_token(
    *, user_id: uuid.UUID, organization_id: uuid.UUID, role: str, is_demo: bool = False
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "type": ACCESS_TOKEN_TYPE,
        "sub": str(user_id),
        "org": str(organization_id),
        "role": role,
        "demo": is_demo,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


class InvalidAccessTokenError(Exception):
    pass


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise InvalidAccessTokenError(str(exc)) from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidAccessTokenError("Beklenmeyen token turu")
    return payload


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)
