"""Faz 3C.3: AI pipeline MANUEL retry + cancel mutasyon servisleri/endpointleri.

Iki test stili (bkz. tests/conftest.py tasarim notu):
- Servis seviyesi (`session` fixture): finans/state/A-B/eligibility mantigi,
  izole transaction'da tam kontrol.
- HTTP seviyesi (`client` fixture): tenant/auth/CSRF izolasyonu, response sekli,
  mutation-yok-on-403/404. Pipeline/rezervasyon satirlari AYRI bir engine ile
  tohumlanir (bkz. test_ai_pipeline_router.py deseni).
"""

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass, field

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import (
    AIPipelineRun,
    ChipLedgerEntry,
    ChipReservation,
    Organization,
    Project,
    SimulationRun,
    TestDefinition,
    TestVariant,
)
from app.models.ai_pipeline import (
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.billing import ChipReservationStatus
from app.models.simulations import SimulationStatus
from app.services import chip_ledger
from app.services import personas as personas_service
from app.services.ai_pipeline import mutations
from app.services.pricing import AI_REPORT_CHIP_COST
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


def _arun(func, *args, **kwargs):
    return anyio.run(functools.partial(func, *args, **kwargs))


# =============================================================================
# Stage/pipeline spec + seeding (servis seviyesi)
# =============================================================================


@dataclass
class StageSpec:
    stage_type: AIPipelineStageType
    status: AIPipelineStageStatus
    batch_index: int | None = None
    validated_output: dict | None = None
    error_code: str | None = None
    attempt_count: int = 1


@dataclass
class PipelineSpec:
    status: AIPipelineStatus
    stages: list[StageSpec] = field(default_factory=list)
    manual_retry_count: int = 0
    cancel_requested: bool = False


def _stage(pipeline_id: uuid.UUID, spec: StageSpec, *, execution_key: str | None = None) -> AIPipelineStage:
    return AIPipelineStage(
        ai_pipeline_run_id=pipeline_id,
        stage_type=spec.stage_type,
        batch_index=spec.batch_index,
        status=spec.status,
        attempt_count=spec.attempt_count,
        input_hash="0" * 64,
        idempotency_key=uuid.uuid4().hex,
        execution_idempotency_key=execution_key,
        provider="mock" if execution_key else None,
        model_name="mock-model" if execution_key else None,
        validated_output=spec.validated_output,
        error_code=spec.error_code,
    )


async def _make_org(session: AsyncSession, *, credit: int = 1000) -> Organization:
    org = Organization(name="Mut Org", slug=f"mut-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    if credit > 0:
        await chip_ledger.credit(session, org.id, credit, "test seed credit")
    return org


async def _make_reservation(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    status: ChipReservationStatus,
    amount: int = AI_REPORT_CHIP_COST,
) -> ChipReservation:
    """Gercek ledger akisiyla istenen durumda bir AI rezervasyonu kurar."""

    res = await chip_ledger.reserve_chips(
        session, org_id, amount, "ai reserve seed", idempotency_key=f"seed:{uuid.uuid4().hex}"
    )
    if status == ChipReservationStatus.RELEASED:
        await chip_ledger.release_reservation(session, org_id, res.id, "seed release")
    elif status == ChipReservationStatus.CONSUMED:
        await chip_ledger.consume_reservation(session, org_id, res.id, "seed consume")
    return res


async def _make_group(
    session: AsyncSession,
    *,
    org: Organization,
    pipelines: list[PipelineSpec],
    reservation_status: ChipReservationStatus = ChipReservationStatus.RELEASED,
    reservation: ChipReservation | None = None,
    ai_report: bool = True,
) -> tuple[list[SimulationRun], list[AIPipelineRun], ChipReservation]:
    """Bir launch grubu (len(pipelines) varyant) + paylasilan AI rezervasyonu +
    her varyant icin bir AIPipelineRun (+ stage'ler) tohumlar."""

    if reservation is None:
        reservation = await _make_reservation(session, org.id, status=reservation_status)
    launch_run_id = uuid.uuid4()
    project = Project(organization_id=org.id, name="Mut Project")
    session.add(project)
    await session.flush()
    definition = TestDefinition(organization_id=org.id, project_id=project.id, name="Mut Test")
    session.add(definition)
    await session.flush()

    runs: list[SimulationRun] = []
    pipeline_rows: list[AIPipelineRun] = []
    modules = ["ai_report"] if ai_report else []
    for i, pspec in enumerate(pipelines):
        variant = TestVariant(
            organization_id=org.id, test_definition_id=definition.id, name=f"Variant {i}", config={}
        )
        session.add(variant)
        await session.flush()
        run = SimulationRun(
            organization_id=org.id,
            test_variant_id=variant.id,
            status=SimulationStatus.SUCCEEDED,
            deterministic_seed=1,
            model_version="v0",
            input_snapshot={"persona_count": 15, "modules": modules},
            result={"metrics": {}},
            launch_run_id=launch_run_id,
            ai_chip_reservation_id=reservation.id,
        )
        session.add(run)
        await session.flush()
        pipeline = AIPipelineRun(
            organization_id=org.id,
            simulation_run_id=run.id,
            status=pspec.status,
            cancel_requested=pspec.cancel_requested,
            manual_retry_count=pspec.manual_retry_count,
        )
        session.add(pipeline)
        await session.flush()
        for spec in pspec.stages:
            session.add(
                _stage(
                    pipeline.id,
                    spec,
                    execution_key=(
                        f"exec:{run.id}:{spec.stage_type.value}:{spec.batch_index}"
                        if spec.status == AIPipelineStageStatus.SUCCEEDED
                        else None
                    ),
                )
            )
        await session.flush()
        runs.append(run)
        pipeline_rows.append(pipeline)
    return runs, pipeline_rows, reservation


def _failed_pipeline(error_code: str = "provider_timeout") -> PipelineSpec:
    return PipelineSpec(
        status=AIPipelineStatus.FAILED,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.SCENARIO_INTERPRETATION,
                AIPipelineStageStatus.FAILED,
                error_code=error_code,
            ),
        ],
    )


