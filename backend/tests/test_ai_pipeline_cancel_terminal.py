"""Faz 3C.3 SORUN 2: cancel altinda ORFAN RUNNING stage terminalizasyonu.

`cancel_ai_pipeline_group` artik NONTERMINAL (QUEUED + RUNNING) stage'lerin
TAMAMINI `worker.mark_stage_cancelled` ile ATOMIK olarak CANCELLED yapar -
CANCELLED bir pipeline altinda ORFAN bir RUNNING stage KALMAZ. Bir RUNNING
stage'in gec provider sonucu iki katmanli CAS ile reddedilir (stage.status artik
RUNNING degil + pipeline CANCELLED/jenerasyon; bkz. worker._relock_for_persist).

Session-seviyesi testler mutations `_make_group`/`_make_org` yardimcilarini
yeniden kullanir; late-result testi GERCEK commit eden worker session'larini
kullanir (test_ai_pipeline_worker/_retry_stale yardimcilariyla).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker

from app.models.ai_pipeline import (
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.billing import ChipReservationStatus
from app.models.personas import Persona
from app.models.simulations import SimulationRun
from app.services import chip_ledger
from app.services.ai_pipeline import mutations, queries
from app.services.ai_pipeline import worker as wk
from app.services.ai_pipeline.worker import (
    ERROR_STALE_EXECUTION_CANCELLED,
    AIStageOutcome,
    execute_claimed_ai_stage,
    persist_stage_success,
)
from app.services.pricing import AI_REPORT_CHIP_COST
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables
from tests.test_ai_pipeline_mutations import (
    PipelineSpec,
    StageSpec,
    _make_group,
    _make_org,
)
from tests.test_ai_pipeline_retry_stale import (
    _advance_pure_prefix,
    _claim_one,
    _prepare,
    _stage3_queued_row,
)
from tests.test_ai_pipeline_worker import CountingMockProvider, _get_pipeline, _seed_pipeline, _stages

pytestmark = pytest.mark.integration


@pytest.fixture
async def maker(test_engine):
    """Commit eden, ayri DB session'lari acan session_maker (bkz. retry_stale)."""

    session_maker = _async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


def _running_pipeline_with(stages: list[StageSpec]) -> PipelineSpec:
    return PipelineSpec(status=AIPipelineStatus.RUNNING, stages=stages)


async def _stages_of(session: AsyncSession, pipeline_id: uuid.UUID) -> list[AIPipelineStage]:
    return list(
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline_id)
            )
        )
        .scalars()
        .all()
    )


# =================================================================================
# CANCEL (12)
# =================================================================================


async def test_cancel_queued_stage_becomes_cancelled(session: AsyncSession):  # (1)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.QUEUED)]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages = await _stages_of(session, pipelines[0].id)
    assert all(s.status == AIPipelineStageStatus.CANCELLED for s in stages)


async def test_cancel_running_stage_becomes_cancelled(session: AsyncSession):  # (2)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING)]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    result = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    stages = await _stages_of(session, pipelines[0].id)
    assert stages[0].status == AIPipelineStageStatus.CANCELLED
    assert result.cancelled_stage_count == 1


async def test_no_running_stage_remains_under_cancelled_pipeline(session: AsyncSession):  # (3)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(AIPipelineStageType.PERSONA_BATCH_PREPARATION, AIPipelineStageStatus.QUEUED),
            StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING),
        ]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages = await _stages_of(session, pipelines[0].id)
    assert not any(s.status == AIPipelineStageStatus.RUNNING for s in stages)
    assert pipelines[0].status == AIPipelineStatus.CANCELLED


async def test_cancel_preserves_succeeded_stage(session: AsyncSession):  # (4)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [
            StageSpec(
                AIPipelineStageType.EVIDENCE_PREPARATION,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output={"kept": "value"},
            ),
            StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING),
        ]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    stages = await _stages_of(session, pipelines[0].id)
    evidence = next(s for s in stages if s.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION)
    scenario = next(s for s in stages if s.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert evidence.status == AIPipelineStageStatus.SUCCEEDED
    assert evidence.validated_output == {"kept": "value"}
    assert scenario.status == AIPipelineStageStatus.CANCELLED


async def test_cancel_running_stage_field_contract(session: AsyncSession):  # (5)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING)]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    # RUNNING stage'i pinlenmis (provider/model/execution key + attempt_count) yap.
    stage = (await _stages_of(session, pipelines[0].id))[0]
    stage.provider = "mock"
    stage.model_name = "mock-model"
    stage.execution_idempotency_key = "exec:pinned:running"
    stage.attempt_count = 1
    stage.started_at = mutations._now()
    await session.flush()

    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    fresh = (await _stages_of(session, pipelines[0].id))[0]
    assert fresh.status == AIPipelineStageStatus.CANCELLED
    assert fresh.error_code == ERROR_STALE_EXECUTION_CANCELLED
    assert fresh.finished_at is not None
    # KORUNANLAR: attempt_count / provider / model / execution key / validated_output.
    assert fresh.attempt_count == 1
    assert fresh.provider == "mock"
    assert fresh.model_name == "mock-model"
    assert fresh.execution_idempotency_key == "exec:pinned:running"
    assert fresh.validated_output is None


async def test_duplicate_cancel_idempotent_with_running(session: AsyncSession):  # (6)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING)]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    first = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    second = await mutations.cancel_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert first.cancelled_stage_count == 1
    assert second.cancelled_stage_count == 0  # ikinci cagri stage'e dokunmaz
    assert first.released_chips == AI_REPORT_CHIP_COST
    assert second.released_chips == 0  # ikinci release URETMEZ


async def test_cancel_releases_reservation_once(session: AsyncSession):  # (7)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING)]
    )
    runs, _pipelines, res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    balance_before = await chip_ledger.get_chip_balance(session, org.id)
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    fresh_res = await session.get(type(res), res.id)
    assert fresh_res.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, org.id) == balance_before + AI_REPORT_CHIP_COST


