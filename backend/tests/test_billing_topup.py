"""Chip yukleme (top-up) paketleri ve talep akisi: bakiye degismez, kart verisi yoktur.

`client` fixture'i tests/conftest.py'den gelir (bkz. o dosyanin docstring'i).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.services import chip_ledger

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


def _snapshot_cookies(client: TestClient) -> list[tuple[str, str, str, str]]:
    return [(cookie.name, cookie.value, cookie.path, cookie.domain) for cookie in client.cookies.jar]


def _restore_cookies(client: TestClient, snapshot: list[tuple[str, str, str, str]]) -> None:
    client.cookies.clear()
    for name, value, path, domain in snapshot:
        client.cookies.set(name, value, domain=domain, path=path)


def test_list_chip_packages_does_not_use_disallowed_pdf_amounts(client):
    response = client.get("/api/billing/chip-packages")
    assert response.status_code == 200
    body = response.json()
    assert body["package_version"] == "2026.2"

    amounts = {p["chip_amount"] for p in body["packages"]}
    assert amounts == {50, 100, 200, 500, 1000, 2000}
    assert {p["price_try"] for p in body["packages"]} == {119, 199, 349, 799, 1399, 2499}
    # PDF'deki 1.500-3.000 Chip araligindaki degerler dogrudan kullanilmamali.
    assert 1500 not in amounts
    assert 3000 not in amounts


def test_create_topup_request_does_not_change_chip_balance(client):
    _register(client)

    balance_before = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_before == 0

    response = client.post(
        "/api/billing/topup-requests",
        json={"package_key": "growth"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["package_key"] == "growth"
    assert body["chip_amount"] == 500
    assert body["status"] == "pending"

    balance_after = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_after == 0


def test_create_topup_request_rejects_unknown_package(client):
    _register(client)

    response = client.post(
        "/api/billing/topup-requests",
        json={"package_key": "not-a-real-package"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_topup_request_response_never_includes_card_fields(client):
    _register(client)

    response = client.post(
        "/api/billing/topup-requests",
        json={"package_key": "starter"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201
    body_text = response.text.lower()
    for forbidden in ("card_number", "cvv", "card_expiry", "pan"):
        assert forbidden not in body_text


def test_list_topup_requests_returns_most_recent_first(client):
    _register(client)
    client.post("/api/billing/topup-requests", json={"package_key": "starter"}, headers=_csrf_headers(client))
    client.post("/api/billing/topup-requests", json={"package_key": "scale"}, headers=_csrf_headers(client))

    response = client.get("/api/billing/topup-requests")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["package_key"] == "scale"
    assert body[1]["package_key"] == "starter"


@pytest.mark.security
def test_topup_requests_are_tenant_isolated(client):
    _register(client)
    client.post("/api/billing/topup-requests", json={"package_key": "starter"}, headers=_csrf_headers(client))
    snapshot_a = _snapshot_cookies(client)

    _register(client)  # organizasyon B'ye gecer
    response = client.get("/api/billing/topup-requests")
    assert response.status_code == 200
    assert response.json() == []

    _restore_cookies(client, snapshot_a)
    own_response = client.get("/api/billing/topup-requests")
    assert len(own_response.json()) == 1


async def _credit_chips(organization_id: str, amount: int) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.models.tenancy import Organization
    from tests.conftest import TEST_DATABASE_URL

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            org = await session.get(Organization, uuid.UUID(organization_id))
            assert org is not None
            await chip_ledger.credit(session, org.id, amount, "test setup credit")
            await session.commit()
    finally:
        await engine.dispose()


def test_pending_topup_request_never_credits_ledger_even_with_existing_balance(client):
    import anyio

    session = _register(client)
    anyio.run(_credit_chips, session["organization_id"], 50)

    balance_before = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_before == 50

    client.post("/api/billing/topup-requests", json={"package_key": "scale"}, headers=_csrf_headers(client))

    balance_after = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_after == 50
