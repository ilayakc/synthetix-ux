"""`network_device_test`in yalnizca gercek URL kaynaklariyla calisabildigini
dogrulayan testler (bkz. app.services.test_wizard.validate_module_source_compatibility
ve app.services.module_catalog.AnalysisModuleDefinition.supported_source_types).

Kok neden: ekran goruntusu/AI kaynagi statik bir gorseldir - `network_device_test`
gercek bir URL'ye HTTP istegi atarak (analyzer uzerinden) sayfa yukleme/ag
olcumu yaptigi icin bu kaynak turleriyle YAPISAL olarak calisamaz. Eskiden bu
uyumsuzluk yalnizca worker asamasinda (`input_snapshot.url gereklidir`) ortaya
cikiyordu; bu dosya artik launch-oncesi (sifir side effect) reddedildigini,
ayrica bu hatayla zaten FAILED olmus eski run'larin `retryable=False` olarak
raporlandigini ve retry endpoint'inin bunlari reddettigini dogrular.

`client`/`session`/`organization` fixture'lari tests/conftest.py'dendir.
"""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.services import chip_ledger, simulation_worker
from app.services.exceptions import InvalidSimulationStateError
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


def _patch_draft(client: TestClient, draft_id: str, payload: dict) -> dict:
    response = client.patch(
        f"/api/tests/drafts/{draft_id}",
        json={"payload": payload},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
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


async def _credit_chips(organization_id: str, amount: int) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            org = await session.get(Organization, uuid.UUID(organization_id))
            assert org is not None
            await chip_ledger.credit(session, org.id, amount, "test setup credit")
            await session.commit()
    finally:
        await engine.dispose()


def _basic_payload(project_id: str, *, persona_count: int = 500, modules: list[str] | None = None) -> dict:
    return {
        "project_id": project_id,
        "name": f"Anasayfa testi {uuid.uuid4().hex[:6]}",
        "target_task": "Kullanicinin sepete urun eklemesini gozlemle",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com/anasayfa",
        "persona_count": persona_count,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": modules or [],
        "authorization_confirmed": True,
    }


def _ab_payload(project_id: str, *, persona_count: int = 500, modules: list[str] | None = None) -> dict:
    payload = _basic_payload(project_id, persona_count=persona_count, modules=modules)
    payload["test_type"] = "ab_comparison"
    payload["new_url"] = "https://www.example.com"
    return payload


REJECTION_MESSAGE_FRAGMENT = "yalnızca tüm tasarım kaynakları canlı URL"


def _credit_and_get_balance(client: TestClient, amount: int) -> str:
    org_id = client.get("/api/billing/usage-summary").json()["organization_id"]
    import anyio

    anyio.run(_credit_chips, org_id, amount)
    return org_id


# --- 1: tekli URL + network_device_test launch basarili ----------------------


def test_single_url_source_with_network_device_test_launches_successfully(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], modules=["network_device_test"])
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 1


# --- 2: tekli screenshot + network_device_test launch 400 --------------------