async def test_readonly_status_shows_no_running_after_cancel(session: AsyncSession):  # (8)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING),
        ]
    )
    runs, _pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    # get_ai_pipeline_status'un batch sayimi butunluk kontrolu icin kalici Persona.
    session.add(
        Persona(
            simulation_run_id=runs[0].id,
            index=0,
            label="P0",
            attributes={"age_range": "25_34"},
            population_weight=1,
        )
    )
    await session.flush()
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    status = await queries.get_ai_pipeline_status(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert status.status == AIPipelineStatus.CANCELLED.value
    assert status.running_stage_count == 0
    assert all(s.status != AIPipelineStageStatus.RUNNING.value for s in status.stages)


async def test_cancel_then_paid_retry_requeues_safely(session: AsyncSession):  # (9)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.SCENARIO_INTERPRETATION,
                AIPipelineStageStatus.RUNNING,
                error_code=None,
            ),
        ]
    )
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    # Cancel edilen (RUNNING->CANCELLED) stage error_code'u retryable allowlist'te
    # (stale_execution_cancelled) oldugu icin ucretli manuel retry guvenli requeue.
    result = await mutations.retry_ai_pipeline_group(
        session, organization_id=org.id, simulation_run_id=runs[0].id
    )
    assert result.charged_chips == AI_REPORT_CHIP_COST
    stages = await _stages_of(session, pipelines[0].id)
    scenario = next(s for s in stages if s.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert scenario.status == AIPipelineStageStatus.QUEUED
    assert scenario.attempt_count == 0


async def test_cancel_does_not_touch_baseline_or_persona(session: AsyncSession):  # (10)/(11)
    org = await _make_org(session)
    pipeline = _running_pipeline_with(
        [StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING)]
    )
    runs, _pipelines, _res = await _make_group(
        session, org=org, pipelines=[pipeline], reservation_status=ChipReservationStatus.RESERVED
    )
    # Baseline SimulationRun SUCCEEDED + bir Persona ekle.
    persona = Persona(
        simulation_run_id=runs[0].id,
        index=0,
        label="P0",
        attributes={"age_range": "25_34"},
        population_weight=1,
    )
    session.add(persona)
    await session.flush()
    baseline_status = runs[0].status
    persona_attrs = dict(persona.attributes)

    await mutations.cancel_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[0].id)
    assert runs[0].status == baseline_status  # SimulationRun SUCCEEDED korunur
    fresh_persona = await session.get(Persona, persona.id)
    assert fresh_persona is not None
    assert fresh_persona.attributes == persona_attrs  # Persona DEGISMEZ


async def _augment_and_cancel(maker, org_id, run_id):
    """Maker-seeded run'i AI grubuna (RESERVED rezervasyon) donusturur ve GERCEK
    `cancel_ai_pipeline_group`i AYRI commit edilmis bir session'da calistirir."""

    async with maker() as s:
        run = (await s.execute(select(SimulationRun).where(SimulationRun.id == run_id))).scalar_one()
        await chip_ledger.credit(s, org_id, 1000, "seed credit")
        res = await chip_ledger.reserve_chips(
            s, org_id, AI_REPORT_CHIP_COST, "ai reserve", idempotency_key=f"seed-cancel:{run_id}"
        )
        run.ai_chip_reservation_id = res.id
        await s.commit()
    async with maker() as s:
        result = await mutations.cancel_ai_pipeline_group(s, organization_id=org_id, simulation_run_id=run_id)
        await s.commit()
        return result


async def test_cancel_late_running_provider_result_rejected(maker):  # (12)
    """GERCEK worker: Stage 3'u claim+pin+execute et (RUNNING, persist YOK); AYRI
    session'da GERCEK cancel calistir (RUNNING->CANCELLED, pipeline CANCELLED); gec
    gelen provider sonucu STALE reddedilir - cikti/token/successor DEGISMEZ."""

    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    claimedA = await _claim_one(maker)  # Stage 3 RUNNING, gen 0, attempt 1
    assert claimedA.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION
    assert (await wk._pin_provider_execution(maker, claimedA, provider=provider)) is wk._PinResult.OK
    planA = await _prepare(maker, claimedA)
    resultA = await execute_claimed_ai_stage(claimedA, planA, provider=provider)

    cancel_result = await _augment_and_cancel(maker, env.org_id, env.run_id)
    assert cancel_result.cancelled_stage_count == 1  # RUNNING Stage 3 terminalize edildi

    before = await _get_pipeline(maker, env.pipeline_id)
    tin, tout = before.total_input_tokens, before.total_output_tokens

    rA = await persist_stage_success(maker, claimedA, resultA, provider=provider)
    assert rA.outcome == AIStageOutcome.STALE_RESULT_REJECTED

    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.CANCELLED
    assert stage3.validated_output is None
    after = await _get_pipeline(maker, env.pipeline_id)
    assert (after.total_input_tokens, after.total_output_tokens) == (tin, tout)
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert behavior == []
