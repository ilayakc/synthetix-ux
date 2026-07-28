"""Proje yonetimi ve yeni test kurulum sihirbazi (5 adim) icin uctan uca testler.

`client` fixture'i tests/conftest.py'den gelir: her test kendi izole test
veritabanina yonlendirilmis, taze bir `TestClient` alir (bkz. conftest.py
docstring'i). Birden fazla kimlik gerektiginde cookie jar'i anlik
goruntulenip/degistirilir.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.tenancy import Organization
from app.services import chip_ledger
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


def _snapshot_cookies(client: TestClient) -> list[tuple[str, str, str, str]]:
    return [(cookie.name, cookie.value, cookie.path, cookie.domain) for cookie in client.cookies.jar]


def _restore_cookies(client: TestClient, snapshot: list[tuple[str, str, str, str]]) -> None:
    client.cookies.clear()
    for name, value, path, domain in snapshot:
        client.cookies.set(name, value, domain=domain, path=path)


async def _expire_design_asset(asset_id: str) -> None:
    """DesignAsset'in `expires_at`'ini gecmise cekerek saklama suresini dolmus
    isaretler (ikili veri hala DB'de olsa bile - purge cron'u henuz calismamis
    gibi); bkz. app.services.design_assets.is_expired."""

    from datetime import UTC, datetime, timedelta

    from app.models.design_assets import DesignAsset

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            asset = await session.get(DesignAsset, uuid.UUID(asset_id))
            assert asset is not None
            asset.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
    finally:
        await engine.dispose()


async def _purge_design_asset_binary(asset_id: str) -> None:
    """DesignAsset'in ikili verisini (image_data) dogrudan temizler; gercek
    purge cron'unun (bkz. app.services.design_assets.purge_expired_design_assets)
    ikili veriyi sildigi ama metadata satirini biraktigi durumu simule eder."""

    from app.models.design_assets import DesignAsset

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            asset = await session.get(DesignAsset, uuid.UUID(asset_id))
            assert asset is not None
            asset.image_data = None
            await session.commit()
    finally:
        await engine.dispose()


async def _credit_chips(organization_id: str, amount: int) -> None:
    # Izole test veritabanina karsi, tek kullanimlik bir NullPool motoru:
    # `client` fixture'i (bkz. tests/conftest.py) istekleri TEST_DATABASE_URL'e
    # yonlendirir; bu yardimci da ayni veritabanina yazmalidir (aksi halde
    # kredi, organizasyonun gercekte bulundugu DB'den farkli bir veritabanina
    # yazilir ve testte hicbir etkisi olmaz).
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            org = await session.get(Organization, uuid.UUID(organization_id))
            assert org is not None
            await chip_ledger.credit(session, org.id, amount, "test setup credit")
            await session.commit()
    finally:
        await engine.dispose()


def _basic_ux_payload(project_id: str, *, persona_count: int = 500) -> dict:
    return {
        "project_id": project_id,
        "name": f"Anasayfa testi {uuid.uuid4().hex[:6]}",
        "target_task": "Kullanicinin sepete urun eklemesini gozlemle",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com/anasayfa",
        "persona_count": persona_count,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": [],
    }


def _create_project(client: TestClient, *, name: str | None = None) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": name or f"Proje {uuid.uuid4().hex[:8]}", "description": "Test projesi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def _png_bytes(width: int = 50, height: int = 40) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload_design_asset(client: TestClient) -> dict:
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch_draft(
    client: TestClient, draft_id: str, payload: dict, *, current_step: int | None = None
) -> dict:
    body: dict = {"payload": payload}
    if current_step is not None:
        body["current_step"] = current_step
    response = client.patch(f"/api/tests/drafts/{draft_id}", json=body, headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    return response.json()


# --- Proje yonetimi -----------------------------------------------------------


def test_create_project_lists_with_zero_test_count(client):
    _register(client)
    project = _create_project(client)

    assert project["status"] == "active"
    assert project["test_count"] == 0
    assert project["archived_at"] is None

    listing = client.get("/api/projects")
    assert listing.status_code == 200
    ids = [p["id"] for p in listing.json()]
    assert project["id"] in ids


def test_duplicate_project_name_is_rejected(client):
    _register(client)
    project = _create_project(client)

    response = client.post(
        "/api/projects",
        json={"name": project["name"]},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409


def test_archive_project_is_idempotent_and_hides_from_default_listing(client):
    _register(client)
    project = _create_project(client)

    first = client.post(f"/api/projects/{project['id']}/archive", headers=_csrf_headers(client))
    assert first.status_code == 200
    assert first.json()["status"] == "archived"
    assert first.json()["archived_at"] is not None

    # Ikinci arsivleme cagrisi hata vermemeli (idempotent) ve ayni sonucu dondurmeli.
    second = client.post(f"/api/projects/{project['id']}/archive", headers=_csrf_headers(client))
    assert second.status_code == 200
    assert second.json()["archived_at"] == first.json()["archived_at"]

    default_listing = client.get("/api/projects")
    assert project["id"] not in [p["id"] for p in default_listing.json()]

    full_listing = client.get("/api/projects?include_archived=true")
    assert project["id"] in [p["id"] for p in full_listing.json()]

    # Fiziksel silme yok: proje satiri hala GET ile erisilebilir olmali.
    detail = client.get(f"/api/projects/{project['id']}")
    assert detail.status_code == 200


def test_archived_project_cannot_be_updated(client):
    _register(client)
    project = _create_project(client)
    client.post(f"/api/projects/{project['id']}/archive", headers=_csrf_headers(client))

    response = client.patch(
        f"/api/projects/{project['id']}",
        json={"description": "yeni aciklama"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409


@pytest.mark.security
def test_project_tenant_isolation(client):
    _register(client)
    project_a = _create_project(client)
    snapshot_a = _snapshot_cookies(client)

    _register(client)  # organizasyon B'ye gecer
    response = client.get(f"/api/projects/{project_a['id']}")
    assert response.status_code == 404

    archive_response = client.post(f"/api/projects/{project_a['id']}/archive", headers=_csrf_headers(client))
    assert archive_response.status_code == 404

    _restore_cookies(client, snapshot_a)
    own_response = client.get(f"/api/projects/{project_a['id']}")
    assert own_response.status_code == 200


# --- Sihirbaz taslagi: kalicilik / gecersiz alanlar ---------------------------


def test_wizard_draft_persists_partial_payload_across_patches_and_resumes(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    assert draft["status"] == "draft"
    assert draft["current_step"] == 1

    step1 = _patch_draft(
        client,
        draft["id"],
        {
            "project_id": project["id"],
            "name": "Sepet akisi testi",
            "target_task": "Sepete urun ekleyip odemeyi tamamla",
            "test_type": "existing_site_basic_ux",
        },
        current_step=2,
    )
    assert step1["payload"]["name"] == "Sepet akisi testi"
    assert step1["current_step"] == 2

    # Sayfa yenilenmesini simule etmek icin: taslak yeniden GET edilir ve
    # onceki adimlarin verisi hala orada olmalidir (kaybolmamalidir).
    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["payload"]["target_task"] == "Sepete urun ekleyip odemeyi tamamla"
    assert reloaded.json()["current_step"] == 2

    step2 = _patch_draft(client, draft["id"], {"current_url": "https://example.com"}, current_step=3)
    assert step2["payload"]["current_url"] == "https://example.com"
    # Onceki adimin alanlari hala mevcut (birlestirme, ustune yazma degil).
    assert step2["payload"]["name"] == "Sepet akisi testi"


def test_wizard_invalid_url_syntax_is_rejected(client):
    _register(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"current_url": "not-a-valid-url"}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_persona_count_below_minimum_is_rejected(client):
    _register(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"persona_count": 99}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_persona_count_above_maximum_is_rejected(client):
    _register(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"persona_count": 50_001}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_persona_count_at_boundaries_is_accepted(client):
    _register(client)
    draft = _create_draft(client)

    low = _patch_draft(client, draft["id"], {"persona_count": 100})
    assert low["payload"]["persona_count"] == 100

    high = _patch_draft(client, draft["id"], {"persona_count": 50_000})
    assert high["payload"]["persona_count"] == 50_000


def test_wizard_ab_comparison_requires_new_url_before_launch(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"])
    payload["test_type"] = "ab_comparison"
    _patch_draft(client, draft["id"], payload)
    _patch_draft(client, draft["id"], {"authorization_confirmed": True})

    response = client.get(f"/api/tests/drafts/{draft['id']}")
    assert "new_url" in response.json()["missing_fields"]

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400


# --- Sihirbaz baslatma: ucretsiz hak / Chip / yetersiz bakiye -----------------


def test_wizard_launch_uses_free_entitlement_within_persona_limit(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"], persona_count=1000)
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    body = launch.json()
    assert body["used_free_entitlement"] is True
    assert body["reserved_chips"] == 0
    assert body["status"] == "launched"
    assert len(body["simulation_run_ids"]) == 1

    # Proje artik en az bir teste sahip olmali.
    project_after = client.get(f"/api/projects/{project['id']}")
    assert project_after.json()["test_count"] == 1


@pytest.mark.security
def test_wizard_launch_reserves_chips_for_modules_even_when_base_test_is_free(client):
    """Temel UX testi ucretsiz hakki (persona<=1000) kullanilsa bile,
    sihirbazin 4. adiminda secilen (Chip gerektiren) gelismis moduller
    AYRICA rezerve edilmelidir - aksi halde modul secimi teklifte gorunup
    hicbir Chip harcamadan calisir (bkz. app.services.test_wizard.launch_draft
    "NOT" yorumu)."""

    session = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    import anyio

    anyio.run(_credit_chips, session["organization_id"], 500)

    payload = _basic_ux_payload(project["id"], persona_count=500)
    payload["modules"] = ["network_device_test", "campaign_cta_test", "synthetic_attention_estimate"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    balance_before = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_before == 500

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    body = launch.json()

    assert body["used_free_entitlement"] is True
    # 40 (network_device_test) + 35 (campaign_cta_test) + 25 (synthetic_attention_estimate).
    assert body["reserved_chips"] == 100

    balance_after = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_after == 400


def test_wizard_launch_without_authorization_confirmation_is_rejected(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"])
    payload.pop("modules")
    _patch_draft(client, draft["id"], payload)
    # authorization_confirmed kasitli olarak gonderilmedi.

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400
    assert "authorization_confirmed" in launch.json()["detail"]


def test_wizard_launch_over_free_limit_requires_chips_and_rejects_insufficient_balance(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"], persona_count=1500)
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 402
    assert "Chip" in launch.json()["detail"]

    # Reddedilen baslatma denemesi hicbir kayit birakmamali; taslak hala 'draft'.
    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.json()["status"] == "draft"


def test_wizard_launch_over_free_limit_succeeds_with_sufficient_chips(client):
    import anyio

    session = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"], persona_count=1500)
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    anyio.run(_credit_chips, session["organization_id"], 5000)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    body = launch.json()
    assert body["used_free_entitlement"] is False
    assert body["reserved_chips"] == 1500  # 1 chip/persona (bkz. app.services.pricing)


@pytest.mark.security
def test_wizard_launch_is_idempotent_on_double_click(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"], persona_count=200)
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    headers = _csrf_headers(client)
    first = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=headers)
    second = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["test_definition_id"] == second.json()["test_definition_id"]
    assert first.json()["simulation_run_ids"] == second.json()["simulation_run_ids"]

    # Proje testi iki kez degil, yalnizca bir kez olusturulmus olmali.
    project_after = client.get(f"/api/projects/{project['id']}")
    assert project_after.json()["test_count"] == 1


def test_wizard_launched_draft_cannot_be_patched(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"])
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"name": "degistirilmis isim"}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 409


# --- Sihirbaz taslagi: kiraci (tenant) izolasyonu -----------------------------


@pytest.mark.security
def test_wizard_draft_tenant_isolation(client):
    _register(client)
    _create_project(client)
    draft_a = _create_draft(client)
    snapshot_a = _snapshot_cookies(client)

    _register(client)  # organizasyon B'ye gecer

    get_response = client.get(f"/api/tests/drafts/{draft_a['id']}")
    assert get_response.status_code == 404

    patch_response = client.patch(
        f"/api/tests/drafts/{draft_a['id']}",
        json={"payload": {"name": "baska organizasyon"}},
        headers=_csrf_headers(client),
    )
    assert patch_response.status_code == 404

    launch_response = client.post(f"/api/tests/drafts/{draft_a['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 404

    _restore_cookies(client, snapshot_a)
    own_response = client.get(f"/api/tests/drafts/{draft_a['id']}")
    assert own_response.status_code == 200


# --- Sihirbaz tasarim kaynagi (URL / ekran goruntusu) - Prompt 2 -------------


def test_wizard_missing_source_type_defaults_to_url_and_launches(client):
    """Eski taslaklarda `current_source_type` hic gonderilmemis olabilir; bu
    durumda geriye donuk uyumluluk icin URL kaynagi varsayilir ve mevcut
    URL akisi/testleri bozulmadan calismaya devam eder."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"], persona_count=200)
    payload["authorization_confirmed"] = True
    assert "current_source_type" not in payload
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text