def _succeeded_pipeline() -> PipelineSpec:
    return PipelineSpec(
        status=AIPipelineStatus.SUCCEEDED,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(AIPipelineStageType.UX_REPORT, AIPipelineStageStatus.SUCCEEDED),
        ],
    )


async def _reservation_status(session: AsyncSession, res_id: uuid.UUID) -> ChipReservationStatus:
    res = await session.get(ChipReservation, res_id)
    assert res is not None
    return res.status


# =============================================================================
# MODEL / MIGRATION
# =============================================================================


async def test_manual_retry_count_defaults_to_zero(session: AsyncSession):
    org = await _make_org(session)
    _runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    fresh = await session.get(AIPipelineRun, pipelines[0].id)
    assert fresh is not None
    assert fresh.manual_retry_count == 0


async def test_manual_retry_count_negative_rejected_by_db(session: AsyncSession):
    from sqlalchemy.exc import IntegrityError

    org = await _make_org(session)
    _runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    pipelines[0].manual_retry_count = -1
    with pytest.raises(IntegrityError):
        await session.flush()


def test_single_alembic_head():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    # Platform admin altyapisi: 'd68747f9e724' (users.is_platform_admin) tek
    # head'i ilerletti (bkz. backend/migrations/versions/d68747f9e724_*).
    assert list(heads) == ["d68747f9e724"], heads


def test_migration_down_revision_chain():
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("ab507b226d36")
    assert rev.down_revision == "c7b2f4e91a5d"


async def test_manual_retry_count_column_introspection(session: AsyncSession):
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name='ai_pipeline_runs' AND column_name='manual_retry_count'"
            )
        )
    ).first()
    assert row is not None
    is_nullable, column_default = row
    assert is_nullable == "NO"
    assert "0" in (column_default or "")


