"""Proje yonetimi ve yeni test kurulum sihirbazi (5 adim) icin uctan uca testler.

`client` fixture'i tests/conftest.py'den gelir: her test kendi izole test
veritabanina yonlendirilmis, taze bir `TestClient` alir (bkz. conftest.py
docstring'i). Birden fazla kimlik gerektiginde cookie jar'i anlik
goruntulenip/degistirilir.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
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