def test_wizard_url_source_with_valid_url_is_accepted(client):
    _register(client)
    draft = _create_draft(client)

    result = _patch_draft(
        client, draft["id"], {"current_source_type": "url", "current_url": "https://example.com"}
    )
    assert result["payload"]["current_source_type"] == "url"
    assert result["payload"]["current_url"] == "https://example.com"


def test_wizard_url_source_without_url_is_rejected_at_launch(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "url"
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    missing = client.get(f"/api/tests/drafts/{draft['id']}").json()["missing_fields"]
    assert "current_url" in missing

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400


def test_wizard_screenshot_source_without_asset_id_is_rejected_at_launch(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    missing = client.get(f"/api/tests/drafts/{draft['id']}").json()["missing_fields"]
    assert "current_design_asset_id" in missing
    assert "current_url" not in missing

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400


def test_wizard_screenshot_source_with_valid_same_tenant_asset_is_saved(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    result = _patch_draft(
        client,
        draft["id"],
        {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]},
    )
    assert result["payload"]["current_source_type"] == "screenshot"
    assert result["payload"]["current_design_asset_id"] == asset["id"]


@pytest.mark.security
def test_wizard_screenshot_source_with_other_tenant_asset_is_rejected_without_leaking(client):
    _register(client)
    asset = _upload_design_asset(client)

    _register(client)  # organizasyon B'ye gecer
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "current_source_type": "screenshot",
                "current_design_asset_id": asset["id"],
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
    # Mesaj, asset'in "baska bir organizasyona ait" oldugunu SIZDIRMAMALI;
    # "yok" durumuyla aynen ayni genel mesaj kullanilmalidir.
    assert (
        "silinmis" in response.json()["detail"].lower()
        or "kullanilamiyor" in response.json()["detail"].lower()
    )
    assert asset["id"] not in response.json()["detail"]


def test_wizard_screenshot_source_with_deleted_asset_is_rejected(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    delete_response = client.delete(f"/api/design-assets/{asset['id']}", headers=_csrf_headers(client))
    assert delete_response.status_code == 204

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "current_source_type": "screenshot",
                "current_design_asset_id": asset["id"],
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_screenshot_source_with_expired_asset_is_rejected(client):
    import anyio

    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    anyio.run(_expire_design_asset, asset["id"])

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "current_source_type": "screenshot",
                "current_design_asset_id": asset["id"],
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_screenshot_source_with_purged_asset_is_rejected(client):
    import anyio

    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    anyio.run(_purge_design_asset_binary, asset["id"])

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "current_source_type": "screenshot",
                "current_design_asset_id": asset["id"],
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_accessibility_precheck_rejects_screenshot_source(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    _patch_draft(client, draft["id"], {"test_type": "accessibility_precheck"})

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "current_source_type": "screenshot",
                "current_design_asset_id": asset["id"],
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
    assert "URL" in response.json()["detail"]


def test_wizard_ab_comparison_url_url_still_launches(client):
    """URL/URL A/B akisi bu paketten sonra da bozulmadan calismaya devam etmeli."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_ux_payload(project["id"])
    payload["test_type"] = "ab_comparison"
    payload["new_url"] = "https://www.example.com"
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2


# --- Paket 3A: A/B karsilastirmasinda gorsel kaynaklar (URL/Screenshot kombinasyonlari) --


def _ab_payload(project_id: str, *, persona_count: int = 500) -> dict:
    payload = _basic_ux_payload(project_id, persona_count=persona_count)
    payload["test_type"] = "ab_comparison"
    payload["new_url"] = "https://www.example.com"
    return payload


def test_wizard_ab_url_screenshot_combination_launch_succeeds(client):
    """Paket 4 Final: gorsel (URL disi) kaynaklarin launch engeli kaldirildi -
    A/B'nin "Tasarim B" tarafi ekran goruntusu oldugunda test artik basariyla
    baslatilir (iki SimulationRun, birbirinden bagimsiz PageAnalysis'e bagli)."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    result = _patch_draft(client, draft["id"], payload)
    assert result["payload"]["new_source_type"] == "screenshot"
    assert result["payload"]["new_design_asset_id"] == asset["id"]

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2


def test_wizard_ab_screenshot_url_combination_launch_succeeds(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    result = _patch_draft(client, draft["id"], payload)
    assert result["payload"]["current_source_type"] == "screenshot"
    assert result["payload"]["current_design_asset_id"] == asset["id"]

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2


def test_wizard_ab_screenshot_screenshot_combination_launch_succeeds(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset_a = _upload_design_asset(client)
    asset_b = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["current_url"]
    del payload["new_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset_a["id"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset_b["id"]
    payload["authorization_confirmed"] = True
    result = _patch_draft(client, draft["id"], payload)
    assert result["payload"]["current_design_asset_id"] == asset_a["id"]
    assert result["payload"]["new_design_asset_id"] == asset_b["id"]

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2

    project_after = client.get(f"/api/projects/{project['id']}")
    assert project_after.json()["test_count"] == 1
    all_runs = client.get("/api/simulations/runs")
    assert len(all_runs.json()) == 2
    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.json()["status"] == "launched"


def test_wizard_ab_screenshot_screenshot_same_asset_on_both_sides_launch_succeeds(client):
    """Ayni DesignAsset her iki tarafta da referans verilse bile HER VARYANT
    icin AYRI bir PageAnalysis satiri olusturulur (provenance karismaz)."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["current_url"]
    del payload["new_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    run_ids = launch.json()["simulation_run_ids"]
    assert len(run_ids) == 2
    assert len(set(run_ids)) == 2


def test_wizard_ai_generated_side_launch_succeeds(client):
    """Provider `none` oldugu icin gercek uretim tetiklenmez - ama daha once
    kabul edilmis (SUCCEEDED, `result_asset_id` eslesen) bir AI sonucuyla
    launch edilebilir olmali (bkz. Paket 4 Final kapsami)."""

    import anyio

    from app.models.design_generation import DesignGenerationJob, DesignGenerationStatus
    from app.models.tenancy import Organization
    from app.services import design_assets as design_assets_service_module

    async def _store_accepted_generation(organization_id: str) -> tuple[str, str]:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                org = await session.get(Organization, uuid.UUID(organization_id))
                assert org is not None
                asset = await design_assets_service_module.store_generated_asset(
                    session, organization_id=org.id, raw_bytes=_png_bytes()
                )
                job = DesignGenerationJob(
                    organization_id=org.id,
                    created_by_user_id=None,
                    source_asset_id=asset.id,
                    status=DesignGenerationStatus.SUCCEEDED,
                    prompt="test prompt",
                    provider="none",
                    model_name="unspecified",
                    result_asset_id=asset.id,
                )
                session.add(job)
                await session.commit()
                return str(asset.id), str(job.id)
        finally:
            await engine.dispose()

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    org_id = client.get("/api/billing/usage-summary").json()["organization_id"]
    asset_id, job_id = anyio.run(_store_accepted_generation, org_id)

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "ai_generated"
    payload["new_design_asset_id"] = asset_id
    payload["new_ai_generation_id"] = job_id
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2


def test_wizard_screenshot_asset_deleted_between_patch_and_launch_is_rejected_with_no_side_effects(client):
    """PATCH aninda gecerli olan bir asset, launch'tan HEMEN once silinebilir -
    launch, create-time/PATCH-time kontrolune KOR guvenmeyip yeniden dogrular
    (bkz. `_revalidate_launch_sources`); reddedilen baslatma hicbir
    TestDefinition/SimulationRun/Chip rezervasyonu birakmamali."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_ux_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    delete_response = client.delete(f"/api/design-assets/{asset['id']}", headers=_csrf_headers(client))
    assert delete_response.status_code == 204

    balance_before = client.get("/api/billing/usage-summary").json()["chip_balance"]
    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400

    project_after = client.get(f"/api/projects/{project['id']}")
    assert project_after.json()["test_count"] == 0
    all_runs = client.get("/api/simulations/runs")
    assert all_runs.json() == []
    balance_after = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_after == balance_before
    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.json()["status"] == "draft"


def test_wizard_ab_new_side_screenshot_without_asset_id_is_reported_missing(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    missing = client.get(f"/api/tests/drafts/{draft['id']}").json()["missing_fields"]
    assert "new_design_asset_id" in missing
    assert "new_url" not in missing

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400


@pytest.mark.security
def test_wizard_ab_new_side_screenshot_with_other_tenant_asset_is_rejected_without_leaking(client):
    _register(client)
    asset = _upload_design_asset(client)

    _register(client)  # organizasyon B'ye gecer
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": payload},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "silinmis" in detail or "kullanilamiyor" in detail
    assert asset["id"] not in response.json()["detail"]


def test_wizard_ab_new_side_screenshot_with_expired_asset_is_rejected(client):
    import anyio

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    anyio.run(_expire_design_asset, asset["id"])

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": payload},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_ab_new_side_screenshot_with_deleted_asset_is_rejected(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    delete_response = client.delete(f"/api/design-assets/{asset['id']}", headers=_csrf_headers(client))
    assert delete_response.status_code == 204

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": payload},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_ab_new_side_screenshot_with_purged_asset_is_rejected(client):
    import anyio

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    anyio.run(_purge_design_asset_binary, asset["id"])

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": payload},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_ab_same_asset_referenced_on_both_sides_is_allowed(client):
    """Ayni DesignAsset'in her iki tarafta da referans verilmesi engellenmez
    (dedupe kurali belirtilmedi); iki alan da bagimsiz sekilde ayni asset id'yi
    tasiyabilir."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["current_url"]
    del payload["new_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]

    result = _patch_draft(client, draft["id"], payload)
    assert result["payload"]["current_design_asset_id"] == asset["id"]
    assert result["payload"]["new_design_asset_id"] == asset["id"]


def test_wizard_ab_visual_source_hydrates_after_reload(client):
    """Autosave/hydration: gorsel kaynakli A/B secimi sayfa yenilendikten
    (taslagin yeniden GET edilmesinden) sonra da korunmalidir."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["payload"]["new_source_type"] == "screenshot"
    assert reloaded.json()["payload"]["new_design_asset_id"] == asset["id"]


def test_wizard_screenshot_launch_succeeds_by_backend(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_ux_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 1

    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.json()["status"] == "launched"


def test_wizard_screenshot_launch_creates_test_definition_and_run(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_ux_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))

    project_after = client.get(f"/api/projects/{project['id']}")
    assert project_after.json()["test_count"] == 1

    all_runs = client.get("/api/simulations/runs")
    assert all_runs.status_code == 200
    assert len(all_runs.json()) == 1
    assert all_runs.json()[0]["status"] == "queued"


def test_wizard_screenshot_launch_over_free_limit_reserves_chips(client):
    import anyio

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    org_id = client.get("/api/billing/usage-summary").json()["organization_id"]
    anyio.run(_credit_chips, org_id, 100_000)

    payload = _basic_ux_payload(project["id"], persona_count=1500)
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    balance_before = client.get("/api/billing/usage-summary").json()["chip_balance"]

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert launch.json()["reserved_chips"] > 0

    balance_after = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_after == balance_before - launch.json()["reserved_chips"]


def test_wizard_screenshot_launch_consumes_free_entitlement_within_persona_limit(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_ux_payload(project["id"], persona_count=200)
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert launch.json()["used_free_entitlement"] is True

    # Ucretsiz hak artik bu organizasyonda tuketilmis olmali: ayni persona
    # sayisiyla ikinci (URL kaynakli) bir test artik ucretsiz hakki KULLANAMAZ
    # ve Chip bakiyesi olmadigi icin 402 doner (bkz. quotes servisi - bir
    # organizasyon icin temel UX testi ucretsiz hakki tek seferliktir).
    payload_url = _basic_ux_payload(project["id"], persona_count=200)
    payload_url["authorization_confirmed"] = True
    payload_url["current_source_type"] = "url"
    draft2 = _create_draft(client)
    _patch_draft(client, draft2["id"], payload_url)
    launch2 = client.post(f"/api/tests/drafts/{draft2['id']}/launch", headers=_csrf_headers(client))
    assert launch2.status_code == 402, launch2.text


# --- Kullanici CTA onayi (Paket 4C+4D) ----------------------------------------


def _cta_annotation(
    asset_id: str,
    *,
    x: float = 0.1,
    y: float = 0.2,
    w: float = 0.3,
    h: float = 0.1,
    selection_source: str = "manual_box",
    source_candidate_index: int | None = None,
) -> dict:
    value: dict = {
        "design_asset_id": asset_id,
        "box": {"x": x, "y": y, "w": w, "h": h},
        "selection_source": selection_source,
    }
    if source_candidate_index is not None:
        value["source_candidate_index"] = source_candidate_index
    return value


def test_wizard_cta_annotation_manual_box_is_saved(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    result = _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset["id"])})
    saved = result["payload"]["current_cta_annotation"]
    assert saved["design_asset_id"] == asset["id"]
    assert saved["selection_source"] == "manual_box"
    assert saved["box"] == {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.1}
    assert saved["verified_content_sha256"], "sunucu tarafinda hesaplanmis hash bulunmali"


def test_wizard_cta_annotation_candidate_confirmation_is_saved(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    result = _patch_draft(
        client,
        draft["id"],
        {
            "current_cta_annotation": _cta_annotation(
                asset["id"], selection_source="candidate_confirmation", source_candidate_index=2
            )
        },
    )
    saved = result["payload"]["current_cta_annotation"]
    assert saved["selection_source"] == "candidate_confirmation"
    assert saved["source_candidate_index"] == 2


def test_wizard_cta_annotation_client_provided_hash_is_ignored(client):
    """`verified_content_sha256` client'tan kabul edilmez - sunucu HER ZAMAN
    kendi hesapladigi degeri yazar, gonderilen sahte deger sessizce yok sayilir."""

    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    raw = _cta_annotation(asset["id"])
    raw["verified_content_sha256"] = "0" * 64  # sahte/uydurma deger
    result = _patch_draft(client, draft["id"], {"current_cta_annotation": raw})
    assert result["payload"]["current_cta_annotation"]["verified_content_sha256"] != "0" * 64


@pytest.mark.parametrize(
    "bad_box",
    [
        {"x": 1.5, "y": 0.1, "w": 0.2, "h": 0.2},
        {"x": -0.1, "y": 0.1, "w": 0.2, "h": 0.2},
        {"x": 0.1, "y": 0.1, "w": 0.0, "h": 0.0},
    ],
)
def test_wizard_cta_annotation_rejects_invalid_box(client, bad_box):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    annotation = _cta_annotation(asset["id"])
    annotation["box"] = bad_box
    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"current_cta_annotation": annotation}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "bad_box",
    [
        {"x": float("nan"), "y": 0.1, "w": 0.2, "h": 0.2},
        {"x": 0.1, "y": float("inf"), "w": 0.2, "h": 0.2},
        {"x": 0.1, "y": float("-inf"), "w": 0.2, "h": 0.2},
    ],
)
def test_wizard_cta_annotation_rejects_nan_and_infinity_box_at_service_layer(bad_box):
    """NaN/Infinity, standart JSON uzerinden HTTP ile taşınamaz (httpx/tarayici
    JSON.stringify bunlari serilestiremez) - bu yuzden bu guard, servis
    fonksiyonu dogrudan cagrilarak (HTTP katmanini atlayarak) test edilir."""

    from app.services import test_wizard as wizard_service

    annotation = _cta_annotation("11111111-1111-1111-1111-111111111111")
    annotation["box"] = bad_box
    with pytest.raises(wizard_service.DraftValidationError):
        wizard_service.validate_patch_fields({"current_cta_annotation": annotation})


def test_wizard_cta_annotation_large_area_produces_warning_not_rejection(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    annotation = _cta_annotation(asset["id"], x=0.0, y=0.0, w=1.0, h=1.0)
    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"current_cta_annotation": annotation}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert "cta_annotation_covers_full_image" in response.json()["warnings"]
    assert response.json()["payload"]["current_cta_annotation"] is not None


def test_wizard_cta_annotation_design_asset_id_mismatch_with_slot_is_rejected(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    other_asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"current_cta_annotation": _cta_annotation(other_asset["id"])}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


@pytest.mark.security
def test_wizard_cta_annotation_cross_tenant_asset_is_rejected_without_leaking(client):
    _register(client)
    asset = _upload_design_asset(client)

    _register(client)  # organizasyon B'ye gecer
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": None})
    # `current_design_asset_id` bu tenant'ta hicbir zaman set edilmedigi icin
    # annotation'in kendi design_asset_id'si zaten slot'la eslesmeyecek - bu
    # yuzden onceki testten farkli bir senaryo: annotation'in asset_field
    # eslesmesi icin ONCE slot'u ayni (var olmayan/baska tenant) id ile
    # "eslesiyor gibi" ayarlayamayiz (PATCH sekil dogrulamasi UUID ister,
    # ownership kontrolu ayrica yapilir) - dogrudan mismatch/ownership'in
    # HANGISININ once tetiklendigini degil, sonucta 400 ve sizinti olmadigini
    # dogrulariz.
    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "current_source_type": "screenshot",
                "current_design_asset_id": asset["id"],
                "current_cta_annotation": _cta_annotation(asset["id"]),
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
    assert asset["id"] not in response.json()["detail"]


def test_wizard_cta_annotation_expired_asset_is_rejected(client):
    import anyio

    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    anyio.run(_expire_design_asset, asset["id"])

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"current_cta_annotation": _cta_annotation(asset["id"])}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_cta_annotation_deleted_asset_is_rejected(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )

    delete_response = client.delete(f"/api/design-assets/{asset['id']}", headers=_csrf_headers(client))
    assert delete_response.status_code == 204

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"current_cta_annotation": _cta_annotation(asset["id"])}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_cta_annotation_cleared_when_slot_asset_changes(client):
    """Slot'un design_asset_id'si degisince o slot'un ESKI annotation'i
    otomatik olarak temizlenir - diger slot etkilenmez (bkz. plan §4)."""

    _register(client)
    draft = _create_draft(client)
    asset_a = _upload_design_asset(client)
    asset_b = _upload_design_asset(client)

    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset_a["id"]}
    )
    _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset_a["id"])})

    result = _patch_draft(client, draft["id"], {"current_design_asset_id": asset_b["id"]})
    assert result["payload"]["current_cta_annotation"] is None
    assert result["payload"]["current_design_asset_id"] == asset_b["id"]


