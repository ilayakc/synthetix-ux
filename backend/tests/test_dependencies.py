"""`app.dependencies` icin birim testleri: gecersiz/eksik erisim tokeni ve rol
yetki kontrolu (`require_roles`). Bu modul kimlik dogrulama baglaminin nasil
cozumlendigini (bkz. `docs/architecture.md#kimlik-doğrulama-ve-oturum`) ve
tenant/rol izolasyonunun ilk savunma katmanini tasir.
"""

import uuid

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import settings
from app.dependencies import Principal, require_roles

pytestmark = pytest.mark.security


def test_malformed_access_token_cookie_is_rejected(client: TestClient):
    client.cookies.set("access_token", "not-a-valid-jwt")
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_access_token_missing_required_claims_is_rejected(client: TestClient):
    # Gecerli bir imza tasir ama zorunlu 'org' claim'i eksiktir; bu, bir
    # istemcinin (veya bozuk bir tokenin) `sub`/`org`/`role` doldurulmadan
    # kabul edilmesini engelledigini dogrular.
    incomplete_payload = {"type": "access", "sub": str(uuid.uuid4()), "role": "owner"}
    token = jwt.encode(incomplete_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    client.cookies.set("access_token", token)
    response = client.get("/api/auth/me")
    assert response.status_code == 401


async def test_require_roles_rejects_disallowed_role():
    checker = require_roles("owner", "admin")
    principal = Principal(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="viewer")

    with pytest.raises(HTTPException) as exc_info:
        await checker(principal=principal)
    assert exc_info.value.status_code == 403


async def test_require_roles_accepts_allowed_role():
    checker = require_roles("owner", "admin")
    principal = Principal(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="owner")

    result = await checker(principal=principal)
    assert result is principal