# =============================================================================
# RETRY FINANCE
# =============================================================================


async def test_retry_failed_released_creates_new_50_reservation(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, old_res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    balance_before = await chip_ledger.get_chip_balance(session, org.id)

    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert result.charged_chips == AI_REPORT_CHIP_COST
    # Yeni rezervasyon eski rezervasyondan AYRI ve RESERVED.
    new_res_id = runs[0].ai_chip_reservation_id
    assert new_res_id != old_res.id
    assert await _reservation_status(session, new_res_id) == ChipReservationStatus.RESERVED
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before - AI_REPORT_CHIP_COST


async def test_retry_ab_single_50_charge(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session, org=org, pipelines=[_succeeded_pipeline(), _failed_pipeline()]
    )
    balance_before = await chip_ledger.get_chip_balance(session, org.id)
    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[1].id
    )
    assert result.charged_chips == AI_REPORT_CHIP_COST
    # Tek -50 (100 DEGIL). Iki run da AYNI yeni rezervasyonu tasir.
    assert runs[0].ai_chip_reservation_id == runs[1].ai_chip_reservation_id
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before - AI_REPORT_CHIP_COST


async def test_retry_insufficient_balance_no_state_change(session: AsyncSession):
    from app.services.exceptions import InsufficientChipBalanceError

    # Yalnizca orijinal rezervasyon icin yeter, retry icin yetmez.
    org = await _make_org(session, credit=AI_REPORT_CHIP_COST)
    runs, pipelines, old_res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    # Orijinal rezervasyon RELEASED oldugu icin balance == credit (net 0); ama
    # retry oncesi baska bir rezervasyonla bakiyeyi tuket.
    drain = await chip_ledger.reserve_chips(session, org.id, AI_REPORT_CHIP_COST, "drain")
    assert drain is not None
    balance_before = await chip_ledger.get_chip_balance(session, org.id)
    manual_before = pipelines[0].manual_retry_count
    old_ai_res = runs[0].ai_chip_reservation_id

    with pytest.raises(InsufficientChipBalanceError):
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert pipelines[0].manual_retry_count == manual_before
    assert runs[0].ai_chip_reservation_id == old_ai_res
    assert pipelines[0].status == AIPipelineStatus.FAILED
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before


