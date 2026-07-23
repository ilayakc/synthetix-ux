"""AI ile tasarim varyanti uretimi: API (router) katmani + sihirbaz (wizard)
entegrasyonu uctan uca testleri.

`test_design_assets_api.py` ile ayni desen: gercek bir `TestClient` (bkz.
tests/conftest.py) kullanilir. Worker/cron gercekte calismadigi icin, "isin
islenmesi" adimi testte dogrudan `app.services.design_generation.claim_next_queued`
+ `process_job` cagrilarak simule edilir (arq'in kendisi test edilmez, yalnizca
is akisinin DB durumu dogrulanir).
"""

from __future__ import annotations

import io
import uuid

import anyio
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_DATABASE_URL

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


def _png_bytes(width: int = 50, height: int = 40, color: tuple = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_design_asset(client: TestClient) -> dict:
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "Test projesi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def _patch_draft(client: TestClient, draft_id: str, payload: dict) -> dict:
    response = client.patch(
        f"/api/tests/drafts/{draft_id}", json={"payload": payload}, headers=_csrf_headers(client)
    )
    return response


async def _enable_provider_and_process_job(job_id: str, *, result_bytes: bytes | None = None) -> None:
    """Ayri bir DB baglantisi uzerinden: saglayiciyi 'remote' olarak acar
    (endpoint gercekten cagrilmaz - mock provider enjekte edilir), isi
    kuyruktan alir ve kontrollu bir sahte saglayiciyla isler."""

    from app.models.design_generation import DesignGenerationJob, DesignGenerationStatus
    from app.services import design_generation as design_generation_service

    class _MockProvider(design_generation_service.BaseImageGenerationProvider):
        name = "mock-remote"
        model_name = "mock-model-v1"

        async def generate(self, *, reference_image: bytes, reference_content_type: str, prompt: str):
            return design_generation_service.GeneratedImageResult(
                raw_bytes=result_bytes or _png_bytes(color=(9, 9, 9)), provider_request_id="mock-req"
            )

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = await session.get(DesignGenerationJob, uuid.UUID(job_id))
            assert job is not None
            job.status = DesignGenerationStatus.RUNNING
            await session.flush()
            await design_generation_service.process_job(session, job, provider=_MockProvider())
            await session.commit()
    finally:
        await engine.dispose()


def _enable_provider_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "image_generation_provider", "remote")
    monkeypatch.setattr(settings, "image_generation_endpoint", "https://mock.invalid/generate")


# --- Provider kapali (varsayilan) -----------------------------------------------------


def test_availability_reports_disabled_by_default(client: TestClient):
    _register(client)
    response = client.get("/api/design-generations/availability")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["disabled_reason"]
    assert "sağlayıcı" in body["disabled_reason"].lower() or "yapılandırılmadı" in body["disabled_reason"].lower()


