"""Paket 4.0: A/B (ve genel olarak `launch_run_id` paylasan) calistirma
gruplarinin Chip/entitlement muhasebesinin ATOMIK oldugunu dogrulayan testler.

Kok neden (bkz. proje mimari karar raporu): eskiden her `SimulationRun`,
paylasilan rezervasyonu/ucretsiz hakki KENDI basarisi/basarisizligina bakarak
tek basina tuketiyor/serbest birakiyordu. Bu, "A basarili -> tuketildi, B
daha sonra basarisiz oldu -> B'nin release cagrisi zaten tuketilmis
rezervasyon icin etkisiz kaliyor, karsilastirma basarisiz oldugu halde Chip
harcanmis oluyor" senaryosuna yol aciyordu.

Duzeltme: `app.services.simulation_worker._resolve_launch_group`, bir run
terminal duruma gectiginde AYNI `launch_run_id`yi paylasan TUM calistirmalari
kilitleyip okur; yalnizca HEPSI terminal ise (ve ancak o zaman) tek seferlik
muhasebe islemi yapar (hepsi basarili -> tuket, en az biri degilse -> serbest
birak). Bu dosya hem eski (hatali) senaryoyu yeniden uretip artik dogru
davrandigini, hem de es zamanlilik/idempotency/geriye donuk uyumluluk
gereksinimlerini dogrular.

Es zamanlilik testleri, `session`/`organization` fixture'larinin (tek
baglanti + savepoint-rollback) ayri baglantilar arasi commit gorunurlugu
SAGLAMADIGI icin `test_engine` + `async_sessionmaker` deseni kullanir (bkz.
`tests/test_entitlements.py::test_concurrent_reservations_do_not_oversell_balance`
- ayni idiom burada da tekrarlanir).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.billing import (
    ChipLedgerEntry,
    ChipLedgerEntryType,
    ChipReservation,
    ChipReservationStatus,
    EntitlementStatus,
)
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.services import chip_ledger, entitlements, simulation_worker
from app.services.exceptions import InvalidSimulationStateError
from app.services.pricing import FEATURE_BASIC_UX_TEST

pytestmark = pytest.mark.integration


# --- Yardimcilar (test_simulation_engine.py / test_simulation_worker_modules.py ile ayni desen) --


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
        name=f"Varyant {uuid.uuid4().hex[:6]}",
        config={"role": "primary", "url": "https://example.com/ab-test"},
    )
    session.add(variant)
    await session.flush()
    return variant


def _valid_input_snapshot() -> dict:
    return {
        "wizard_test_type": "ab_comparison",
        "persona_count": 500,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": [],
        "url": "https://example.com/ab-test",
        "role": "existing",
        "pricing_version": "2026.1",
    }


async def _make_group_runs(
    session: AsyncSession,
    organization: Organization,
    *,
    count: int,
    launch_run_id: uuid.UUID,
    chip_reservation_id: uuid.UUID | None = None,
    free_entitlement_feature_key: str | None = None,
    status: SimulationStatus = SimulationStatus.RUNNING,
) -> list[SimulationRun]:
    """`count` adet, AYNI `launch_run_id`/rezervasyon/hakki paylasan calistirma
    olusturur - bkz. app.services.test_wizard.launch_draft: A/B karsilastirmasinda
    bu deger tam olarak 2'dir, ama bu yardimci fonksiyon (ve asil kod, bkz.
    `_resolve_launch_group`) sayiyi asla sabit varsaymaz.
    """

    runs = []
    for _ in range(count):
        variant = await _make_variant(session, organization)
        run = SimulationRun(
            organization_id=organization.id,
            test_variant_id=variant.id,
            status=status,
            deterministic_seed=uuid.uuid4().int & ((1 << 63) - 1),
            model_version="pending-engine",
            input_snapshot=_valid_input_snapshot(),
            launch_run_id=launch_run_id,
            chip_reservation_id=chip_reservation_id,
            free_entitlement_feature_key=free_entitlement_feature_key,
        )
        session.add(run)
        runs.append(run)
    await session.flush()
    return runs


async def _finalize(session: AsyncSession, run: SimulationRun, status: SimulationStatus) -> None:
    """`process_run`in basari/basarisizlik/iptal kuyruklarinin sonundaki
    "durumu terminal yap + grup cozumlemesini tetikle" adimini dogrudan
    tetikler (motor hesaplamasini/modul dispatch'ini atlar - bu dosyanin
    odagi muhasebe atomikligidir, motor mantigi degil - bkz.
    test_simulation_engine.py/test_simulation_worker_modules.py)."""

    run.status = status
    await session.flush()
    await simulation_worker._resolve_launch_group(session, run)


async def _consume_count(session: AsyncSession, reservation_id: uuid.UUID) -> int:
    result = await session.execute(
        select(ChipLedgerEntry).where(
            ChipLedgerEntry.reference_id == reservation_id,
            ChipLedgerEntry.entry_type == ChipLedgerEntryType.CONSUME,
        )
    )
    return len(result.scalars().all())


async def _release_count(session: AsyncSession, reservation_id: uuid.UUID) -> int:
    result = await session.execute(
        select(ChipLedgerEntry).where(
            ChipLedgerEntry.reference_id == reservation_id,
            ChipLedgerEntry.entry_type == ChipLedgerEntryType.RELEASE,
        )
    )
    return len(result.scalars().all())


# ===========================================================================
# Chip rezervasyonu - eksiksiz senaryo matrisi
# ===========================================================================


@pytest.mark.security
async def test_ab_one_success_one_still_running_no_accounting_yet(
    session: AsyncSession, organization: Organization
):
    """A basarili, B hala calisiyor -> ne consume ne release olur."""

    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RESERVED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200
    assert await _consume_count(session, reservation.id) == 0
    assert await _release_count(session, reservation.id) == 0

    # B hala RUNNING - hicbir muhasebe islemi tetiklenmemis olmali.
    await session.refresh(run_b)
    assert run_b.status == SimulationStatus.RUNNING


@pytest.mark.security
async def test_ab_both_success_consumes_exactly_once(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.CONSUMED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200
    assert await _consume_count(session, reservation.id) == 1
    assert await _release_count(session, reservation.id) == 0


@pytest.mark.security
async def test_ab_a_success_b_failure_releases_fully(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.FAILED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED
    # Bakiye tamamen geri geldi - A basarili olsa bile Chip HARCANMADI.
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500
    assert await _consume_count(session, reservation.id) == 0
    assert await _release_count(session, reservation.id) == 1


@pytest.mark.security
async def test_ab_a_failure_b_success_releases_fully(session: AsyncSession, organization: Organization):
    """Basarisizligin gelis sirasi (A once mi B once mi basarisiz) sonucu degistirmemeli."""

    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.FAILED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500


@pytest.mark.security
async def test_ab_both_failure_releases_exactly_once(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.FAILED)
    await _finalize(session, run_b, SimulationStatus.FAILED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500
    assert await _release_count(session, reservation.id) == 1


@pytest.mark.security
async def test_ab_a_cancelled_b_success_releases_fully(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.CANCELLED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500


@pytest.mark.security
async def test_ab_a_success_b_timeout_reaped_releases_fully(
    session: AsyncSession, organization: Organization
):
    """B, worker cokmesi/zaman asimi nedeniyle reap tarafindan max deneme
    sinirinda basarisiz sayiliyor - bu da grubu (A basarili olsa bile) tam
    olarak serbest birakmali."""

    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)

    run_b.attempt_count = simulation_worker.MAX_ATTEMPTS
    await session.flush()
    await session.execute(
        SimulationRun.__table__.update()
        .where(SimulationRun.id == run_b.id)
        .values(updated_at=__import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").timezone.utc))
    )

    reaped = await simulation_worker.reap_stale_running_runs(session, timeout_seconds=1)
    assert reaped == 1

    await session.refresh(run_b)
    assert run_b.status == SimulationStatus.FAILED
    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500


@pytest.mark.security
async def test_resolve_launch_group_terminal_callback_replay_produces_no_second_ledger_entry(
    session: AsyncSession, organization: Organization
):
    """Ayni terminal olayin (ör. bir redelivery/tekrar-cagrida) IKINCI kez
    isaretlenmesi ikinci bir ledger kaydi/ek bakiye etkisi uretmemeli."""

    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)
    assert await _consume_count(session, reservation.id) == 1

    # Ayni run icin cozumlemeyi tekrar tetikle (ör. worker'in ayni terminal
    # olayi ikinci kez islemeye calistigi bir senaryoyu simule ederek).
    await simulation_worker._resolve_launch_group(session, run_a)
    await simulation_worker._resolve_launch_group(session, run_b)

    assert await _consume_count(session, reservation.id) == 1
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


@pytest.mark.security
async def test_retry_after_group_release_reserves_fresh_and_avoids_double_reservation(
    session: AsyncSession, organization: Organization
):
    """Grup basarisiz olup serbest birakildiktan sonra basarisiz tarafin
    retry edilmesi: (a) yeni bir rezervasyon acar (eskisi zaten kapali), (b)
    grup yeniden basariyla tamamlaninca YALNIZCA bu yeni rezervasyon
    tuketilir - cift tuketim/mukerrer acik rezervasyon olusmaz."""

    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.FAILED)
    assert await chip_ledger.get_chip_balance(session, organization.id) == 500  # tam release

    await simulation_worker.retry_run(session, organization.id, run_b.id)
    await session.refresh(run_b)
    assert run_b.status == SimulationStatus.QUEUED
    assert run_b.chip_reservation_id != reservation.id  # yeni rezervasyon acildi
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200  # yeniden rezerve edildi

    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    new_reservation = await session.get(ChipReservation, run_b.chip_reservation_id)
    assert new_reservation.status == ChipReservationStatus.CONSUMED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200
    # Eski (ilk jenerasyon) rezervasyon RELEASED durumunda kalmaya devam
    # eder - "un-release edip tekrar consume etme" gibi gecersiz bir gecis
    # denenmedi (bkz. _resolve_chip_reservation'in guvenli atlama mantigi).
    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED


@pytest.mark.security
async def test_retry_while_sibling_still_running_does_not_create_duplicate_reservation(
    session: AsyncSession, organization: Organization
):
    """B basarisiz oldugunda A HALA calisiyorsa grup henuz cozumlenmemistir
    (rezervasyon hala 'reserved'); bu durumda B'nin retry'i YENI bir
    rezervasyon acmamali, mevcut (hala acik) rezervasyonu yeniden kullanmali."""

    await chip_ledger.credit(session, organization.id, 500, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 300, "reserve", run_id=launch_run_id
    )
    run_a, run_b = await _make_group_runs(
        session, organization, count=2, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    # B basarisiz olur, ama A hala RUNNING - grup cozumlenmez.
    await _finalize(session, run_b, SimulationStatus.FAILED)
    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RESERVED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200

    await simulation_worker.retry_run(session, organization.id, run_b.id)
    await session.refresh(run_b)
    # Rezervasyon DEGISMEDI (mukerrer rezervasyon acilmadi), bakiye ayni.
    assert run_b.chip_reservation_id == reservation.id
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


# ===========================================================================
# Ucretsiz hak (free entitlement) - kritik alt kume
# ===========================================================================


@pytest.mark.security
async def test_ab_entitlement_both_success_consumes_once(session: AsyncSession, organization: Organization):
    launch_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, launch_run_id)
    run_a, run_b = await _make_group_runs(
        session,
        organization,
        count=2,
        launch_run_id=launch_run_id,
        free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    entitlement = await entitlements.get_or_create_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST)
    assert entitlement.status == EntitlementStatus.RESERVED  # B hala terminal degil

    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)
    await session.refresh(entitlement)
    assert entitlement.status == EntitlementStatus.CONSUMED


@pytest.mark.security
async def test_ab_entitlement_one_failure_releases_and_available_again(
    session: AsyncSession, organization: Organization
):
    launch_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, launch_run_id)
    run_a, run_b = await _make_group_runs(
        session,
        organization,
        count=2,
        launch_run_id=launch_run_id,
        free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.FAILED)

    entitlement = await entitlements.get_or_create_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST)
    assert entitlement.status == EntitlementStatus.AVAILABLE
    assert entitlement.reserved_run_id is None


@pytest.mark.security
async def test_entitlement_resolution_replay_is_idempotent(session: AsyncSession, organization: Organization):
    launch_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, launch_run_id)
    run_a, run_b = await _make_group_runs(
        session,
        organization,
        count=2,
        launch_run_id=launch_run_id,
        free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
    )

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    # Ikinci cagri hata firlatmamali ve durumu degistirmemeli.
    await simulation_worker._resolve_launch_group(session, run_a)
    entitlement = await entitlements.get_or_create_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST)
    assert entitlement.status == EntitlementStatus.CONSUMED


# ===========================================================================
# Geriye donuk uyumluluk: tekil (A/B olmayan) calistirmalar
# ===========================================================================


@pytest.mark.security
async def test_single_run_success_still_consumes_immediately(
    session: AsyncSession, organization: Organization
):
    """Tekil (A/B olmayan) bir launch de bir `launch_run_id` tasir, ama grup
    boyutu 1'dir - bu durumda muhasebe bugunku gibi calistirmanin KENDI
    sonucunda hemen islenir (baska bir kardes beklenmez)."""

    await chip_ledger.credit(session, organization.id, 200, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 150, "reserve", run_id=launch_run_id
    )
    (run,) = await _make_group_runs(
        session, organization, count=1, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run, SimulationStatus.SUCCEEDED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.CONSUMED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 50


@pytest.mark.security
async def test_single_run_failure_still_releases_immediately(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 200, "kredi")
    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 150, "reserve", run_id=launch_run_id
    )
    (run,) = await _make_group_runs(
        session, organization, count=1, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
    )

    await _finalize(session, run, SimulationStatus.FAILED)

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 200


async def test_retry_on_non_failed_run_is_still_rejected(session: AsyncSession, organization: Organization):
    """Regresyon: retry_run'daki durum kontrolu bu paketle degismedi."""

    (run,) = await _make_group_runs(
        session, organization, count=1, launch_run_id=uuid.uuid4(), status=SimulationStatus.QUEUED
    )
    with pytest.raises(InvalidSimulationStateError):
        await simulation_worker.retry_run(session, organization.id, run.id)


