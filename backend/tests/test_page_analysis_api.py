"""URL analiz servisinin (page_analysis) API katmani testleri.

Bu testler kasitli olarak `test_page_analysis.py`'den AYRI bir dosyadadir: o
dosya ham `AsyncSession` fixture'lari kullanir, bu dosya ise `app.main.app`
uzerinden `TestClient` kullanir. `client` fixture'i tests/conftest.py'den
gelir: her test kendi izole test veritabanina yonlendirilmis, taze (ve
kimlik dogrulanmamis) bir `TestClient` alir; bu yuzden her test kendi
`_register(client)` cagrisini yapar.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

pytestmark = pytest.mark.integration


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient) -> dict:
    email = _unique_email()
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _csrf_headers(client: TestClient) -> dict:
    token = None
    for cookie in client.cookies.jar:
        if cookie.name == "csrf_token":
            token = cookie.value
    assert token, "csrf_token cookie set olmali"
    return {"X-CSRF-Token": token}


@pytest.mark.security
def test_api_rejects_missing_authorization_confirmation(client: TestClient):
    _register(client)
    response = client.post(
        "/api/page-analyses",
        json={"url": "https://example.com/", "authorization_confirmed": False},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


@pytest.mark.security
def test_api_rejects_private_ip_target(client: TestClient):
    _register(client)
    response = client.post(
        "/api/page-analyses",
        json={"url": "http://127.0.0.1/", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_api_creates_queued_analysis_for_public_url(client: TestClient):
    _register(client)
    response = client.post(
        "/api/page-analyses",
        json={"url": "https://example.com/", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["has_screenshot"] is False

    fetched = client.get(f"/api/page-analyses/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_api_get_unknown_analysis_returns_404(client: TestClient):
    _register(client)
    response = client.get(f"/api/page-analyses/{uuid.uuid4()}")
    assert response.status_code == 404


# --- Ortak kaynak sozlesmesi: istek dogrulama (API katmani) --------------------


def _png_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(20, 40, 60)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_design_asset(client: TestClient) -> str:
    response = client.post(
        "/api/design-assets",
        files={"file": ("design.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_api_rejects_both_url_and_design_asset_id(client: TestClient):
    _register(client)
    asset_id = _upload_design_asset(client)
    response = client.post(
        "/api/page-analyses",
        json={
            "url": "https://example.com/",
            "design_asset_id": asset_id,
            "authorization_confirmed": True,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422


def test_api_rejects_neither_url_nor_design_asset_id(client: TestClient):
    _register(client)
    response = client.post(
        "/api/page-analyses",
        json={"authorization_confirmed": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422


def test_api_rejects_source_kind_conflicting_with_url(client: TestClient):
    _register(client)
    response = client.post(
        "/api/page-analyses",
        json={
            "url": "https://example.com/",
            "source_kind": "design_asset",
            "authorization_confirmed": True,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 422


def test_api_creates_queued_analysis_for_design_asset(client: TestClient):
    _register(client)
    asset_id = _upload_design_asset(client)
    response = client.post(
        "/api/page-analyses",
        json={"design_asset_id": asset_id},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["source_kind"] == "design_asset"
    assert body["design_asset_id"] == asset_id
    assert body["url"] is None
    assert body["has_screenshot"] is False


@pytest.mark.security
def test_api_design_asset_from_other_organization_returns_404(client: TestClient):
    _register(client)
    asset_id = _upload_design_asset(client)

    # Ikinci kayit ayni `client` uzerinde yeni bir organizasyona gecer
    # (cookie tabanli oturum degisir) - ilk org'un asset'ine artik erisim yok.
    _register(client)
    response = client.post(
        "/api/page-analyses",
        json={"design_asset_id": asset_id},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 404


def test_api_organization_id_in_body_is_ignored(client: TestClient):
    """Istekte bilinmeyen bir `organization_id` alani gonderilmesi istegi
    reddetmez (pydantic bilinmeyen alani yok sayar) ama tenant kimligi HER
    ZAMAN authenticated principal'dan turetilir - donen kaydin
    `organization_id`'si, istekte gonderilen rastgele degerle DEGIL, kayitli
    kullanicinin kendi organizasyonuyla eslesir."""

    registration = _register(client)
    injected_org_id = str(uuid.uuid4())
    response = client.post(
        "/api/page-analyses",
        json={
            "url": "https://example.com/",
            "authorization_confirmed": True,
            "organization_id": injected_org_id,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["organization_id"] != injected_org_id
    assert body["organization_id"] == registration["organization_id"]