async def test_retry_idempotency_key_bound_to_generation(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    launch_group_id = result.launch_group_id
    expected_key = f"ai-report-retry:{launch_group_id}:1"
    new_res = await session.get(ChipReservation, runs[0].ai_chip_reservation_id)
    assert new_res is not None
    assert new_res.idempotency_key == expected_key


async def test_retry_same_key_returns_same_reservation(session: AsyncSession):
    """Ayni generation key concurrent/duplice istekte AYNI rezervasyonu doner."""

    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    key = f"ai-report-retry:{result.launch_group_id}:1"
    # Ayni key ile reserve_chips YENI satir olusturmadan mevcut olani doner.
    again = await chip_ledger.reserve_chips(session, org.id, AI_REPORT_CHIP_COST, "dup", idempotency_key=key)
    assert again.id == runs[0].ai_chip_reservation_id


async def test_retry_baseline_reservation_unchanged(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    baseline_before = runs[0].chip_reservation_id
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert runs[0].chip_reservation_id == baseline_before


async def test_retry_no_adjustment_ledger_entry(session: AsyncSession):
    from app.models.billing import ChipLedgerEntryType

    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    adjustments = (
        await session.execute(
            select(func.count())
            .select_from(ChipLedgerEntry)
            .where(
                ChipLedgerEntry.organization_id == org.id,
                ChipLedgerEntry.entry_type == ChipLedgerEntryType.ADJUSTMENT,
            )
        )
    ).scalar_one()
    assert adjustments == 0


async def test_retry_rejected_when_reservation_still_reserved(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session, org=org, pipelines=[_failed_pipeline()], reservation_status=ChipReservationStatus.RESERVED
    )
    balance_before = await chip_ledger.get_chip_balance(session, org.id)
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert exc.value.code == mutations.ERROR_RETRY_WAITING_FOR_SETTLEMENT
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before


async def test_retry_rejected_when_reservation_consumed(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session, org=org, pipelines=[_failed_pipeline()], reservation_status=ChipReservationStatus.CONSUMED
    )
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert exc.value.code == mutations.ERROR_RETRY_RESERVATION_CONSUMED


async def test_second_retry_after_first_is_conflict(session: AsyncSession):
    """Ilk retry sonrasi grup QUEUED + rezervasyon RESERVED -> ikinci retry
    conflict (generation iki kez artmaz, ikinci -50 olusmaz)."""

    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    balance_after_first = await chip_ledger.get_chip_balance(session, org.id)
    with pytest.raises(mutations.AIPipelineGroupConflictError):
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    fresh = await session.get(AIPipelineRun, pipelines[0].id)
    assert fresh is not None
    assert fresh.manual_retry_count == 1
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_after_first


# =============================================================================
# RETRY STATE
# =============================================================================


async def test_retry_failed_pipeline_becomes_queued(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert pipelines[0].status == AIPipelineStatus.QUEUED
    assert pipelines[0].finished_at is None
    assert pipelines[0].cancel_requested is False
    assert result.requeued_pipeline_count == 1


async def test_retry_increments_manual_retry_count(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert result.manual_retry_count == 1
    assert pipelines[0].manual_retry_count == 1


async def test_retry_preserves_succeeded_pipeline_and_stages(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[_succeeded_pipeline(), _failed_pipeline()]
    )
    succeeded_pipeline = pipelines[0]
    succeeded_stage_ids = {
        s.id
        for s in (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == succeeded_pipeline.id)
            )
        )
        .scalars()
        .all()
    }
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[1].id)
    # A (succeeded) pipeline durumu degismez; stage'leri SUCCEEDED kalir.
    assert succeeded_pipeline.status == AIPipelineStatus.SUCCEEDED
    stages_now = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == succeeded_pipeline.id)
            )
        )
        .scalars()
        .all()
    )
    assert {s.id for s in stages_now} == succeeded_stage_ids
    assert all(s.status == AIPipelineStageStatus.SUCCEEDED for s in stages_now)


async def test_retry_failed_stage_requeued_attempt_reset(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipelines[0].id)
            )
        )
        .scalars()
        .all()
    )
    failed = next(s for s in stages if s.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert failed.status == AIPipelineStageStatus.QUEUED
    assert failed.attempt_count == 0
    assert failed.error_code is None
    assert failed.started_at is None
    assert failed.finished_at is None
    # SUCCEEDED evidence stage KORUNUR.
    evidence = next(s for s in stages if s.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION)
    assert evidence.status == AIPipelineStageStatus.SUCCEEDED


async def test_retry_preserves_logical_and_execution_keys_on_succeeded(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    stages_before = {
        s.stage_type: (s.idempotency_key, s.execution_idempotency_key, s.provider, s.model_name)
        for s in (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipelines[0].id)
            )
        )
        .scalars()
        .all()
    }
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages_after = {
        s.stage_type: (s.idempotency_key, s.execution_idempotency_key, s.provider, s.model_name)
        for s in (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipelines[0].id)
            )
        )
        .scalars()
        .all()
    }
    # Logical idempotency_key ve (succeeded stage'de) execution/pin KORUNUR.
    assert (
        stages_after[AIPipelineStageType.EVIDENCE_PREPARATION]
        == stages_before[AIPipelineStageType.EVIDENCE_PREPARATION]
    )
    # Failed stage'in logical key'i de korunur (execution key None'di, None kalir).
    assert (
        stages_after[AIPipelineStageType.SCENARIO_INTERPRETATION][0]
        == stages_before[AIPipelineStageType.SCENARIO_INTERPRETATION][0]
    )


