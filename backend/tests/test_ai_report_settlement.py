"""Faz 3C.2B1: baseline-grup-sonucuna bagli AI rezervasyon settlement testleri
(A/B 23-26 + regression).

Bu FAZ yalnizca BASELINE grup sonucuna bagli AI consume/release yapar:
  - tum baseline run'lar SUCCEEDED -> baseline CONSUMED, AI RESERVED (kalir);
  - en az bir run FAILED/CANCELLED -> baseline RELEASED, AI da RELEASED
    (paylasilan AI rezervasyonu yalnizca BIR KEZ release edilir).
AI pipeline'inin KENDI sonucuna bagli hicbir settlement yapilmaz (pipeline bu
fazda hic olusturulmaz). Desen `tests/test_atomic_ab_reservation.py` ile aynidir.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import (
    ChipLedgerEntry,
    ChipLedgerEntryType,
    ChipReservationStatus,
)
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.services import chip_ledger, simulation_worker

pytestmark = pytest.mark.integration


async def _make_variant(session: AsyncSession, organization: Organization):
    from app.models.projects import Project
    from app.models.tests import TestDefinition, TestVariant

    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()
    definition = TestDefinition(
        organization_id=organization.id, project_id=project.id, name=f"Def {uuid.uuid4().hex[:6]}"
    )
    session.add(definition)
    await session.flush()
    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name=f"Var {uuid.uuid4().hex[:6]}",
        config={"role": "primary"},
    )
    session.add(variant)
    await session.flush()
    return variant


async def _make_group_runs(
    session: AsyncSession,
    organization: Organization,
    *,
    count: int,
    launch_run_id: uuid.UUID,
    chip_reservation_id: uuid.UUID,
    ai_chip_reservation_id: uuid.UUID,
) -> list[SimulationRun]:
    runs = []
    for _ in range(count):
        variant = await _make_variant(session, organization)
        run = SimulationRun(
            organization_id=organization.id,
            test_variant_id=variant.id,
            status=SimulationStatus.RUNNING,
            deterministic_seed=uuid.uuid4().int & ((1 << 63) - 1),
            model_version="pending-engine",
            input_snapshot={"modules": ["ai_report"], "methodology_context": "m"},
            launch_run_id=launch_run_id,
            chip_reservation_id=chip_reservation_id,
            ai_chip_reservation_id=ai_chip_reservation_id,
        )
        session.add(run)
        runs.append(run)
    await session.flush()
    return runs


async def _finalize(session: AsyncSession, run: SimulationRun, status: SimulationStatus) -> None:
    run.status = status
    await session.flush()
    await simulation_worker._resolve_launch_group(session, run)


async def _release_count(session: AsyncSession, reservation_id: uuid.UUID) -> int:
    result = await session.execute(
        select(ChipLedgerEntry).where(
            ChipLedgerEntry.reference_id == reservation_id,
            ChipLedgerEntry.entry_type == ChipLedgerEntryType.RELEASE,
        )
    )
    return len(result.scalars().all())


async def _consume_count(session: AsyncSession, reservation_id: uuid.UUID) -> int:
    result = await session.execute(
        select(ChipLedgerEntry).where(
            ChipLedgerEntry.reference_id == reservation_id,
            ChipLedgerEntry.entry_type == ChipLedgerEntryType.CONSUME,
        )
    )
    return len(result.scalars().all())


async def _seed(session, organization, *, count):
    await chip_ledger.credit(session, organization.id, 1000, "kredi")
    launch_run_id = uuid.uuid4()
    baseline = await chip_ledger.reserve_chips(session, organization.id, 35, "baseline", run_id=launch_run_id)
    ai = await chip_ledger.reserve_chips(session, organization.id, 50, "ai", run_id=launch_run_id)
    runs = await _make_group_runs(
        session,
        organization,
        count=count,
        launch_run_id=launch_run_id,
        chip_reservation_id=baseline.id,
        ai_chip_reservation_id=ai.id,
    )
    return baseline, ai, runs


@pytest.mark.security
async def test_ab_all_success_consumes_baseline_but_leaves_ai_reserved(
    session: AsyncSession, organization: Organization
):
    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    await session.refresh(baseline)
    await session.refresh(ai)
    assert baseline.status == ChipReservationStatus.CONSUMED
    # AI bu fazda consume EDILMEZ -> RESERVED kalir.
    assert ai.status == ChipReservationStatus.RESERVED
    assert await _consume_count(session, ai.id) == 0
    assert await _release_count(session, ai.id) == 0


@pytest.mark.security
async def test_ab_one_failure_releases_baseline_and_ai(session: AsyncSession, organization: Organization):
    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.FAILED)

    await session.refresh(baseline)
    await session.refresh(ai)
    assert baseline.status == ChipReservationStatus.RELEASED
    assert ai.status == ChipReservationStatus.RELEASED
    # Tam bakiye geri geldi (1000 - hicbir sey harcanmadi).
    assert await chip_ledger.get_chip_balance(session, organization.id) == 1000


@pytest.mark.security
async def test_ab_cancelled_releases_baseline_and_ai(session: AsyncSession, organization: Organization):
    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)

    await _finalize(session, run_a, SimulationStatus.CANCELLED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    await session.refresh(ai)
    assert ai.status == ChipReservationStatus.RELEASED


@pytest.mark.security
async def test_shared_ai_reservation_produces_exactly_one_release(
    session: AsyncSession, organization: Organization
):
    """A ve B AYNI AI rezervasyon id'sini tasir; grup basarisiz oldugunda AI
    icin YALNIZCA BIR release ledger kaydi olusur (cift release olmaz)."""

    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)

    await _finalize(session, run_a, SimulationStatus.FAILED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    assert await _release_count(session, ai.id) == 1


@pytest.mark.security
async def test_group_not_terminal_leaves_both_reservations_unresolved(
    session: AsyncSession, organization: Organization
):
    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)

    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    # run_b hala RUNNING - grup terminal degil.

    await session.refresh(baseline)
    await session.refresh(ai)
    assert baseline.status == ChipReservationStatus.RESERVED
    assert ai.status == ChipReservationStatus.RESERVED


@pytest.mark.security
async def test_single_run_success_leaves_ai_reserved(session: AsyncSession, organization: Organization):
    baseline, ai, (run,) = await _seed(session, organization, count=1)

    await _finalize(session, run, SimulationStatus.SUCCEEDED)

    await session.refresh(baseline)
    await session.refresh(ai)
    assert baseline.status == ChipReservationStatus.CONSUMED
    assert ai.status == ChipReservationStatus.RESERVED


@pytest.mark.security
async def test_all_success_replay_does_not_consume_ai(session: AsyncSession, organization: Organization):
    """Terminal olayin ikinci kez islenmesi AI'yi (RESERVED) yanlislikla
    consume/release ETMEMELI."""

    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)
    await _finalize(session, run_a, SimulationStatus.SUCCEEDED)
    await _finalize(session, run_b, SimulationStatus.SUCCEEDED)

    await simulation_worker._resolve_launch_group(session, run_a)

    await session.refresh(ai)
    assert ai.status == ChipReservationStatus.RESERVED
    assert await _consume_count(session, ai.id) == 0


@pytest.mark.security
async def test_no_adjustment_ledger_entries_are_written(session: AsyncSession, organization: Organization):
    """Kod ADJUSTMENT ledger kaydi URETMEZ (release plain reserve->release
    yasam dongusunu kullanir)."""

    baseline, ai, (run_a, run_b) = await _seed(session, organization, count=2)
    await _finalize(session, run_a, SimulationStatus.FAILED)
    await _finalize(session, run_b, SimulationStatus.FAILED)

    result = await session.execute(
        select(ChipLedgerEntry).where(
            ChipLedgerEntry.organization_id == organization.id,
            ChipLedgerEntry.entry_type == ChipLedgerEntryType.ADJUSTMENT,
        )
    )
    assert result.scalars().all() == []