def test_create_generation_returns_503_and_creates_nothing_when_provider_off(client: TestClient):
    _register(client)
    asset = _upload_design_asset(client)

    response = client.post(
        "/api/design-generations",
        json={
            "source_asset_id": asset["id"],
            "prompt": "Ana CTA'yi turuncu yap",
            "authorization_confirmed": True,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 503


# --- Provider acikken: is olusturma / dogrulama ---------------------------------------


def test_availability_reports_enabled_when_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    response = client.get("/api/design-generations/availability")
    assert response.status_code == 200
    assert response.json()["available"] is True
    assert response.json()["disabled_reason"] is None


def test_create_generation_without_consent_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)

    response = client.post(
        "/api/design-generations",
        json={"source_asset_id": asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": False},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_create_generation_with_empty_prompt_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)

    response = client.post(
        "/api/design-generations",
        json={"source_asset_id": asset["id"], "prompt": "   ", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


@pytest.mark.security
def test_create_generation_with_other_tenant_source_asset_is_rejected_without_leaking(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)

    _register(client)  # organizasyon B'ye gecer
    response = client.post(
        "/api/design-generations",
        json={"source_asset_id": asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
    assert asset["id"] not in response.json()["detail"]


def test_create_generation_succeeds_and_queues(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)

    response = client.post(
        "/api/design-generations",
        json={
            "source_asset_id": asset["id"],
            "prompt": "Ana CTA'yi turuncu yap",
            "authorization_confirmed": True,
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["result_asset"] is None


# --- Is akisi: get, cancel, delete -----------------------------------------------------


@pytest.mark.security
def test_get_generation_tenant_isolation(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)
    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()

    _register(client)  # organizasyon B'ye gecer
    response = client.get(f"/api/design-generations/{job['id']}")
    assert response.status_code == 404


def test_get_generation_unknown_id_returns_404(client: TestClient):
    _register(client)
    response = client.get(f"/api/design-generations/{uuid.uuid4()}")
    assert response.status_code == 404


def test_cancel_queued_generation_succeeds(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)
    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()

    response = client.post(f"/api/design-generations/{job['id']}/cancel", headers=_csrf_headers(client))
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_delete_generation_succeeds_and_keeps_accepted_asset(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    asset = _upload_design_asset(client)
    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()

    anyio.run(_enable_provider_and_process_job, job["id"])
    succeeded = client.get(f"/api/design-generations/{job['id']}").json()
    assert succeeded["status"] == "succeeded"
    result_asset_id = succeeded["result_asset"]["id"]

    delete_response = client.delete(f"/api/design-generations/{job['id']}", headers=_csrf_headers(client))
    assert delete_response.status_code == 204

    metadata = client.get(f"/api/design-assets/{result_asset_id}")
    assert metadata.status_code == 200
    assert metadata.json()["status"] == "active"


# --- Sihirbaz entegrasyonu: kabul/reddet/yeniden-uret --------------------------------


def _ab_base_payload(project_id: str) -> dict:
    return {
        "project_id": project_id,
        "name": f"AB testi {uuid.uuid4().hex[:6]}",
        "target_task": "Kayit formunu tamamla",
        "test_type": "ab_comparison",
        "current_url": "https://example.com",
        "persona_count": 500,
        "target_audience": "Yeni kullanicilar",
        "modules": [],
    }


def test_wizard_accepts_succeeded_ai_generation_as_design_b(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    source_asset = _upload_design_asset(client)

    job = client.post(
        "/api/design-generations",
        json={
            "source_asset_id": source_asset["id"],
            "prompt": "Ana CTA'yi turuncu yap",
            "authorization_confirmed": True,
        },
        headers=_csrf_headers(client),
    ).json()
    anyio.run(_enable_provider_and_process_job, job["id"])
    succeeded = client.get(f"/api/design-generations/{job['id']}").json()
    result_asset_id = succeeded["result_asset"]["id"]

    payload = _ab_base_payload(project["id"])
    payload["new_source_type"] = "ai_generated"
    payload["new_design_asset_id"] = result_asset_id
    payload["new_ai_generation_id"] = job["id"]
    response = _patch_draft(client, draft["id"], payload)
    assert response.status_code == 200, response.text
    assert response.json()["payload"]["new_source_type"] == "ai_generated"


def test_wizard_rejects_ai_generation_not_yet_succeeded(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Kullanici, hala 'queued'/'running' olan bir isi (ör. hile amacli
    dogrudan API cagrisiyla) draft'a "kabul edilmis" gibi baglayamaz."""

    _enable_provider_settings(monkeypatch)
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    source_asset = _upload_design_asset(client)

    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": source_asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()
    # Is kasitli olarak ISLENMEDEN (hala 'queued') draft'a baglanmaya calisilir.

    payload = _ab_base_payload(project["id"])
    payload["new_source_type"] = "ai_generated"
    payload["new_design_asset_id"] = source_asset["id"]  # gercek sonuc degil, kaynak asset
    payload["new_ai_generation_id"] = job["id"]
    response = _patch_draft(client, draft["id"], payload)
    assert response.status_code == 400


def test_wizard_rejects_mismatched_asset_and_generation_id(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _enable_provider_settings(monkeypatch)
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    source_asset = _upload_design_asset(client)
    other_asset = _upload_design_asset(client)

    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": source_asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()
    anyio.run(_enable_provider_and_process_job, job["id"])

    payload = _ab_base_payload(project["id"])
    payload["new_source_type"] = "ai_generated"
    payload["new_design_asset_id"] = other_asset["id"]  # isin GERCEK sonucu degil
    payload["new_ai_generation_id"] = job["id"]
    response = _patch_draft(client, draft["id"], payload)
    assert response.status_code == 400


def test_wizard_rejecting_ai_result_keeps_existing_manual_design_b_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Bir AI uretim isinin BASLATILMASI/BASARISIZ OLMASI, taslakta zaten
    kayitli manuel Tasarim B kaynagini (screenshot) SILMEMELI/DEGISTIRMEMELI -
    kaynak degisimi yalnizca ACIK bir kabul (accept) PATCH'iyle olur."""

    _enable_provider_settings(monkeypatch)
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    manual_b_asset = _upload_design_asset(client)
    source_asset = _upload_design_asset(client)

    payload = _ab_base_payload(project["id"])
    del payload["current_url"]
    payload["current_design_asset_id"] = source_asset["id"]
    payload["current_source_type"] = "screenshot"
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = manual_b_asset["id"]
    saved = _patch_draft(client, draft["id"], payload)
    assert saved.status_code == 200, saved.text

    # Kullanici AI ile yeniden uretim dener (basarisiz olur) ama draft'a HIC dokunmaz.
    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": source_asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()
    anyio.run(_enable_provider_and_process_job, job["id"])

    reloaded = client.get(f"/api/tests/drafts/{draft['id']}").json()
    assert reloaded["payload"]["new_source_type"] == "screenshot"
    assert reloaded["payload"]["new_design_asset_id"] == manual_b_asset["id"]


def test_wizard_regenerated_unaccepted_result_does_not_become_active_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Iki ayri (basarili) uretim isi olustursa bile, HICBIRI acikca kabul
    edilmeden draft'in Tasarim B kaynagi degismez."""

    _enable_provider_settings(monkeypatch)
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    source_asset = _upload_design_asset(client)

    job1 = client.post(
        "/api/design-generations",
        json={"source_asset_id": source_asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()
    anyio.run(_enable_provider_and_process_job, job1["id"])

    job2 = client.post(
        "/api/design-generations",
        json={"source_asset_id": source_asset["id"], "prompt": "Kartlari genislet", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()
    anyio.run(_enable_provider_and_process_job, job2["id"])

    reloaded = client.get(f"/api/tests/drafts/{draft['id']}").json()
    assert "new_source_type" not in reloaded["payload"] or reloaded["payload"].get("new_source_type") in (
        None,
        "url",
    )
    assert reloaded["payload"].get("new_design_asset_id") is None


def test_launch_succeeds_when_ai_generated_source_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Paket 4 Final: kabul edilmis (SUCCEEDED, dogru `result_asset_id`
    eslesen) bir AI uretim sonucuyla launch artik engellenmez - saglayici
    burada mock'lansa da (`_enable_provider_and_process_job`), gercek
    dogrulama (job sahipligi/durumu/asset eslesmesi) `launch_draft` icinde
    AYNEN tekrar calisir (bkz. `_revalidate_launch_sources`)."""

    _enable_provider_settings(monkeypatch)
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    source_asset = _upload_design_asset(client)

    job = client.post(
        "/api/design-generations",
        json={"source_asset_id": source_asset["id"], "prompt": "Baslik kisalt", "authorization_confirmed": True},
        headers=_csrf_headers(client),
    ).json()
    anyio.run(_enable_provider_and_process_job, job["id"])
    succeeded = client.get(f"/api/design-generations/{job['id']}").json()

    payload = _ab_base_payload(project["id"])
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = source_asset["id"]
    del payload["current_url"]
    payload["new_source_type"] = "ai_generated"
    payload["new_design_asset_id"] = succeeded["result_asset"]["id"]
    payload["new_ai_generation_id"] = job["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2