async def test_retry_two_failed_ab_same_generation(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[_failed_pipeline(), _failed_pipeline()]
    )
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert pipelines[0].manual_retry_count == pipelines[1].manual_retry_count == 1
    assert pipelines[0].status == AIPipelineStatus.QUEUED
    assert pipelines[1].status == AIPipelineStatus.QUEUED


async def test_retry_only_failed_reset_when_one_succeeded(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[_succeeded_pipeline(), _failed_pipeline()]
    )
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert pipelines[0].status == AIPipelineStatus.SUCCEEDED
    assert pipelines[1].status == AIPipelineStatus.QUEUED
    # Ama her ikisi de AYNI generation'i tasir (grup tutarliligi).
    assert pipelines[0].manual_retry_count == pipelines[1].manual_retry_count == 1


async def test_retry_partial_batch4_only_failed_batch_requeued(session: AsyncSession):
    org = await _make_org(session)
    pipeline = PipelineSpec(
        status=AIPipelineStatus.FAILED,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(AIPipelineStageType.PERSONA_BATCH_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageStatus.SUCCEEDED, batch_index=0),
            StageSpec(
                AIPipelineStageType.PERSONA_BEHAVIOR,
                AIPipelineStageStatus.FAILED,
                batch_index=1,
                error_code="provider_timeout",
            ),
        ],
    )
    runs, pipelines, _res = await _make_group(session, org=org, pipelines=[pipeline])
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipelines[0].id)
            )
        )
        .scalars()
        .all()
    )
    by_batch = {s.batch_index: s for s in stages if s.stage_type == AIPipelineStageType.PERSONA_BEHAVIOR}
    assert by_batch[0].status == AIPipelineStageStatus.SUCCEEDED  # basarili batch korunur
    assert by_batch[1].status == AIPipelineStageStatus.QUEUED  # yalnizca basarisiz batch requeue


async def test_retry_non_retryable_integrity_no_change(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, old_res = await _make_group(
        session, org=org, pipelines=[_failed_pipeline(error_code="provider_invalid_output")]
    )
    balance_before = await chip_ledger.get_chip_balance(session, org.id)
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert exc.value.code == mutations.ERROR_RETRY_NON_RETRYABLE
    assert pipelines[0].status == AIPipelineStatus.FAILED
    assert runs[0].ai_chip_reservation_id == old_res.id
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before


async def test_retry_failed_stage_with_validated_output_is_integrity(session: AsyncSession):
    org = await _make_org(session)
    pipeline = PipelineSpec(
        status=AIPipelineStatus.FAILED,
        stages=[
            StageSpec(
                AIPipelineStageType.SCENARIO_INTERPRETATION,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
                validated_output={"unexpected": "data"},
            ),
        ],
    )
    runs, _pipelines, _res = await _make_group(session, org=org, pipelines=[pipeline])
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert exc.value.code == mutations.ERROR_RETRY_INTEGRITY


async def test_retry_all_succeeded_group_conflict(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[_succeeded_pipeline()],
        reservation_status=ChipReservationStatus.CONSUMED,
    )
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    # Ya nothing_to_retry ya reservation_consumed - ikisi de guvenli conflict.
    assert exc.value.code in {
        mutations.ERROR_RETRY_NOTHING_TO_RETRY,
        mutations.ERROR_RETRY_RESERVATION_CONSUMED,
    }


async def test_retry_active_queued_running_group_conflict(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert exc.value.code == mutations.ERROR_RETRY_PIPELINE_ACTIVE


async def test_retry_wrong_tenant_not_found(session: AsyncSession):
    org = await _make_org(session)
    other = await _make_org(session)
    runs, _pipelines, _res = await _make_group(session, org=org, pipelines=[_failed_pipeline()])
    with pytest.raises(mutations.SimulationRunNotFoundError):
        await mutations.retry_ai_pipeline_group(
            session, organization_id=other.id, simulation_run_id=runs[0].id
        )


async def test_retry_missing_run_same_not_found(session: AsyncSession):
    org = await _make_org(session)
    with pytest.raises(mutations.SimulationRunNotFoundError):
        await mutations.retry_ai_pipeline_group(
            session, organization_id=org.id, simulation_run_id=uuid.uuid4()
        )


# =============================================================================
# CANCEL
# =============================================================================


async def test_cancel_queued_group_becomes_cancelled(session: AsyncSession):
    org = await _make_org(session)
    pipeline = PipelineSpec(
        status=AIPipelineStatus.RUNNING,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.QUEUED),
        ],
    )
    runs, pipelines, res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    result = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert pipelines[0].status == AIPipelineStatus.CANCELLED
    assert pipelines[0].cancel_requested is True
    assert result.cancelled_pipeline_count == 1
    assert result.cancelled_stage_count == 1


