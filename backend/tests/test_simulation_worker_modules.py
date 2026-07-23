"""Sihirbazda secilen gelismis modullerin (network_device_test, campaign_cta_test,
synthetic_attention_estimate) `app.services.simulation_worker.process_run` icinde
gercekten islendigini, DB'ye yazildigini ve Chip/idempotency kurallarinin
korundugunu dogrulayan testler.

`session`/`organization` fixture'lari + `_make_queued_run` deseni
`test_simulation_engine.py` ile aynidir (bkz. o dosyanin dokstring'i);
`network_device_test` icin gercek bir analyzer/ag cagrisi yapilmaz -
`app.services.device_network_analysis.run_network_device_test`
monkeypatch ile sahtelenir.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import EntitlementStatus
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.services import chip_ledger, device_network_analysis, entitlements, simulation_worker
from app.services.exceptions import ModuleProcessingError
from app.services.pricing import FEATURE_BASIC_UX_TEST

pytestmark = pytest.mark.integration


async def _make_variant(session: AsyncSession, organization: Organization):
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
        config={"role": "primary", "url": "https://example.com/kampanya"},
    )
    session.add(variant)
    await session.flush()
    return variant


def _input_snapshot(*, modules: list[str]) -> dict:
    return {
        "wizard_test_type": "existing_site_basic_ux",
        "persona_count": 500,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": modules,
        "url": "https://example.com/kampanya",
        "role": "primary",
        "pricing_version": "2026.2",
    }


async def _make_queued_run(
    session: AsyncSession,
    organization: Organization,
    *,
    modules: list[str],
    launch_run_id: uuid.UUID | None = None,
    chip_reservation_id: uuid.UUID | None = None,
    free_entitlement_feature_key: str | None = None,
) -> SimulationRun:
    variant = await _make_variant(session, organization)
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.QUEUED,
        deterministic_seed=42,
        model_version="pending-engine",
        input_snapshot=_input_snapshot(modules=modules),
        launch_run_id=launch_run_id,
        chip_reservation_id=chip_reservation_id,
        free_entitlement_feature_key=free_entitlement_feature_key,
    )
    session.add(run)
    await session.flush()
    return run


def _fake_network_device_result() -> dict:
    return {
        "module_key": "network_device_test",
        "analyzer_version": "device-network-analyzer-2026.1",
        "url": "https://example.com/kampanya",
        "profiles": [
            {
                "profile_key": "desktop_broadband",
                "device_label": "Masaustu",
                "network_label": "Genis bant",
                "succeeded": True,
                "error": None,
                "timings": {
                    "dom_content_loaded_ms": 100.0,
                    "load_event_ms": 150.0,
                    "total_navigation_ms": 150.0,
                },
                "accessibility_violation_count": 0,
            }
        ],
        "error_rate": 0.0,
        "warnings": [],
        "disclaimer": "gercek teknik olcum, gercek kullanici deneyimi degildir",
    }


# --- Sentetik modul sonuclari DB'ye yaziliyor ---------------------------------


async def test_campaign_cta_and_attention_modules_are_persisted_in_result(
    session: AsyncSession, organization: Organization
):
    run = await _make_queued_run(
        session, organization, modules=["campaign_cta_test", "synthetic_attention_estimate"]
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.SUCCEEDED
    assert run.result is not None
    assert "campaign_cta_test" in run.result["modules"]
    assert "synthetic_attention_estimate" in run.result["modules"]
    # Mevcut "Sentetik dikkat tahmini" ısı haritasi bolumu bu alani okur
    # (bkz. app.routers.reports._build_heatmap).
    assert run.result["attention_grid"] == run.result["modules"]["synthetic_attention_estimate"]["grid"]


async def test_no_modules_selected_produces_no_modules_key(session: AsyncSession, organization: Organization):
    run = await _make_queued_run(session, organization, modules=[])
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.SUCCEEDED
    assert "modules" not in run.result
    assert "attention_grid" not in run.result


# --- network_device_test: gercek analyzer cagrisi sahtelenir -----------------


async def test_network_device_test_module_is_persisted(
    session: AsyncSession, organization: Organization, monkeypatch: pytest.MonkeyPatch
):
    async def _fake_run_network_device_test(url: str) -> dict:
        assert url == "https://example.com/kampanya"
        return _fake_network_device_result()

    monkeypatch.setattr(device_network_analysis, "run_network_device_test", _fake_run_network_device_test)

    run = await _make_queued_run(session, organization, modules=["network_device_test"])
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.SUCCEEDED
    assert run.result["modules"]["network_device_test"]["error_rate"] == 0.0


# --- Hata durumunda Chip haksiz dusulmez --------------------------------------


@pytest.mark.security
async def test_module_processing_failure_releases_chip_reservation_without_charge(
    session: AsyncSession, organization: Organization, monkeypatch: pytest.MonkeyPatch
):
    async def _failing_run_network_device_test(url: str) -> dict:
        raise ModuleProcessingError("analyzer'a ulasilamadi (test)")

    monkeypatch.setattr(device_network_analysis, "run_network_device_test", _failing_run_network_device_test)

    await chip_ledger.credit(session, organization.id, 200, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 140, "test reserve", run_id=launch_run_id
    )

    run = await _make_queued_run(
        session,
        organization,
        modules=["network_device_test"],
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.FAILED
    assert run.result is None
    # Chip HAKSIZ YERE DUSULMEDI: rezervasyon tamamen serbest birakildi.
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200

    # Idempotency: grup cozumlemesini tekrar cagirmak (ör. reap sonrasi
    # tekrar finalize edilme senaryosu) bakiyeyi tekrar etkilemez.
    await simulation_worker._resolve_launch_group(session, run)
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


@pytest.mark.security
async def test_retry_after_module_failure_does_not_double_charge(
    session: AsyncSession, organization: Organization, monkeypatch: pytest.MonkeyPatch
):
    """Bir modul basarisiz olup rezervasyon serbest birakildiktan sonra retry
    edilip (rezervasyon yeniden yapilip) basarili tamamlandiginda, Chip
    yalnizca BIR KEZ tuketilir (cift tuketim yok)."""

    call_count = {"n": 0}

    async def _flaky_run_network_device_test(url: str) -> dict:
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ModuleProcessingError("gecici analyzer hatasi")
        return _fake_network_device_result()

    monkeypatch.setattr(device_network_analysis, "run_network_device_test", _flaky_run_network_device_test)

    await chip_ledger.credit(session, organization.id, 300, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 100, "test reserve", run_id=launch_run_id
    )

    run = await _make_queued_run(
        session,
        organization,
        modules=["network_device_test"],
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
    )
    run.status = SimulationStatus.RUNNING
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.FAILED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 300

    await simulation_worker.retry_run(session, organization.id, run.id)
    assert run.status == SimulationStatus.QUEUED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200

    run.status = SimulationStatus.RUNNING
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.SUCCEEDED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


@pytest.mark.security
async def test_free_entitlement_and_chip_reservation_are_both_consumed_when_combined(
    session: AsyncSession, organization: Organization
):
    """Bir run hem temel testin ucretsiz hakkini HEM DE (secili gelismis
    moduller icin) bir Chip rezervasyonunu ayni anda tasiyabilir (bkz.
    app.services.test_wizard.launch_draft). Basarili tamamlandiginda
    IKISI DE tuketilmelidir - yalnizca biri degil (regresyon: onceki 'elif'
    mantigi ikinci rezervasyonu sessizce hic tuketmiyordu/serbest
    birakmiyordu)."""

    launch_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, launch_run_id)

    await chip_ledger.credit(session, organization.id, 100, "test credit")
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 60, "test reserve (modules)", run_id=launch_run_id
    )
    assert await chip_ledger.get_chip_balance(session, organization.id) == 40

    run = await _make_queued_run(
        session,
        organization,
        modules=["campaign_cta_test"],
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
        free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.SUCCEEDED
    entitlement = await entitlements.get_or_create_entitlement(
        session, organization.id, FEATURE_BASIC_UX_TEST
    )
    assert entitlement.status == EntitlementStatus.CONSUMED
    # Chip rezervasyonu da tuketildi (bakiye degismedi, yalnizca rezerve
    # edilen 60 Chip artik "harcanmis" sayilir - rezervasyon sirasinda zaten
    # bakiyeden dusulmustu).
    assert await chip_ledger.get_chip_balance(session, organization.id) == 40


@pytest.mark.security
async def test_free_entitlement_and_chip_reservation_are_both_released_on_failure(
    session: AsyncSession, organization: Organization, monkeypatch: pytest.MonkeyPatch
):
    async def _failing_run_network_device_test(url: str) -> dict:
        raise ModuleProcessingError("test hatasi")

    monkeypatch.setattr(device_network_analysis, "run_network_device_test", _failing_run_network_device_test)

    launch_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, launch_run_id)

    await chip_ledger.credit(session, organization.id, 100, "test credit")
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 60, "test reserve (modules)", run_id=launch_run_id
    )

    run = await _make_queued_run(
        session,
        organization,
        modules=["network_device_test"],
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
        free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.FAILED
    entitlement = await entitlements.get_or_create_entitlement(
        session, organization.id, FEATURE_BASIC_UX_TEST
    )
    assert entitlement.status == EntitlementStatus.AVAILABLE
    # Chip rezervasyonu da serbest birakildi: bakiye tam olarak iade edildi.
    assert await chip_ledger.get_chip_balance(session, organization.id) == 100