async def _corrupt_cta_annotation_hash(draft_id: str, slot: str) -> None:
    """Draft'in kaydedilmis annotation'inin `verified_content_sha256`sini
    dogrudan DB'de bozarak, PATCH-time dogrulamadan gecmis ama sonradan
    (ör. veri gocu/legacy senaryo) gecersizlesmis bir CTA onayi durumunu
    simule eder - asset'in kendisi hala tamamen kullanilabilir kalir, bu
    yuzden launch'in KAYNAK dogrulamasi (`_revalidate_launch_sources`) BASARILI
    olur ama CTA-ozel dogrulama (`revalidate_cta_annotation`) basarisiz olur."""

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            from app.models.test_wizard import TestWizardDraft

            draft = await session.get(TestWizardDraft, uuid.UUID(draft_id))
            assert draft is not None
            payload = dict(draft.payload)
            payload[f"{slot}_cta_annotation"] = {
                **payload[f"{slot}_cta_annotation"],
                "verified_content_sha256": "0" * 64,
            }
            draft.payload = payload
            await session.commit()
    finally:
        await engine.dispose()


def test_wizard_stale_cta_annotation_is_cleared_with_explicit_warning_at_launch(client):
    """Launch aninda gecersizlesen bir CTA onayi artik SESSIZCE temizlenmez -
    launch yine de basarili olur (annotation zorunlu degil) AMA response'ta
    dogru tarafa (`slot`) bagli acik bir uyari doner."""

    import anyio

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_ux_payload(project["id"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)
    _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset["id"])})

    anyio.run(_corrupt_cta_annotation_hash, draft["id"], "current")

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    body = launch.json()
    assert len(body["warnings"]) == 1
    assert body["warnings"][0]["code"] == "stale_cta_annotation_cleared"
    assert body["warnings"][0]["slot"] == "current"
    assert "CTA" in body["warnings"][0]["message"]

    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.json()["payload"]["current_cta_annotation"] is None