def test_single_screenshot_source_with_network_device_test_launch_rejected(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_payload(project["id"], modules=["network_device_test"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400
    assert REJECTION_MESSAGE_FRAGMENT in launch.json()["detail"]


# --- 3: A/B URL/URL + network_device_test basarili ----------------------------


def test_ab_url_url_with_network_device_test_launches_successfully(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _ab_payload(project["id"], modules=["network_device_test"])
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    assert len(launch.json()["simulation_run_ids"]) == 2


# --- 4-6: A/B karisik/screenshot kombinasyonlari + network_device_test 400 ---


def test_ab_url_screenshot_with_network_device_test_launch_rejected(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"], modules=["network_device_test"])
    del payload["new_url"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400
    assert REJECTION_MESSAGE_FRAGMENT in launch.json()["detail"]


def test_ab_screenshot_url_with_network_device_test_launch_rejected(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _ab_payload(project["id"], modules=["network_device_test"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400
    assert REJECTION_MESSAGE_FRAGMENT in launch.json()["detail"]


def test_ab_screenshot_screenshot_with_network_device_test_launch_rejected(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset_a = _upload_design_asset(client)
    asset_b = _upload_design_asset(client)

    payload = _ab_payload(project["id"], modules=["network_device_test"])
    del payload["current_url"]
    del payload["new_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset_a["id"]
    payload["new_source_type"] = "screenshot"
    payload["new_design_asset_id"] = asset_b["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400
    assert REJECTION_MESSAGE_FRAGMENT in launch.json()["detail"]


# --- 7-8: screenshot + campaign_cta_test / synthetic_attention_estimate basarili --


def test_screenshot_source_with_campaign_cta_test_launches_successfully(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_payload(project["id"], modules=["campaign_cta_test"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text


def test_screenshot_source_with_synthetic_attention_estimate_launches_successfully(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    payload = _basic_payload(project["id"], modules=["synthetic_attention_estimate"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text


# --- 9-12: uyumsuz launch sifir side effect birakir ---------------------------


def test_incompatible_launch_leaves_zero_side_effects(client):
    _register(client)
    _credit_and_get_balance(client, 200)
    project = _create_project(client)
    draft = _create_draft(client)
    asset = _upload_design_asset(client)

    balance_before = client.get("/api/billing/usage-summary").json()["chip_balance"]

    payload = _basic_payload(project["id"], modules=["network_device_test"])
    del payload["current_url"]
    payload["current_source_type"] = "screenshot"
    payload["current_design_asset_id"] = asset["id"]
    _patch_draft(client, draft["id"], payload)

    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 400

    # TestDefinition/PageAnalysis olusmadi (proje test sayaci sifir kaldi).
    project_after = client.get(f"/api/projects/{project['id']}")
    assert project_after.json()["test_count"] == 0

    # SimulationRun olusmadi.
    all_runs = client.get("/api/simulations/runs")
    assert all_runs.json() == []

    # Chip rezervasyonu/tuketimi olmadi (bakiye degismedi).
    balance_after = client.get("/api/billing/usage-summary").json()["chip_balance"]
    assert balance_after == balance_before

    # Ucretsiz hak rezervasyonu da olusmadi - draft hala 'draft' durumunda.
    reloaded = client.get(f"/api/tests/drafts/{draft['id']}")
    assert reloaded.json()["status"] == "draft"


# --- 13-14: eski FAILED screenshot+network run -> retryable=False, retry reddedilir --


async def _make_variant(session, organization: Organization):
    from app.models.projects import Project
    from app.models.tests import TestDefinition, TestVariant

    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()

    definition = TestDefinition(
        organization_id=organization.id,
        project_id=project.id,
        name=f"Test tanimi {uuid.uuid4().hex[:6]}",
    )
    session.add(definition)
    await session.flush()

    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Ana Senaryo",
        config={"role": "primary", "source_type": "screenshot"},
    )
    session.add(variant)
    await session.flush()
    return variant


def _legacy_failed_snapshot(*, modules: list[str], source_type: str) -> dict:
    return {
        "wizard_test_type": "existing_site_basic_ux",
        "persona_count": 500,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": modules,
        "url": None,
        "source_type": source_type,
        "role": "primary",
        "pricing_version": "2026.2",
    }


async def test_legacy_failed_screenshot_network_run_is_not_retryable(
    session, organization: Organization
):
    variant = await _make_variant(session, organization)
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.FAILED,
        deterministic_seed=1,
        model_version="pending-engine",
        input_snapshot=_legacy_failed_snapshot(modules=["network_device_test"], source_type="screenshot"),
        error="input_snapshot.url gereklidir (network_device_test)",
    )
    session.add(run)
    await session.flush()

    retryable, failure_code = simulation_worker.classify_run_failure(run)
    assert retryable is False
    assert failure_code == simulation_worker.NETWORK_DEVICE_TEST_REQUIRES_URL_CODE


async def test_legacy_failed_screenshot_network_run_retry_endpoint_rejects_without_reservation(
    session, organization: Organization
):
    variant = await _make_variant(session, organization)
    launch_run_id = uuid.uuid4()
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.FAILED,
        deterministic_seed=1,
        model_version="pending-engine",
        input_snapshot=_legacy_failed_snapshot(modules=["network_device_test"], source_type="screenshot"),
        error="input_snapshot.url gereklidir (network_device_test)",
        launch_run_id=launch_run_id,
    )
    session.add(run)
    await session.flush()

    balance_before = await chip_ledger.get_chip_balance(session, organization.id)

    with pytest.raises(InvalidSimulationStateError):
        await simulation_worker.retry_run(session, organization.id, run.id)

    balance_after = await chip_ledger.get_chip_balance(session, organization.id)
    assert balance_after == balance_before
    assert run.status == SimulationStatus.FAILED
    assert run.chip_reservation_id is None


# --- 15: gecici provider/network hatali run retryable=True kalir --------------


async def test_transient_network_device_test_failure_stays_retryable(
    session, organization: Organization
):
    variant = await _make_variant(session, organization)
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.FAILED,
        deterministic_seed=1,
        model_version="pending-engine",
        input_snapshot=_legacy_failed_snapshot(modules=["network_device_test"], source_type="url"),
        error="analyzer'a ulasilamadi (network_device_test): connection error",
    )
    session.add(run)
    await session.flush()

    retryable, failure_code = simulation_worker.classify_run_failure(run)
    assert retryable is True
    assert failure_code is None


# --- 16: cross-tenant davranis degismiyor -------------------------------------


@pytest.mark.security
def test_cross_tenant_cannot_retry_or_see_other_organizations_failed_run(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _credit_and_get_balance(client, 200)

    payload = _basic_payload(project["id"], modules=["network_device_test"])
    _patch_draft(client, draft["id"], payload)
    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    run_id = launch.json()["simulation_run_ids"][0]

    other_client = TestClient(client.app)
    try:
        _register(other_client)
        response = other_client.post(
            f"/api/simulations/runs/{run_id}/retry", headers=_csrf_headers(other_client)
        )
        assert response.status_code == 404
        get_response = other_client.get(f"/api/simulations/runs/{run_id}")
        assert get_response.status_code == 404
    finally:
        other_client.close()
