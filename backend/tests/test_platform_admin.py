"""`app.dependencies.require_platform_admin` icin uctan uca yetkilendirme testleri.

`require_platform_admin` uzerinde HERHANGI bir uretim endpoint'i (bu asamada
kasitli olarak yok - bkz. gorev kapsami) OLMADIGI icin, gercek cookie/JWT/DB
zincirini (401/403/200 ayrimini) dogrulamak amaciyla YALNIZCA bu test
modulunde, uretim `app.main.app`'ten TAMAMEN BAGIMSIZ, minik bir test-only
FastAPI uygulamasi kurulur (bkz. `_build_admin_only_app`). Bu uygulama hicbir
zaman gercek uygulamaya dahil edilmez.

Kullaniciyi yonetici yapmak/cikarmak icin (henuz bir admin API'si olmadigindan)
`tests.conftest.TEST_DATABASE_URL`'e dogrudan, kisa omurlu bir baglantiyla
UPDATE atilir - `app.admin_cli`'nin ayni sekilde yaptigi guncellemenin bir
benzeri, ama HTTP/DI katmanindan tamamen bagimsiz.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.cookies import ACCESS_TOKEN_COOKIE
from app.dependencies import Principal, require_platform_admin
from app.security import ACCESS_TOKEN_TYPE, create_access_token
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


def _build_admin_only_app() -> FastAPI:
    """Yalnizca bu test dosyasi icin: `require_platform_admin`i tek basina zorlayan minik bir app."""

    app = FastAPI()

    @app.get("/admin-only")
    async def admin_only(principal: Principal = Depends(require_platform_admin)) -> dict:
        return {"user_id": str(principal.user_id)}

    return app


def _unique_email() -> str:
    return f"platform-admin-test-{uuid.uuid4().hex[:12]}@example.com"


def _register(client) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": _unique_email(),
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201
    return response.json()


def _access_token_cookie(client) -> str:
    for cookie in client.cookies.jar:
        if cookie.name == ACCESS_TOKEN_COOKIE:
            return cookie.value
    raise AssertionError("access_token cookie bulunamadi")


async def _set_platform_admin_in_db(user_id: str, *, is_platform_admin: bool) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE users SET is_platform_admin = :value WHERE id = :user_id"),
                {"value": is_platform_admin, "user_id": user_id},
            )
    finally:
        await engine.dispose()


def _forged_admin_token(user_id: str, organization_id: str, role: str = "owner") -> str:
    """`is_platform_admin=True` iddia eden, ama gercek `create_access_token`
    tarafindan HIC uretilmeyen bir claim tasiyan sahte bir token uretir.

    `require_platform_admin` bu claim'i asla okumaz (bkz. app.dependencies) -
    bu yuzden DB'de `is_platform_admin=False` olan bir kullanici icin bu
    token yine de 403 almalidir.
    """

    now = datetime.now(UTC)
    payload = {
        "type": ACCESS_TOKEN_TYPE,
        "sub": user_id,
        "org": organization_id,
        "role": role,
        "is_platform_admin": True,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def test_no_session_returns_401(client):
    admin_app = _build_admin_only_app()
    with TestClient(admin_app) as admin_client:
        response = admin_client.get("/admin-only")
    assert response.status_code == 401


def test_non_admin_organization_owner_returns_403(client):
    # Kayittan hemen sonraki rol her zaman "owner"dir (bkz. auth_service.
    # DEFAULT_ROLE) - bu, organizasyon-ici owner rolunun platform yoneticiligi
    # SAGLAMADIGINI dogrudan kanitlar.
    session_body = _register(client)
    assert session_body["role"] == "owner"
    assert session_body["is_platform_admin"] is False

    token = _access_token_cookie(client)
    admin_app = _build_admin_only_app()
    with TestClient(admin_app) as admin_client:
        admin_client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        response = admin_client.get("/admin-only")
    assert response.status_code == 403


def test_promote_grants_access_with_existing_token_without_new_login(client):
    session_body = _register(client)
    token = _access_token_cookie(client)

    asyncio.run(_set_platform_admin_in_db(session_body["user_id"], is_platform_admin=True))

    admin_app = _build_admin_only_app()
    with TestClient(admin_app) as admin_client:
        admin_client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        response = admin_client.get("/admin-only")
    assert response.status_code == 200
    assert response.json()["user_id"] == session_body["user_id"]


def test_demote_revokes_access_immediately_with_same_old_token(client):
    session_body = _register(client)
    token = _access_token_cookie(client)

    asyncio.run(_set_platform_admin_in_db(session_body["user_id"], is_platform_admin=True))

    admin_app = _build_admin_only_app()
    with TestClient(admin_app) as admin_client:
        admin_client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        promoted_response = admin_client.get("/admin-only")
    assert promoted_response.status_code == 200

    asyncio.run(_set_platform_admin_in_db(session_body["user_id"], is_platform_admin=False))

    # AYNI (suresi henuz dolmamis) eski access token ile tekrar denenir -
    # JWT hicbir zaman degismedi, yalnizca DB degisti.
    with TestClient(admin_app) as admin_client:
        admin_client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        demoted_response = admin_client.get("/admin-only")
    assert demoted_response.status_code == 403


def test_forged_is_platform_admin_claim_is_ignored(client):
    session_body = _register(client)
    # Bu kullanici DB'de gercekten platform admin DEGIL.
    forged_token = _forged_admin_token(session_body["user_id"], session_body["organization_id"])

    admin_app = _build_admin_only_app()
    with TestClient(admin_app) as admin_client:
        admin_client.cookies.set(ACCESS_TOKEN_COOKIE, forged_token)
        response = admin_client.get("/admin-only")
    assert response.status_code == 403


def test_real_platform_admin_can_access(client):
    session_body = _register(client)
    token = _access_token_cookie(client)
    asyncio.run(_set_platform_admin_in_db(session_body["user_id"], is_platform_admin=True))

    admin_app = _build_admin_only_app()
    with TestClient(admin_app) as admin_client:
        admin_client.cookies.set(ACCESS_TOKEN_COOKIE, token)
        response = admin_client.get("/admin-only")
    assert response.status_code == 200


def test_unused_create_access_token_signature_is_unchanged() -> None:
    """Plan geregi: `create_access_token` JWT'ye `is_platform_admin` EKLEMEZ.

    Bu, imzanin (ve dolayisiyla payload'in) kazara genisletilmedigini
    dogrulayan basit bir sozlesme testidir.
    """

    token = create_access_token(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="owner")
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    assert "is_platform_admin" not in payload
