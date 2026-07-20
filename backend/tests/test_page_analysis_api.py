"""URL analiz servisinin (page_analysis) API katmani testleri.

Bu testler kasitli olarak `test_page_analysis.py`'den AYRI bir dosyadadir: o
dosya ham `AsyncSession` fixture'lari kullanir, bu dosya ise `app.main.app`
uzerinden `TestClient` kullanir. `client` fixture'i tests/conftest.py'den
gelir: her test kendi izole test veritabanina yonlendirilmis, taze (ve
kimlik dogrulanmamis) bir `TestClient` alir; bu yuzden her test kendi
`_register(client)` cagrisini yapar.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

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