def test_wizard_stale_cta_annotation_warning_attributed_to_correct_ab_side(client):
    """A/B'de yalnizca 'new' tarafinin annotation'i stale ise, uyari SADECE
    'new' slot'una atfedilir - 'current' tarafa yanlislikla yazilmaz."""

    import anyio

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    asset_current = _upload_design_asset(client)
    asset_new = _upload_design_asset(client)

    payload = _ab_payload(project["id"])
    del payload["current_url"]
    del payload["new_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset_current["id"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset_new["id"]
    payload["authorization_confirmed"] = True
    _patch_draft(client, draft["id"], payload)
    _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset_current["id"])})
    _patch_draft(client, draft["id"], {"new_cta_annotation": _cta_annotation(asset_new["id"])})

    anyio.run(_corrupt_cta_annotation_hash, draft["id"], "new")

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    body = launch.json()
    assert len(body["warnings"]) == 1
    assert body["warnings"][0]["slot"] == "new"

    reloaded = client.get(f"/api/tests/drafts/{draft['id']}").json()
    assert reloaded["payload"]["new_cta_annotation"] is None
    assert reloaded["payload"]["current_cta_annotation"] is not None


def test_wizard_cta_annotation_slots_are_isolated_between_current_and_new(client):
    """Design A (current) ve Design B (new) tarafindaki annotation'lar
    birbirinden bagimsiz saklanir ve birbirini etkilemez."""

    _register(client)
    draft = _create_draft(client)
    asset_a = _upload_design_asset(client)
    asset_b = _upload_design_asset(client)

    _patch_draft(
        client,
        draft["id"],
        {
            "test_type": "ab_comparison",
            "current_source_type": "screenshot",
            "current_design_asset_id": asset_a["id"],
            "new_source_type": "screenshot",
            "new_design_asset_id": asset_b["id"],
        },
    )
    _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset_a["id"])})
    result = _patch_draft(client, draft["id"], {"new_cta_annotation": _cta_annotation(asset_b["id"])})

    assert result["payload"]["current_cta_annotation"]["design_asset_id"] == asset_a["id"]
    assert result["payload"]["new_cta_annotation"]["design_asset_id"] == asset_b["id"]

    # Yalnizca "new" tarafinin asset'ini degistir - "current" annotation'i
    # ETKILENMEMELI.
    result2 = _patch_draft(client, draft["id"], {"new_design_asset_id": asset_a["id"]})
    assert result2["payload"]["current_cta_annotation"] is not None
    assert result2["payload"]["new_cta_annotation"] is None


