"""Sentetik simulasyon motoru: determinism, belirsizlik, durum makinesi,
idempotent retry, iptal, entitlement/Chip tuketimi ve yasak iddia testleri.

`test_engine`, `session` ve `organization` fixture'lari artik tests/conftest.py'de
paylasilan altyapidan gelir (izole test veritabani + her testte rollback).
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import baseline
from app.engine import fixtures as engine_fixtures
from app.engine.rules_config import CURRENT_RULES_VERSION
from app.models.billing import ChipReservationStatus, EntitlementStatus
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.models.tests import TestDefinition, TestVariant
from app.services import chip_ledger, entitlements, simulation_worker
from app.services.exceptions import InvalidSimulationStateError, SimulationRunNotFoundError
from app.services.pricing import FEATURE_BASIC_UX_TEST

pytestmark = pytest.mark.integration


async def _make_variant(session: AsyncSession, organization: Organization) -> TestVariant:
    definition = TestDefinition(
        organization_id=organization.id,
        project_id=uuid.uuid4(),  # bu test dosyasi projects tablosuna bagimli degildir (FK yok burada)
        name=f"Test tanimi {uuid.uuid4().hex[:6]}",
    )
    # project_id gercek bir projects satirina isaret etmeli (FK ON DELETE CASCADE);
    # bu yuzden burada dogrudan projects tablosuna basit bir satir ekleriz.
    from app.models.projects import Project

    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()
    definition.project_id = project.id
    session.add(definition)
    await session.flush()

    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Ana Senaryo",
        config={"role": "primary", "url": "https://example.com/anasayfa"},
    )
    session.add(variant)
    await session.flush()
    return variant


def _valid_input_snapshot(*, url: str = "https://example.com/anasayfa", role: str = "primary") -> dict:
    return {
        "wizard_test_type": "existing_site_basic_ux",
        "persona_count": 500,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": [],
        "url": url,
        "role": role,
        "pricing_version": "2026.1",
    }


async def _make_queued_run(
    session: AsyncSession,
    organization: Organization,
    *,
    launch_run_id: uuid.UUID | None = None,
    free_entitlement_feature_key: str | None = None,
    chip_reservation_id: uuid.UUID | None = None,
    input_snapshot: dict | None = None,
) -> SimulationRun:
    variant = await _make_variant(session, organization)
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.QUEUED,
        deterministic_seed=42,
        model_version="pending-engine",
        input_snapshot=input_snapshot or _valid_input_snapshot(),
        launch_run_id=launch_run_id,
        free_entitlement_feature_key=free_entitlement_feature_key,
        chip_reservation_id=chip_reservation_id,
    )
    session.add(run)
    await session.flush()
    return run


# --- Determinism ---------------------------------------------------------


def test_same_input_and_seed_produce_identical_result():
    snapshot = _valid_input_snapshot()
    result_1 = baseline.run_baseline_simulation(snapshot, deterministic_seed=123)
    result_2 = baseline.run_baseline_simulation(snapshot, deterministic_seed=123)
    assert result_1 == result_2


def test_different_seed_does_not_change_point_estimates():
    """Bu motor rastgele sayi uretmeyi 'model' olarak kullanmaz (bkz. modul dokstring'i):
    nokta tahminleri yalnizca kural agirliklarindan hesaplanir, seed'den degil.
    """

    snapshot = _valid_input_snapshot()
    result_a = baseline.run_baseline_simulation(snapshot, deterministic_seed=1)
    result_b = baseline.run_baseline_simulation(snapshot, deterministic_seed=999999)

    metrics_a = {k: v for k, v in result_a["metrics"].items() if k != "regional_interest"}
    metrics_b = {k: v for k, v in result_b["metrics"].items() if k != "regional_interest"}
    assert metrics_a == metrics_b
    # deterministic_seed alani provenance icin ayri ayri saklanir.
    assert result_a["deterministic_seed"] == 1
    assert result_b["deterministic_seed"] == 999999


def test_different_url_changes_result():
    result_a = baseline.run_baseline_simulation(_valid_input_snapshot(url="https://a.example.com"), 1)
    result_b = baseline.run_baseline_simulation(_valid_input_snapshot(url="https://b.example.com"), 1)
    assert result_a["metrics"] != result_b["metrics"]


def test_missing_url_raises_simulation_input_error():
    with pytest.raises(baseline.SimulationInputError):
        baseline.run_baseline_simulation({"role": "primary"}, deterministic_seed=1)


# --- Paket 4 Final Hardening: source_reference (pseudo-URL kaldirma) --------


def test_source_reference_alone_satisfies_input_requirement_without_page_feature_snapshot_fails():
    """`source_reference` TEK BASINA (page_feature_snapshot verilmeden) motoru
    tatmin ETMEZ - DesignAsset kaynagi icin gercek skorlama HER ZAMAN
    `page_feature_snapshot` gerektirir (legacy sha256(url) yer tutucusu
    yalnizca GERCEK url ile calisir); bu, motorun "bos degil" sartini
    URL-DISI bir string ile GECERSIZ bicimde atlatilamayacagini kanitlar."""

    with pytest.raises(baseline.SimulationInputError):
        baseline.run_baseline_simulation(
            {"role": "primary", "source_reference": "design-asset:11111111-1111-1111-1111-111111111111"},
            deterministic_seed=1,
        )


def test_source_reference_with_page_feature_snapshot_succeeds_and_url_is_none():
    """DesignAsset kaynagi: `page_feature_snapshot` saglandiginda `source_reference`
    tek basina yeterlidir; sonucta `url` ASLA sahte bir deger tasimaz - acikca
    `None` kalir, gercek kimlik yalnizca `source_reference` alanindadir."""

    snapshot = engine_fixtures.PageFeatureSnapshot(
        fixture_version="visual-adapter-1:test",
        url="design-asset:11111111-1111-1111-1111-111111111111",
        role="primary",
        nav_depth=0,
        primary_cta_count=1,
        form_field_count=0,
        above_fold_cta=True,
        page_word_count=0,
        avg_sentence_word_count=0.0,
        heading_count=0,
        min_contrast_ratio=6.0,
        avg_contrast_ratio=6.0,
        mobile_friendly=True,
        input_hash="b" * 64,
    )
    result = baseline.run_baseline_simulation(
        {"role": "primary", "source_reference": "design-asset:11111111-1111-1111-1111-111111111111"},
        deterministic_seed=1,
        page_feature_snapshot=snapshot,
    )
    assert result["url"] is None
    assert result["source_reference"] == "design-asset:11111111-1111-1111-1111-111111111111"


def test_visual_result_is_independent_of_source_reference_identity():
    """'Ayni visual feature verisi FARKLI asset ID ile verilirse analiz sonucu
    ayni olmalidir' (Paket 4 Final Hardening kurali) - skorlama
    `page.input_hash`den (gercek feature icerigi) turer, `source_reference`
    veya asset ID'den DEGIL."""

    def make_snapshot(source_reference: str) -> engine_fixtures.PageFeatureSnapshot:
        return engine_fixtures.PageFeatureSnapshot(
            fixture_version="visual-adapter-1:test",
            url=source_reference,
            role="primary",
            nav_depth=0,
            primary_cta_count=2,
            form_field_count=0,
            above_fold_cta=True,
            page_word_count=0,
            avg_sentence_word_count=0.0,
            heading_count=0,
            min_contrast_ratio=6.0,
            avg_contrast_ratio=6.0,
            mobile_friendly=True,
            input_hash="c" * 64,  # AYNI icerik hash'i - iki farkli "asset" ayni gorseli temsil ediyor
        )

    result_a = baseline.run_baseline_simulation(
        {"role": "primary", "source_reference": "design-asset:aaaaaaaa-0000-0000-0000-000000000000"},
        deterministic_seed=7,
        page_feature_snapshot=make_snapshot("design-asset:aaaaaaaa-0000-0000-0000-000000000000"),
    )
    result_b = baseline.run_baseline_simulation(
        {"role": "primary", "source_reference": "design-asset:bbbbbbbb-1111-1111-1111-111111111111"},
        deterministic_seed=7,
        page_feature_snapshot=make_snapshot("design-asset:bbbbbbbb-1111-1111-1111-111111111111"),
    )
    assert result_a["metrics"] == result_b["metrics"]


def test_legacy_url_only_behavior_is_byte_identical():
    """Geriye uyumluluk: yalnizca `url` verilen (Paket 4 Final Hardening
    ONCESI ile AYNI) girdi, `source_reference` alani hic gonderilmeden
    tamamen ayni sekilde calismaya devam eder."""

    result = baseline.run_baseline_simulation(_valid_input_snapshot(), deterministic_seed=42)
    assert result["url"] == "https://example.com/anasayfa"
    assert result["source_reference"] is None


# --- Belirsizlik araligi ----------------------------------------------------


def test_probability_metrics_have_sane_uncertainty_intervals():
    result = baseline.run_baseline_simulation(_valid_input_snapshot(), deterministic_seed=1)
    for key in (
        "task_completion_probability",
        "misclick_probability",
        "abandonment_probability",
    ):
        metric = result["metrics"][key]
        assert 0.0 <= metric["low"] <= metric["point_estimate"] <= metric["high"] <= 1.0
        assert metric["distribution"] == "triangular"


def test_task_duration_distribution_percentiles_are_monotonic():
    result = baseline.run_baseline_simulation(_valid_input_snapshot(), deterministic_seed=1)
    duration = result["metrics"]["task_duration_seconds"]
    assert duration["low"] <= duration["p10"] <= duration["p50"] <= duration["p90"] <= duration["high"]
    assert duration["unit"] == "seconds"


def test_contrast_check_uses_wcag_aa_threshold():
    result = baseline.run_baseline_simulation(_valid_input_snapshot(), deterministic_seed=1)
    contrast = result["metrics"]["contrast_check"]
    assert contrast["threshold"] == engine_fixtures.WCAG_AA_CONTRAST_THRESHOLD
    assert contrast["pass"] == (contrast["avg_ratio"] >= contrast["threshold"])


@pytest.mark.security
def test_result_always_reports_uncalibrated_status():
    result = baseline.run_baseline_simulation(_valid_input_snapshot(), deterministic_seed=1)
    assert result["calibration_status"] == "uncalibrated"
    assert result["rules_version"] == CURRENT_RULES_VERSION


# --- Yasak iddia metinleri ---------------------------------------------------


@pytest.mark.security
def test_disclaimer_and_comparison_note_contain_no_forbidden_claims():
    baseline.assert_no_banned_claims(baseline.RESULT_DISCLAIMER)
    baseline.assert_no_banned_claims(baseline.COMPARISON_NOTE)


@pytest.mark.security
def test_disclaimer_frames_result_as_synthetic_and_uncalibrated():
    lowered = baseline.RESULT_DISCLAIMER.lower()
    assert "sentetik" in lowered
    assert "kalibre edilmemis" in lowered or "uncalibrated" in lowered


@pytest.mark.security
@pytest.mark.parametrize(
    "phrase",
    [
        "Bu sonuclar bilimsel olarak kanitlanmistir.",
        "Bu sonuclar istatistiksel olarak anlamlidir.",
        "Bu, gercek pazar talebini gosterir.",
        "Goz takibi verileriyle dogrulanmistir.",
    ],
)
def test_assert_no_banned_claims_rejects_forbidden_phrases(phrase):
    with pytest.raises(baseline.SimulationInputError):
        baseline.assert_no_banned_claims(phrase)


@pytest.mark.security
def test_comparison_result_contains_no_significance_claim():
    result_a = baseline.run_baseline_simulation(_valid_input_snapshot(url="https://a.example.com"), 1)
    result_b = baseline.run_baseline_simulation(_valid_input_snapshot(url="https://b.example.com"), 1)
    comparison = baseline.compare_baseline_results(
        result_a, result_b, persona_count_a=500, persona_count_b=500
    )
    assert comparison["calibration_status"] == "uncalibrated"
    assert comparison["sampled_synthetic_persona_count"] == {"variant_a": 500, "variant_b": 500}
    baseline.assert_no_banned_claims(comparison["note"])


# --- Durum makinesi: queued -> running ---------------------------------------


async def test_claim_next_queued_runs_transitions_to_running(
    session: AsyncSession, organization: Organization
):
    # `limit` buyuk tutulur: bu test gercek bir Postgres'e karsi (worker
    # container'i da ayni DB'ye baglidir) calisir; baska testlerden kalan
    # (commit edilmis) eski 'queued' satirlar varsa varsayilan kucuk bir
    # limit bu testin kendi (henuz commit edilmemis, yalnizca bu session'a
    # gorunur) satirini disari itebilir.
    run = await _make_queued_run(session, organization)
    claimed = await simulation_worker.claim_next_queued_runs(session, limit=10_000)

    assert any(r.id == run.id for r in claimed)
    await session.refresh(run)
    assert run.status == SimulationStatus.RUNNING
    assert run.attempt_count == 1
    assert run.started_at is not None


async def test_claim_does_not_reclaim_already_running_run(session: AsyncSession, organization: Organization):
    run = await _make_queued_run(session, organization)
    await simulation_worker.claim_next_queued_runs(session, limit=10_000)
    await session.refresh(run)
    assert run.status == SimulationStatus.RUNNING

    second_claim = await simulation_worker.claim_next_queued_runs(session, limit=10_000)
    assert run.id not in [r.id for r in second_claim]


# --- Basarili tamamlama + ucretsiz hak tuketimi ------------------------------


async def test_process_run_success_consumes_free_entitlement(
    session: AsyncSession, organization: Organization
):
    launch_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, launch_run_id)
    run = await _make_queued_run(
        session,
        organization,
        launch_run_id=launch_run_id,
        free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.SUCCEEDED
    assert run.result is not None
    assert run.result["calibration_status"] == "uncalibrated"
    assert run.progress_percent == 100

    entitlement = await entitlements.get_or_create_entitlement(
        session, organization.id, FEATURE_BASIC_UX_TEST
    )
    assert entitlement.status == EntitlementStatus.CONSUMED


@pytest.mark.security
async def test_process_run_success_consumes_chip_reservation_without_double_debit(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 1000, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 500, "test reserve", run_id=launch_run_id
    )
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500

    run = await _make_queued_run(
        session, organization, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.SUCCEEDED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500

    # Ayni run icin grup cozumlemesini tekrar cagirmak (ornegin bir
    # retry/redelivery senaryosunu simule ederek) bakiyeyi tekrar
    # dusurmemeli (idempotent).
    await simulation_worker._resolve_launch_group(session, run)
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500


# --- Basarisizlik + serbest birakma ------------------------------------------


@pytest.mark.security
async def test_process_run_failure_releases_chip_reservation(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 200, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 150, "test reserve", run_id=launch_run_id
    )

    run = await _make_queued_run(
        session,
        organization,
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
        input_snapshot={"role": "primary"},  # url eksik -> motor SimulationInputError firlatir
    )
    run.status = SimulationStatus.RUNNING

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.FAILED
    assert run.error is not None
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200

    # Basarisiz calistirma icin grup cozumlemesini tekrar cagirmak hata
    # firlatmamali (idempotent no-op, cift iade uretmez).
    await simulation_worker._resolve_launch_group(session, run)
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


@pytest.mark.security
async def test_retry_after_failure_reserves_and_requeues_without_double_consumption(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 300, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 100, "test reserve", run_id=launch_run_id
    )

    run = await _make_queued_run(
        session,
        organization,
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
        input_snapshot={"role": "primary"},
    )
    run.status = SimulationStatus.RUNNING
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.FAILED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 300

    # Girdiyi duzeltip yeniden dene.
    run.input_snapshot = _valid_input_snapshot()
    await simulation_worker.retry_run(session, organization.id, run.id)
    assert run.status == SimulationStatus.QUEUED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200  # tekrar rezerve edildi

    run.status = SimulationStatus.RUNNING
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.SUCCEEDED
    # Basarili tamamlama sonrasi bakiye degismemeli (yalnizca bir kez tuketildi).
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


async def test_retry_requeues_failed_url_page_analysis(
    session: AsyncSession, organization: Organization
):
    analysis = PageAnalysis(
        organization_id=organization.id,
        source_kind=PageAnalysisSourceKind.URL,
        url="https://example.com/",
        authorization_confirmed=True,
        status=PageAnalysisStatus.FAILED,
        attempt_count=3,
        error="analyzer hatasi (502): cold start",
    )
    session.add(analysis)
    await session.flush()

    run = await _make_queued_run(session, organization)
    run.page_analysis_id = analysis.id
    run.status = SimulationStatus.FAILED
    run.error = "Bagli sayfa analizi basarisiz oldugu icin bu calistirma islenemedi"
    await session.flush()

    await simulation_worker.retry_run(session, organization.id, run.id)

    assert run.status == SimulationStatus.QUEUED
    assert analysis.status == PageAnalysisStatus.QUEUED
    assert analysis.attempt_count == 0
    assert analysis.error is None
    assert analysis.started_at is None
    assert analysis.finished_at is None


async def test_retry_recreates_released_ai_reservation(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 100, "test credit")
    launch_run_id = uuid.uuid4()
    old_ai_reservation = await chip_ledger.reserve_chips(
        session,
        organization.id,
        50,
        "AI reserve",
        run_id=launch_run_id,
    )
    await chip_ledger.release_reservation(
        session,
        organization.id,
        old_ai_reservation.id,
        "baseline failed",
    )

    run = await _make_queued_run(session, organization, launch_run_id=launch_run_id)
    run.ai_chip_reservation_id = old_ai_reservation.id
    run.status = SimulationStatus.FAILED
    await session.flush()

    await simulation_worker.retry_run(session, organization.id, run.id)

    assert run.ai_chip_reservation_id != old_ai_reservation.id
    new_ai_reservation = await session.get(type(old_ai_reservation), run.ai_chip_reservation_id)
    assert new_ai_reservation is not None
    assert new_ai_reservation.status == ChipReservationStatus.RESERVED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 50


async def test_retry_on_non_failed_run_is_rejected(session: AsyncSession, organization: Organization):
    run = await _make_queued_run(session, organization)
    with pytest.raises(InvalidSimulationStateError):
        await simulation_worker.retry_run(session, organization.id, run.id)


# --- Iptal ---------------------------------------------------------------


async def test_cancel_queued_run_releases_reservation_immediately(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 100, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 80, "test reserve", run_id=launch_run_id
    )
    run = await _make_queued_run(
        session, organization, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    cancelled = await simulation_worker.cancel_run(session, organization.id, run.id)

    assert cancelled.status == SimulationStatus.CANCELLED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 100


async def test_cancel_running_run_sets_flag_and_process_run_finalizes_cancelled(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 100, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 80, "test reserve", run_id=launch_run_id
    )
    run = await _make_queued_run(
        session, organization, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )
    run.status = SimulationStatus.RUNNING
    await session.flush()

    result = await simulation_worker.cancel_run(session, organization.id, run.id)
    assert result.status == SimulationStatus.RUNNING
    assert result.cancel_requested is True

    await simulation_worker.process_run(session, run)

    assert run.status == SimulationStatus.CANCELLED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 100


@pytest.mark.security
async def test_cancel_is_idempotent_on_terminal_run(session: AsyncSession, organization: Organization):
    run = await _make_queued_run(session, organization)
    first = await simulation_worker.cancel_run(session, organization.id, run.id)
    second = await simulation_worker.cancel_run(session, organization.id, run.id)
    assert first.status == second.status == SimulationStatus.CANCELLED


async def test_cancel_unknown_run_raises_not_found(session: AsyncSession, organization: Organization):
    with pytest.raises(SimulationRunNotFoundError):
        await simulation_worker.cancel_run(session, organization.id, uuid.uuid4())


# --- Reap (takili kalmis isler) ----------------------------------------------


async def test_reap_stale_running_run_requeues_when_under_max_attempts(
    session: AsyncSession, organization: Organization
):
    run = await _make_queued_run(session, organization)
    run.status = SimulationStatus.RUNNING
    run.attempt_count = 1
    await session.flush()
    await session.execute(
        SimulationRun.__table__.update()
        .where(SimulationRun.id == run.id)
        .values(
            updated_at=__import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        )
    )

    reaped = await simulation_worker.reap_stale_running_runs(session, timeout_seconds=1)
    assert reaped == 1
    await session.refresh(run)
    assert run.status == SimulationStatus.QUEUED


async def test_reap_stale_running_run_fails_and_releases_after_max_attempts(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 100, "test credit")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 60, "test reserve", run_id=launch_run_id
    )
    run = await _make_queued_run(
        session, organization, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )
    run.status = SimulationStatus.RUNNING
    run.attempt_count = simulation_worker.MAX_ATTEMPTS
    await session.flush()
    await session.execute(
        SimulationRun.__table__.update()
        .where(SimulationRun.id == run.id)
        .values(
            updated_at=__import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        )
    )

    await simulation_worker.reap_stale_running_runs(session, timeout_seconds=1)
    await session.refresh(run)
    assert run.status == SimulationStatus.FAILED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 100