async def test_cancel_releases_reserved_reservation(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, res = await _make_group(
        session,
        org=org,
        pipelines=[PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    balance_before = await chip_ledger.get_chip_balance(session, org.id)
    result = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert result.released_chips == AI_REPORT_CHIP_COST
    assert await _reservation_status(session, res.id) == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before + AI_REPORT_CHIP_COST


async def test_cancel_ab_all_nonterminal_cancelled(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[
            PipelineSpec(status=AIPipelineStatus.RUNNING),
            PipelineSpec(status=AIPipelineStatus.QUEUED),
        ],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert pipelines[0].status == AIPipelineStatus.CANCELLED
    assert pipelines[1].status == AIPipelineStatus.CANCELLED


async def test_cancel_preserves_succeeded_sibling(session: AsyncSession):
    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[_succeeded_pipeline(), PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[1].id)
    assert pipelines[0].status == AIPipelineStatus.SUCCEEDED  # terminal succeeded korunur
    assert pipelines[1].status == AIPipelineStatus.CANCELLED


async def test_duplicate_cancel_no_second_release(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, res = await _make_group(
        session,
        org=org,
        pipelines=[PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    first = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    balance_after_first = await chip_ledger.get_chip_balance(session, org.id)
    second = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert first.released_chips == AI_REPORT_CHIP_COST
    assert second.released_chips == 0  # ikinci release URETMEZ
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_after_first


async def test_cancel_released_reservation_idempotent(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, res = await _make_group(
        session,
        org=org,
        pipelines=[PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RELEASED,
    )
    result = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert result.released_chips == 0
    assert await _reservation_status(session, res.id) == ChipReservationStatus.RELEASED


async def test_cancel_consumed_succeeded_rejected(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[_succeeded_pipeline()],
        reservation_status=ChipReservationStatus.CONSUMED,
    )
    with pytest.raises(mutations.AIPipelineGroupConflictError) as exc:
        await mutations.cancel_ai_pipeline_group(
            session, organization_id=org.id, simulation_run_id=runs[0].id
        )
    assert exc.value.code == mutations.ERROR_CANCEL_ALREADY_COMPLETED


async def test_cancel_does_not_touch_baseline_run(session: AsyncSession):
    org = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    baseline_status_before = runs[0].status
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert runs[0].status == baseline_status_before  # SimulationRun SUCCEEDED kalir


async def test_cancel_wrong_tenant_not_found(session: AsyncSession):
    org = await _make_org(session)
    other = await _make_org(session)
    runs, _pipelines, _res = await _make_group(
        session,
        org=org,
        pipelines=[PipelineSpec(status=AIPipelineStatus.RUNNING)],
        reservation_status=ChipReservationStatus.RESERVED,
    )
    with pytest.raises(mutations.SimulationRunNotFoundError):
        await mutations.cancel_ai_pipeline_group(
            session, organization_id=other.id, simulation_run_id=runs[0].id
        )


async def test_cancel_succeeded_stage_output_preserved(session: AsyncSession):
    org = await _make_org(session)
    pipeline = PipelineSpec(
        status=AIPipelineStatus.RUNNING,
        stages=[
            StageSpec(
                AIPipelineStageType.EVIDENCE_PREPARATION,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output={"kept": "value"},
            ),
            StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.QUEUED),
        ],
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipelines[0].id)
            )
        )
        .scalars()
        .all()
    )
    evidence = next(s for s in stages if s.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION)
    scenario = next(s for s in stages if s.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert evidence.status == AIPipelineStageStatus.SUCCEEDED
    assert evidence.validated_output == {"kept": "value"}
    assert scenario.status == AIPipelineStageStatus.CANCELLED


# =============================================================================
# HTTP: TENANT / AUTH / CSRF (client fixture + engine seeding)
# =============================================================================


def _register(client: TestClient) -> dict:
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
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
    token = next((c.value for c in client.cookies.jar if c.name == "csrf_token"), None)
    assert token, "csrf_token cookie set olmali"
    return {"X-CSRF-Token": token}


def _snapshot_cookies(client: TestClient) -> list[tuple[str, str, str, str]]:
    return [(c.name, c.value, c.path, c.domain) for c in client.cookies.jar]


def _restore_cookies(client: TestClient, snapshot: list[tuple[str, str, str, str]]) -> None:
    client.cookies.clear()
    for name, value, path, domain in snapshot:
        client.cookies.set(name, value, domain=domain, path=path)


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "mutasyon testi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _launch_single(client: TestClient) -> str:
    _register(client)
    project = _create_project(client)
    draft = client.post("/api/tests/drafts", headers=_csrf_headers(client)).json()
    payload = {
        "project_id": project["id"],
        "name": f"mut testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": 1000,
        "target_audience": "Genel kullanicilar",
        "persona_preset_id": personas_service.builtin_preset_id("general_web_users"),
        "authorization_confirmed": True,
        "modules": [],
    }
    resp = client.patch(
        f"/api/tests/drafts/{draft['id']}", json={"payload": payload}, headers=_csrf_headers(client)
    )
    assert resp.status_code == 200, resp.text
    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    return launch.json()["simulation_run_ids"][0]


async def _seed_ai_group_via_engine(
    run_id: str, *, reservation_status: ChipReservationStatus, pipeline_status: AIPipelineStatus
) -> None:
    """Var olan bir run'i, RELEASED/RESERVED AI rezervasyonlu + tek FAILED/RUNNING
    pipeline'li bir AI grubuna donusturur (org'a Chip kredisi ekler)."""

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            org_id = run.organization_id
            await chip_ledger.credit(session, org_id, 1000, "test seed")
            res = await chip_ledger.reserve_chips(
                session, org_id, AI_REPORT_CHIP_COST, "ai reserve", idempotency_key=f"seed:{run_id}"
            )
            if reservation_status == ChipReservationStatus.RELEASED:
                await chip_ledger.release_reservation(session, org_id, res.id, "seed release")
            snapshot = dict(run.input_snapshot or {})
            snapshot["modules"] = ["ai_report"]
            run.input_snapshot = snapshot
            run.ai_chip_reservation_id = res.id
            pipeline = AIPipelineRun(
                organization_id=org_id,
                simulation_run_id=run.id,
                status=pipeline_status,
            )
            session.add(pipeline)
            await session.flush()
            failed = pipeline_status == AIPipelineStatus.FAILED
            session.add(
                AIPipelineStage(
                    ai_pipeline_run_id=pipeline.id,
                    stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
                    status=(AIPipelineStageStatus.FAILED if failed else AIPipelineStageStatus.QUEUED),
                    attempt_count=1 if failed else 0,
                    input_hash="0" * 64,
                    idempotency_key=uuid.uuid4().hex,
                    error_code="provider_timeout" if failed else None,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _count_ledger_rows() -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return (await session.execute(select(func.count()).select_from(ChipLedgerEntry))).scalar_one()
    finally:
        await engine.dispose()


def test_http_same_org_can_retry(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RELEASED,
        pipeline_status=AIPipelineStatus.FAILED,
    )
    resp = client.post(f"/api/simulations/runs/{run_id}/ai-pipeline/retry", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["charged_chips"] == AI_REPORT_CHIP_COST
    assert body["manual_retry_count"] == 1
    assert body["requeued_pipeline_count"] == 1


def test_http_same_org_can_cancel(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RESERVED,
        pipeline_status=AIPipelineStatus.RUNNING,
    )
    resp = client.post(f"/api/simulations/runs/{run_id}/ai-pipeline/cancel", headers=_csrf_headers(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cancelled_pipeline_count"] == 1
    assert body["released_chips"] == AI_REPORT_CHIP_COST


@pytest.mark.security
def test_http_other_org_gets_404_retry(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RELEASED,
        pipeline_status=AIPipelineStatus.FAILED,
    )
    _register(client)  # org B
    resp = client.post(f"/api/simulations/runs/{run_id}/ai-pipeline/retry", headers=_csrf_headers(client))
    assert resp.status_code == 404


@pytest.mark.security
def test_http_cross_tenant_retry_no_chip_movement(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RELEASED,
        pipeline_status=AIPipelineStatus.FAILED,
    )
    before = _arun(_count_ledger_rows)
    _register(client)  # org B
    assert (
        client.post(
            f"/api/simulations/runs/{run_id}/ai-pipeline/retry", headers=_csrf_headers(client)
        ).status_code
        == 404
    )
    after = _arun(_count_ledger_rows)
    assert before == after


def test_http_retry_without_csrf_rejected(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RELEASED,
        pipeline_status=AIPipelineStatus.FAILED,
    )
    resp = client.post(f"/api/simulations/runs/{run_id}/ai-pipeline/retry")  # CSRF header YOK
    assert resp.status_code == 403


def test_http_cancel_without_csrf_rejected(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RESERVED,
        pipeline_status=AIPipelineStatus.RUNNING,
    )
    resp = client.post(f"/api/simulations/runs/{run_id}/ai-pipeline/cancel")  # CSRF header YOK
    assert resp.status_code == 403


def test_http_unauthenticated_rejected(client: TestClient):
    resp = client.post(f"/api/simulations/runs/{uuid.uuid4()}/ai-pipeline/retry")
    assert resp.status_code in {401, 403}


def test_http_retry_response_omits_internal_ids(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_ai_group_via_engine,
        run_id,
        reservation_status=ChipReservationStatus.RELEASED,
        pipeline_status=AIPipelineStatus.FAILED,
    )
    raw = client.post(
        f"/api/simulations/runs/{run_id}/ai-pipeline/retry", headers=_csrf_headers(client)
    ).text.lower()
    for banned in ("reservation_id", "idempotency", "input_hash", "prompt", "provider", "ledger"):
        assert banned not in raw, f"yasakli alan sizdi: {banned}"


def test_http_openapi_exposes_two_post_endpoints(client: TestClient):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "post" in paths["/api/simulations/runs/{run_id}/ai-pipeline/retry"]
    assert "post" in paths["/api/simulations/runs/{run_id}/ai-pipeline/cancel"]


@pytest.mark.security
@pytest.mark.parametrize("action", ["retry", "cancel"])
def test_http_viewer_role_rejected(client: TestClient, action: str):
    """Viewer rolu retry/cancel yapamaz (require_roles(*WRITE_ROLES))."""

    from app.dependencies import Principal, get_current_principal, verify_csrf
    from app.main import app

    viewer = Principal(user_id=uuid.uuid4(), organization_id=uuid.uuid4(), role="viewer")
    app.dependency_overrides[get_current_principal] = lambda: viewer
    app.dependency_overrides[verify_csrf] = lambda: None
    try:
        resp = client.post(f"/api/simulations/runs/{uuid.uuid4()}/ai-pipeline/{action}")
    finally:
        app.dependency_overrides.pop(get_current_principal, None)
        app.dependency_overrides.pop(verify_csrf, None)
    assert resp.status_code == 403