def test_wizard_cta_annotation_explicit_value_in_same_patch_overrides_auto_clear(client):
    """Ayni PATCH cagrisinda hem yeni design_asset_id hem de o yeni asset icin
    annotation gonderilirse, istemcinin acikca verdigi deger ONCELIKLIDIR -
    otomatik temizleme bunun uzerine yazmaz."""

    _register(client)
    draft = _create_draft(client)
    asset_a = _upload_design_asset(client)
    asset_b = _upload_design_asset(client)

    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset_a["id"]}
    )
    _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset_a["id"])})

    result = _patch_draft(
        client,
        draft["id"],
        {
            "current_design_asset_id": asset_b["id"],
            "current_cta_annotation": _cta_annotation(asset_b["id"]),
        },
    )
    saved = result["payload"]["current_cta_annotation"]
    assert saved is not None
    assert saved["design_asset_id"] == asset_b["id"]


def test_wizard_cta_annotation_explicit_null_clears_selection(client):
    _register(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)
    _patch_draft(
        client, draft["id"], {"current_source_type": "screenshot", "current_design_asset_id": asset["id"]}
    )
    _patch_draft(client, draft["id"], {"current_cta_annotation": _cta_annotation(asset["id"])})

    result = _patch_draft(client, draft["id"], {"current_cta_annotation": None})
    assert result["payload"]["current_cta_annotation"] is None


