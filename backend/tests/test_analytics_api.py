"""Ziyaretci/trafik analitigi API testleri.

Kapsam (docs/testing.md): anonim page_view kaydi, consent reddi davranisi,
basarili login olayinin bir kez kaydi, basarisiz login'in hassas bilgi
icermemesi, signup/organization attribution, platform-admin erisimi, normal
kullanici icin 403, filtre/tarih araligi, sayfalama, tracking link olusturma,
open redirect engelleme, gecersiz event type reddi, rate limiting,
dedup/idempotency, CSV export yetkisi + formula injection korumasi, retention
cleanup.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import redis.asyncio as aioredis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.analytics import AnalyticsEvent, AnalyticsEventType
from app.services import analytics as analytics_service
from app.services import rate_limit
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Yardimcilar                                                                 #
# --------------------------------------------------------------------------- #


def _register(client, *, prefix: str = "analytics", display_name: str = "Analitik Test") -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"{prefix}-{uuid.uuid4().hex[:10]}@example.com",
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": display_name,
        },
    )
    assert response.status_code == 201, response.text
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


def _promote_admin(client, session: dict) -> None:
    asyncio.run(_set_platform_admin(session["user_id"]))


def _page_view(client, *, consent: bool = True, **body) -> None:
    payload = {"event_type": "page_view", "consent": consent, **body}
    response = client.post("/api/analytics/events", json=payload)
    assert response.status_code == 204, response.text


# --------------------------------------------------------------------------- #
# Public ingestion + admin erisim                                             #
# --------------------------------------------------------------------------- #


def test_anonymous_page_view_is_recorded_and_visible_to_admin(client):
    _page_view(client, path="/", utm_source="linkedin", utm_campaign="accelerator")

    admin = _register(client, prefix="pv-admin")
    _promote_admin(client, admin)

    overview = client.get("/api/admin/analytics/overview")
    assert overview.status_code == 200
    metrics = overview.json()["metrics"]
    assert metrics["total_page_views"] >= 1
    assert metrics["total_unique_visitors"] >= 1


def test_page_view_without_consent_is_not_recorded(client):
    # Varsayilan ANALYTICS_REQUIRE_CONSENT=true: consent verilmeden pazarlama
    # analitigi islenmez.
    resp = client.post("/api/analytics/events", json={"event_type": "page_view", "consent": False})
    assert resp.status_code == 204

    admin = _register(client, prefix="noconsent-admin")
    _promote_admin(client, admin)
    overview = client.get("/api/admin/analytics/overview")
    assert overview.json()["metrics"]["total_page_views"] == 0


def test_invalid_event_type_is_rejected(client):
    resp = client.post("/api/analytics/events", json={"event_type": "login_succeeded", "consent": True})
    assert resp.status_code == 422


def test_non_admin_cannot_access_analytics(client):
    _register(client, prefix="non-admin")
    for path in (
        "/api/admin/analytics/overview",
        "/api/admin/analytics/users",
        "/api/admin/analytics/organizations",
        "/api/admin/analytics/visits",
        "/api/admin/analytics/tracking-links",
        "/api/admin/analytics/users/export.csv",
    ):
        assert client.get(path).status_code == 403, path


def test_platform_admin_can_access_overview(client):
    admin = _register(client, prefix="admin-access")
    _promote_admin(client, admin)
    assert client.get("/api/admin/analytics/overview").status_code == 200


# --------------------------------------------------------------------------- #
# Login / signup is olaylari                                                  #
# --------------------------------------------------------------------------- #


def test_successful_login_records_exactly_one_login_event(client):
    admin = _register(client, prefix="login-once")
    _promote_admin(client, admin)
    # Kayit (register) 1 login_succeeded uretir; ek 1 login daha yapalim.
    login = client.post("/api/auth/login", json={"email": admin["email"], "password": "CorrectHorse123!"})
    assert login.status_code == 200

    users = client.get("/api/admin/analytics/users", params={"search": admin["email"]})
    assert users.status_code == 200
    rows = users.json()["users"]
    match = [u for u in rows if u["email"] == admin["email"]]
    assert match, "kullanici listede olmali"
    # register (1) + login (1) = 2 basarili giris olayi.
    assert match[0]["total_logins"] == 2
    assert match[0]["first_login_at"] is not None
    assert match[0]["last_login_at"] is not None


def test_failed_login_records_no_sensitive_data(client):
    victim = _register(client, prefix="victim")
    client.post("/api/auth/logout", headers={"X-CSRF-Token": client.cookies.get("csrf_token")})

    bad = client.post("/api/auth/login", json={"email": victim["email"], "password": "WrongPassword999!"})
    assert bad.status_code == 401

    # Basarisiz giris olayi kimlik ICERMEMELIDIR (user_id null, e-posta yok).
    rows = asyncio.run(_failed_login_events())
    assert rows, "en az bir basarisiz-giris ozet olayi olmali"
    for user_id, path, referral in rows:
        assert user_id is None
        assert path is None
        assert referral is None


async def _failed_login_events():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                select(AnalyticsEvent.user_id, AnalyticsEvent.path, AnalyticsEvent.referral_code).where(
                    AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_FAILED_SECURITY_SUMMARY
                )
            )
            return list(result.all())
    finally:
        await engine.dispose()


def test_signup_attribution_links_campaign_to_user_and_org(client):
    # Anonim ziyaretci once bir kampanya linkiyle gelir (visitor cerezi olusur),
    # sonra kayit olur -> edinim kaynagi kullaniciya + organizasyona baglanir.
    _page_view(client, path="/", utm_source="linkedin", utm_campaign="itu_cekirdek", ref="itu")
    session = _register(client, prefix="attributed")
    _promote_admin(client, session)

    overview = client.get("/api/admin/analytics/overview")
    assert overview.json()["metrics"]["campaign_referred_signups"] >= 1

    detail = client.get(f"/api/admin/analytics/organizations/{session['organization_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["first_campaign"] == "itu_cekirdek"


# --------------------------------------------------------------------------- #
# Filtreler, sayfalama                                                        #
# --------------------------------------------------------------------------- #


def test_overview_respects_date_range(client):
    _page_view(client, path="/")
    admin = _register(client, prefix="range-admin")
    _promote_admin(client, admin)

    # Cok eski bir aralik: bugunku ziyaretler kapsam disinda kalir.
    old = client.get(
        "/api/admin/analytics/overview",
        params={"start": "2000-01-01", "end": "2000-01-31"},
    )
    assert old.status_code == 200
    assert old.json()["metrics"]["successful_logins_in_range"] == 0


def test_users_pagination(client):
    # Once ek kullanicilari olustur, ADMIN'i EN SON kaydet - boylece istemci
    # oturumu admin olarak kalir (her register yeni oturum cookie'si verir).
    for i in range(3):
        _register(client, prefix=f"paged-{i}")
    admin = _register(client, prefix="page-admin")
    _promote_admin(client, admin)

    page = client.get("/api/admin/analytics/users", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] >= 4
    assert len(body["users"]) == 2


def test_visits_event_type_filter(client):
    _page_view(client, path="/pricing")
    admin = _register(client, prefix="visits-admin")
    _promote_admin(client, admin)

    visits = client.get("/api/admin/analytics/visits", params={"event_type": "page_view"})
    assert visits.status_code == 200
    assert all(e["event_type"] == "page_view" for e in visits.json()["events"])


# --------------------------------------------------------------------------- #
# Tracking links + open redirect                                             #
# --------------------------------------------------------------------------- #


def test_create_tracking_link_and_redirect(client):
    admin = _register(client, prefix="track-admin")
    _promote_admin(client, admin)

    create = client.post(
        "/api/admin/analytics/tracking-links",
        json={
            "name": "LinkedIn Outreach",
            "destination_path": "/kayit",
            "utm_source": "linkedin",
            "utm_medium": "outreach",
            "utm_campaign": "accelerator_august",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["referral_code"]
    assert body["tracking_url"].endswith(body["referral_code"])
    # Referral code tahmin edilmesi kolay sirali bir ID olmamali.
    assert len(body["referral_code"]) >= 8

    redirect = client.get(f"/api/analytics/track/{body['referral_code']}", follow_redirects=False)
    assert redirect.status_code == 302
    location = redirect.headers["location"]
    assert "/kayit" in location
    assert "evil" not in location


def test_tracking_link_rejects_open_redirect_destinations(client):
    admin = _register(client, prefix="redirect-admin")
    _promote_admin(client, admin)
    for bad in ("//evil.com", "https://evil.com", "/\\evil", "javascript:alert(1)"):
        resp = client.post(
            "/api/admin/analytics/tracking-links",
            json={"name": "bad", "destination_path": bad},
        )
        assert resp.status_code == 400, bad


# --------------------------------------------------------------------------- #
# Dedup / rate limit / CSV / retention                                        #
# --------------------------------------------------------------------------- #


def test_duplicate_event_id_is_deduplicated(client):
    event_id = str(uuid.uuid4())
    _page_view(client, path="/", event_id=event_id)
    _page_view(client, path="/", event_id=event_id)

    admin = _register(client, prefix="dedup-admin")
    _promote_admin(client, admin)
    overview = client.get("/api/admin/analytics/overview")
    assert overview.json()["metrics"]["total_page_views"] == 1


def test_analytics_ingest_rate_limit_helper(monkeypatch):
    monkeypatch.setattr(settings, "analytics_ingest_rate_limit_max_events", 3)
    monkeypatch.setattr(settings, "analytics_ingest_rate_limit_window_seconds", 60)

    async def _run() -> list[bool]:
        redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
        identifier = f"test-{uuid.uuid4().hex}"
        try:
            return [
                await rate_limit.is_analytics_ingest_rate_limited(redis_client, identifier) for _ in range(5)
            ]
        finally:
            await redis_client.aclose()

    results = asyncio.run(_run())
    # Ilk 3 istek limit icinde (False), sonrakiler reddedilir (True).
    assert results == [False, False, False, True, True]


def test_csv_export_requires_admin_and_escapes_formula_injection(client):
    # Formula injection: display_name '=' ile baslar.
    _register(client, prefix="csvuser", display_name="=1+2")
    admin = _register(client, prefix="csv-admin", display_name="Yonetici")
    # Once yetkisiz erisimi dogrula (henuz admin degil).
    assert client.get("/api/admin/analytics/users/export.csv").status_code == 403

    _promote_admin(client, admin)
    export = client.get("/api/admin/analytics/users/export.csv")
    assert export.status_code == 200
    assert "text/csv" in export.headers["content-type"]
    # `=1+2` guvenli sekilde tek tirnakla prefixlenmis olmali.
    assert "'=1+2" in export.text
    assert "=1+2" not in export.text.replace("'=1+2", "")


async def _insert_event_with_created_at(created_at: datetime) -> uuid.UUID:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    event_id = uuid.uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO analytics_events (id, event_type, created_at) "
                    "VALUES (:id, 'PAGE_VIEW', :created_at)"
                ),
                {"id": event_id, "created_at": created_at},
            )
    finally:
        await engine.dispose()
    return event_id


async def _count_events() -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT count(*) FROM analytics_events"))
            return int(result.scalar_one())
    finally:
        await engine.dispose()


def test_retention_purge_deletes_only_expired_rows(client, monkeypatch):
    monkeypatch.setattr(settings, "analytics_retention_days", 30)
    now = datetime.now(UTC)
    old_id = asyncio.run(_insert_event_with_created_at(now - timedelta(days=90)))
    fresh_id = asyncio.run(_insert_event_with_created_at(now - timedelta(days=1)))

    counts = asyncio.run(_run_purge())
    assert counts.events >= 1

    remaining = asyncio.run(_remaining_event_ids())
    assert old_id not in remaining
    assert fresh_id in remaining


async def _run_purge():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            return await analytics_service.purge_expired(session)
    finally:
        await engine.dispose()


async def _remaining_event_ids() -> set[uuid.UUID]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(select(AnalyticsEvent.id))
            return set(result.scalars())
    finally:
        await engine.dispose()
