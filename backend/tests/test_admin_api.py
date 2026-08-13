import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.cookies import CSRF_TOKEN_COOKIE
from app.models.audit import AuditLog
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


def _register(client, *, prefix: str = "admin-api") -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:10]}@example.com",
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Admin Test",
        },
    )
    assert response.status_code == 201
    return response.json()


async def _set_platform_admin(user_id: str) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE users SET is_platform_admin = true WHERE id = :user_id"),
                {"user_id": user_id},
            )
    finally:
        await engine.dispose()


async def _audit_actions(entity_id: str) -> list[str]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                select(AuditLog.action)
                .where(AuditLog.entity_id == uuid.UUID(entity_id))
                .order_by(AuditLog.created_at)
            )
            return list(result.scalars())
    finally:
        await engine.dispose()


def _csrf_headers(client) -> dict[str, str]:
    token = client.cookies.get(CSRF_TOKEN_COOKIE)
    assert token
    return {"X-CSRF-Token": token}


def test_non_admin_cannot_access_admin_summary(client):
    _register(client, prefix="non-admin")
    response = client.get("/api/admin/summary")
    assert response.status_code == 403


def test_non_admin_cannot_access_login_history(client):
    _register(client, prefix="non-admin-logins")
    response = client.get("/api/admin/login-history")
    assert response.status_code == 403


def test_login_history_records_registration_and_login(client):
    admin = _register(client, prefix="login-history-admin")
    asyncio.run(_set_platform_admin(admin["user_id"]))

    login = client.post(
        "/api/auth/login",
        json={"email": admin["email"], "password": "CorrectHorse123!"},
    )
    assert login.status_code == 200

    response = client.get("/api/admin/login-history")
    assert response.status_code == 200
    payload = response.json()

    # Kayit (register) + parola girisi = en az iki giris olayi.
    assert payload["total_logins"] >= 2
    assert payload["unique_users"] >= 1

    events = payload["events"]
    assert events, "giris gecmisi bos olmamali"
    # En yeni giris ustte olmali (created_at azalan siralama).
    timestamps = [event["logged_in_at"] for event in events]
    assert timestamps == sorted(timestamps, reverse=True)

    admin_events = [event for event in events if event["email"] == admin["email"]]
    login_types = {event["login_type"] for event in admin_events}
    assert {"register", "password"} <= login_types
    # Ham parola hicbir zaman kayda/yanita sizmamali.
    assert "CorrectHorse123!" not in response.text


def test_admin_settings_are_sanitized_and_operational(client):
    session = _register(client, prefix="settings-admin")
    asyncio.run(_set_platform_admin(session["user_id"]))

    response = client.get("/api/admin/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["environment"]
    assert body["ai_report"]["provider"] in {"disabled", "mock", "openai", "ollama"}
    assert body["chip_topups"]["review_mode"] == "manual_admin_review"
    assert body["chip_topups"]["approval_credits_once"] is True
    assert body["security"]["access_token_ttl_minutes"] > 0
    assert body["operations"]["report_screenshot_retention_days"] > 0
    assert "openai_api_key" not in response.text
    assert "jwt_secret_key" not in response.text


def test_admin_can_approve_topup_once_and_credit_balance(client):
    session = _register(client)
    asyncio.run(_set_platform_admin(session["user_id"]))

    create_response = client.post("/api/billing/topup-requests", json={"package_key": "starter"})
    assert create_response.status_code == 201
    request_id = create_response.json()["id"]

    pending = client.get("/api/admin/topup-requests", params={"status": "pending"})
    assert pending.status_code == 200
    assert any(row["id"] == request_id for row in pending.json())

    review_response = client.post(
        f"/api/admin/topup-requests/{request_id}/review",
        json={"action": "approve", "note": "Ödeme doğrulandı"},
        headers=_csrf_headers(client),
    )
    assert review_response.status_code == 200
    assert review_response.json()["status"] == "approved"
    assert review_response.json()["review_note"] == "Ödeme doğrulandı"

    balance = client.get("/api/billing/usage-summary")
    assert balance.status_code == 200
    assert balance.json()["chip_balance"] == 100
    assert asyncio.run(_audit_actions(request_id)) == [
        "chip_topup_requested",
        "chip_topup_approved",
    ]

    repeated = client.post(
        f"/api/admin/topup-requests/{request_id}/review",
        json={"action": "approve", "note": "Tekrar"},
        headers=_csrf_headers(client),
    )
    assert repeated.status_code == 200
    assert client.get("/api/billing/usage-summary").json()["chip_balance"] == 100
    assert asyncio.run(_audit_actions(request_id)) == [
        "chip_topup_requested",
        "chip_topup_approved",
    ]


def test_admin_rejection_requires_note_and_does_not_credit(client):
    session = _register(client, prefix="reject-admin")
    asyncio.run(_set_platform_admin(session["user_id"]))
    request_id = client.post("/api/billing/topup-requests", json={"package_key": "growth"}).json()["id"]

    missing_note = client.post(
        f"/api/admin/topup-requests/{request_id}/review",
        json={"action": "reject"},
        headers=_csrf_headers(client),
    )
    assert missing_note.status_code == 400

    rejected = client.post(
        f"/api/admin/topup-requests/{request_id}/review",
        json={"action": "reject", "note": "Ödeme belgesi bulunamadı"},
        headers=_csrf_headers(client),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.get("/api/billing/usage-summary").json()["chip_balance"] == 0
    assert asyncio.run(_audit_actions(request_id)) == [
        "chip_topup_requested",
        "chip_topup_rejected",
    ]


def test_admin_summary_returns_platform_counts(client):
    session = _register(client, prefix="summary-admin")
    asyncio.run(_set_platform_admin(session["user_id"]))
    client.post("/api/billing/topup-requests", json={"package_key": "scale"})

    response = client.get("/api/admin/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["organization_count"] >= 1
    assert body["user_count"] >= 1
    assert body["pending_topup_count"] >= 1
    assert body["failed_ai_pipeline_count"] >= 0