def test_wizard_cta_annotation_on_ai_generated_asset_behaves_like_normal_asset(client):
    """AI ile uretilip kabul edilmis bir DesignAsset, annotation dogrulamasi
    acisindan sirf provenance'i farkli oldugu icin ozel muamele GORMEZ - ayni
    sahiplik/kullanilabilirlik/hash kurallariyla islenir."""

    import anyio

    from app.models.tenancy import Organization
    from app.services import design_assets as design_assets_service_module

    async def _store_generated(organization_id: str) -> str:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                org = await session.get(Organization, uuid.UUID(organization_id))
                assert org is not None
                asset = await design_assets_service_module.store_generated_asset(
                    session,
                    organization_id=org.id,
                    raw_bytes=_png_bytes(),
                )
                await session.commit()
                return str(asset.id)
        finally:
            await engine.dispose()

    _register(client)
    draft = _create_draft(client)
    org_id = client.get("/api/billing/usage-summary").json()["organization_id"]
    generated_asset_id = anyio.run(_store_generated, org_id)

    _patch_draft(
        client,
        draft["id"],
        {"current_source_type": "screenshot", "current_design_asset_id": generated_asset_id},
    )
    result = _patch_draft(
        client, draft["id"], {"current_cta_annotation": _cta_annotation(generated_asset_id)}
    )
    saved = result["payload"]["current_cta_annotation"]
    assert saved["design_asset_id"] == generated_asset_id
    assert saved["verified_content_sha256"]
