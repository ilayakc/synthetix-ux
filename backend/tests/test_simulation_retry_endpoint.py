"""HTTP (router) seviyesinde retry/cancel uc noktalari icin regresyon testleri.

Kok neden (bu dosyanin dogruladigi hata): `app.routers.simulations.retry_run`
(ve `cancel_run`) `run`u UPDATE ettikten sonra `await session.commit()` cagirip
ardindan `_to_response(run)` ile yaniti kurar. `SimulationRun.updated_at`
server-side `onupdate=func.now()` tasidigi icin (bkz. app.models.base.
TimestampMixin) flush sonrasi ORM tarafinda EXPIRED kalir; `_to_response`
`run.updated_at`i duz attribute erisimiyle okudugunda async engine'de senkron
bir lazy-load denenir ve `sqlalchemy.exc.MissingGreenlet` -> HTTP 500 olusurdu.

Bu senaryo daha once yalnizca SERVIS seviyesinde (`simulation_worker.retry_run`,
commit/serialize ADIMI OLMADAN) test edildigi icin yakalanmamisti; bu dosya
gercek TestClient uzerinden basarili (200) retry/cancel yolunu egzersiz eder.

`client` fixture'i tests/conftest.py'dendir (gercek, commit eden TestClient).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.page_analysis import PageAnalysis, PageAnalysisStatus
from app.models.simulations import SimulationRun
from app.services import simulation_worker
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": _unique_email(),
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _csrf_headers(client: TestClient) -> dict:
    token = next((c.value for c in client.cookies.jar if c.name == "csrf_token"), None)
    assert token, "csrf_token cookie set olmali"
    return {"X-CSRF-Token": token}


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def _basic_url_payload(project_id: str) -> dict:
    """Modulsuz, tekil URL kaynakli temel UX testi (ucretsiz hak -> 0 Chip)."""

    return {
        "project_id": project_id,
        "name": f"Anasayfa testi {uuid.uuid4().hex[:6]}",
        "target_task": "Kullanicinin sepete urun eklemesini gozlemle",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com/anasayfa",
        "persona_count": 500,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": [],
        "authorization_confirmed": True,
    }


def _launch_failed_url_run(client: TestClient) -> str:
    """URL kaynakli bir run baslatir ve (production'daki fail-blocked cron
    yolunu taklit ederek) bagli PageAnalysis'i FAILED yapip run'i FAILED'e
    dusurur; run_id dondurur."""

    project = _create_project(client)
    draft = _create_draft(client)
    _patch = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": _basic_url_payload(project["id"])},
        headers=_csrf_headers(client),
    )
    assert _patch.status_code == 200, _patch.text

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    run_id = launch.json()["simulation_run_ids"][0]

    import anyio

    anyio.run(_fail_run_via_page_analysis, run_id)
    return run_id


async def _fail_run_via_page_analysis(run_id: str) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None and run.page_analysis_id is not None
            analysis = await session.get(PageAnalysis, run.page_analysis_id)
            assert analysis is not None
            analysis.status = PageAnalysisStatus.FAILED
            analysis.error = "analyzer hatasi (502): cold start"
            analysis.finished_at = datetime.now(UTC)
            await session.flush()
            # Production yolu: bagli PageAnalysis FAILED olan QUEUED run'i FAILED
            # yapan cron; grup rezervasyonlarini/ucretsiz hakki serbest birakir.
            await simulation_worker.fail_runs_blocked_by_failed_page_analysis(session)
            await session.commit()
    finally:
        await engine.dispose()


async def _read_analysis_status_for_run(run_id: str) -> str:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None and run.page_analysis_id is not None
            analysis = await session.get(PageAnalysis, run.page_analysis_id)
            assert analysis is not None
            return analysis.status.value
    finally:
        await engine.dispose()


async def _count_runs_for_variant(run_id: str) -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            result = await session.execute(
                select(SimulationRun).where(SimulationRun.test_variant_id == run.test_variant_id)
            )
            return len(result.scalars().all())
    finally:
        await engine.dispose()


def test_retry_failed_url_run_returns_200_and_requeues(client):
    """REGRESYON: basarisiz URL run'inin retry'i ARTIK 200 doner (MissingGreenlet
    -> 500 degil) ve hem run'i hem bagli sayfa analizini yeniden kuyruga alir."""

    _register(client)
    run_id = _launch_failed_url_run(client)

    runs = client.get("/api/simulations/runs").json()
    run = next(r for r in runs if r["id"] == run_id)
    assert run["status"] == "failed"
    assert run["retryable"] is True

    response = client.post(f"/api/simulations/runs/{run_id}/retry", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["error"] is None
    assert body["result"] is None

    import anyio

    assert anyio.run(_read_analysis_status_for_run, run_id) == "queued"


def test_retry_twice_rejects_second_without_duplicate_run(client):
    """Ayni run iki kez retry edilirse ikinci istek 409 (yalnizca FAILED run'lar
    retry edilebilir) doner; yeni bir SimulationRun satiri OLUSMAZ (duplicate
    job yok) ve hicbir kosulda 500 uretilmez."""

    _register(client)
    run_id = _launch_failed_url_run(client)

    import anyio

    runs_before = anyio.run(_count_runs_for_variant, run_id)

    first = client.post(f"/api/simulations/runs/{run_id}/retry", headers=_csrf_headers(client))
    assert first.status_code == 200, first.text

    second = client.post(f"/api/simulations/runs/{run_id}/retry", headers=_csrf_headers(client))
    assert second.status_code == 409, second.text

    runs_after = anyio.run(_count_runs_for_variant, run_id)
    assert runs_after == runs_before  # ayni satir yeniden kuyruga alindi, yeni satir yok


def test_cancel_queued_run_returns_200(client):
    """REGRESYON: QUEUED bir run'in iptali (run'i UPDATE eder) ARTIK 200 doner -
    ayni MissingGreenlet kok nedeni cancel_run icin de duzeltildi."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    patch = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": _basic_url_payload(project["id"])},
        headers=_csrf_headers(client),
    )
    assert patch.status_code == 200, patch.text
    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    run_id = launch.json()["simulation_run_ids"][0]

    response = client.post(f"/api/simulations/runs/{run_id}/cancel", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    assert response.json()["status"] in ("queued", "cancelled")