# ===========================================================================
# Es zamanlilik: gercek ayri baglantilar/transaction'lar (bkz. dosya basligi)
# ===========================================================================


async def _seed_group_committed(
    test_engine, *, count: int, chip_credit: int, chip_reserve_amount: int
) -> tuple[uuid.UUID, list[uuid.UUID], uuid.UUID]:
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    session = maker()
    try:
        org = Organization(name="AB Concurrency Org", slug=f"ab-concurrency-{uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()

        launch_run_id = uuid.uuid4()
        await chip_ledger.credit(session, org.id, chip_credit, "kredi")
        reservation = await chip_ledger.reserve_chips(
            session, org.id, chip_reserve_amount, "reserve", run_id=launch_run_id
        )
        runs = await _make_group_runs(
            session, org, count=count, launch_run_id=launch_run_id, chip_reservation_id=reservation.id
        )
        run_ids = [r.id for r in runs]
        org_id = org.id
        reservation_id = reservation.id
        await session.commit()
    finally:
        await session.close()
    return org_id, run_ids, reservation_id


async def _finalize_in_new_connection(test_engine, run_id: uuid.UUID, status: SimulationStatus) -> None:
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    session = maker()
    try:
        result = await session.execute(
            select(SimulationRun).where(SimulationRun.id == run_id).with_for_update()
        )
        run = result.scalar_one()
        run.status = status
        await session.flush()
        await simulation_worker._resolve_launch_group(session, run)
        await session.commit()
    finally:
        await session.close()


async def _cleanup_organization(test_engine, organization_id: uuid.UUID) -> None:
    maker = async_sessionmaker(test_engine, expire_on_commit=False)
    session = maker()
    try:
        db_org = await session.get(Organization, organization_id)
        if db_org is not None:
            await session.delete(db_org)
            await session.commit()
    finally:
        await session.close()


@pytest.mark.security
async def test_concurrent_both_siblings_succeed_produces_exactly_one_consume(test_engine):
    """Iki worker, ayni gruptaki iki kardesi TAM AYNI ANDA basariyla
    sonuclandirmaya calisirsa: satir kilitlemesi (`FOR UPDATE`) sayesinde
    yalnizca BIR tuketim islenir, ikinci cagri zaten CONSUMED durumu bulup
    no-op yapar."""

    org_id, run_ids, reservation_id = await _seed_group_committed(
        test_engine, count=2, chip_credit=500, chip_reserve_amount=300
    )
    try:
        await asyncio.gather(
            _finalize_in_new_connection(test_engine, run_ids[0], SimulationStatus.SUCCEEDED),
            _finalize_in_new_connection(test_engine, run_ids[1], SimulationStatus.SUCCEEDED),
        )

        maker = async_sessionmaker(test_engine, expire_on_commit=False)
        verify_session = maker()
        try:
            reservation = await verify_session.get(ChipReservation, reservation_id)
            assert reservation.status == ChipReservationStatus.CONSUMED
            balance = await chip_ledger.get_chip_balance(verify_session, org_id)
            assert balance == 200
            assert await _consume_count(verify_session, reservation_id) == 1
            assert await _release_count(verify_session, reservation_id) == 0
        finally:
            await verify_session.close()
    finally:
        await _cleanup_organization(test_engine, org_id)


@pytest.mark.security
async def test_concurrent_one_succeeds_one_fails_produces_exactly_one_release(test_engine):
    """Iki worker, ayni gruptaki iki kardesi ES ZAMANLI olarak FARKLI
    sonuclarla (biri basarili, biri basarisiz) sonuclandirmaya calisirsa:
    net sonuc TAM OLARAK bir release olmali, hicbir Chip harcanmamali."""

    org_id, run_ids, reservation_id = await _seed_group_committed(
        test_engine, count=2, chip_credit=500, chip_reserve_amount=300
    )
    try:
        await asyncio.gather(
            _finalize_in_new_connection(test_engine, run_ids[0], SimulationStatus.SUCCEEDED),
            _finalize_in_new_connection(test_engine, run_ids[1], SimulationStatus.FAILED),
        )

        maker = async_sessionmaker(test_engine, expire_on_commit=False)
        verify_session = maker()
        try:
            reservation = await verify_session.get(ChipReservation, reservation_id)
            assert reservation.status == ChipReservationStatus.RELEASED
            balance = await chip_ledger.get_chip_balance(verify_session, org_id)
            assert balance == 500
            assert await _consume_count(verify_session, reservation_id) == 0
            assert await _release_count(verify_session, reservation_id) == 1
        finally:
            await verify_session.close()
    finally:
        await _cleanup_organization(test_engine, org_id)
