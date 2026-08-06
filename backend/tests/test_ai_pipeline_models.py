"""Faz 1: `AIPipelineRun`/`AIPipelineStage` veri modeli testleri.

Bu dosya yalnizca modeli (kisit/indeks/cascade/iliski) test eder - provider,
prompt, worker veya orkestrasyon bu asamanin (Faz 1) kapsami disindadir
(bkz. app.models.ai_pipeline modul dokstring'i).
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AIPipelineRun, Organization, Project, SimulationRun, TestDefinition, TestVariant
from app.models.ai_pipeline import (
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)

pytestmark = pytest.mark.integration


async def _make_simulation_run(session: AsyncSession) -> tuple[SimulationRun, uuid.UUID]:
    org = Organization(name="AI Pipeline Org", slug=f"ai-pipeline-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()

    project = Project(organization_id=org.id, name="AI Pipeline Project")
    session.add(project)
    await session.flush()

    test_definition = TestDefinition(organization_id=org.id, project_id=project.id, name="AI Pipeline Test")
    session.add(test_definition)
    await session.flush()

    variant = TestVariant(
        organization_id=org.id,
        test_definition_id=test_definition.id,
        name="Variant A",
        config={},
    )
    session.add(variant)
    await session.flush()

    run = SimulationRun(
        organization_id=org.id,
        test_variant_id=variant.id,
        deterministic_seed=42,
        model_version="v0",
        input_snapshot={},
    )
    session.add(run)
    await session.flush()
    return run, org.id


def _idempotency_key(*parts: str) -> str:
    return ":".join(parts)


# --- AIPipelineRun -----------------------------------------------------------


async def test_ai_pipeline_run_is_created_with_expected_defaults(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)

    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id)
    session.add(pipeline)
    await session.flush()
    await session.refresh(pipeline)

    assert pipeline.status == AIPipelineStatus.QUEUED
    assert pipeline.cancel_requested is False
    assert pipeline.total_input_tokens == 0
    assert pipeline.total_output_tokens == 0
    assert pipeline.estimated_cost is None
    assert pipeline.started_at is None
    assert pipeline.finished_at is None


async def test_ai_pipeline_run_simulation_run_id_is_unique(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)
    session.add(AIPipelineRun(organization_id=org_id, simulation_run_id=run.id))
    await session.flush()

    session.add(AIPipelineRun(organization_id=org_id, simulation_run_id=run.id))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_different_simulation_runs_can_each_have_their_own_pipeline(session: AsyncSession):
    run_a, org_id = await _make_simulation_run(session)
    run_b, _ = await _make_simulation_run(session)

    session.add(AIPipelineRun(organization_id=org_id, simulation_run_id=run_a.id))
    session.add(AIPipelineRun(organization_id=org_id, simulation_run_id=run_b.id))
    await session.flush()  # raise etmemeli


async def test_ai_pipeline_run_organization_scope_matches_simulation_run(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)
    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id)
    session.add(pipeline)
    await session.flush()
    await session.refresh(pipeline)

    assert pipeline.organization_id == run.organization_id == org_id


@pytest.mark.parametrize(
    "status",
    [
        AIPipelineStatus.QUEUED,
        AIPipelineStatus.RUNNING,
        AIPipelineStatus.SUCCEEDED,
        AIPipelineStatus.FAILED,
        AIPipelineStatus.PARTIAL,
        AIPipelineStatus.CANCELLED,
    ],
)
async def test_all_valid_ai_pipeline_statuses_can_be_stored(session: AsyncSession, status: AIPipelineStatus):
    run, org_id = await _make_simulation_run(session)
    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id, status=status)
    session.add(pipeline)
    await session.flush()
    pipeline_id = pipeline.id
    session.expunge(pipeline)

    reloaded = await session.get(AIPipelineRun, pipeline_id)
    assert reloaded is not None
    assert reloaded.status == status


async def test_ai_pipeline_run_negative_total_input_tokens_is_rejected(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)
    session.add(AIPipelineRun(organization_id=org_id, simulation_run_id=run.id, total_input_tokens=-1))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_ai_pipeline_run_negative_total_output_tokens_is_rejected(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)
    session.add(AIPipelineRun(organization_id=org_id, simulation_run_id=run.id, total_output_tokens=-1))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_ai_pipeline_run_deleted_when_simulation_run_is_deleted(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)
    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id)
    session.add(pipeline)
    await session.flush()
    pipeline_id = pipeline.id

    await session.delete(run)
    await session.flush()

    result = await session.execute(select(AIPipelineRun).where(AIPipelineRun.id == pipeline_id))
    assert result.scalar_one_or_none() is None


# --- AIPipelineStage -----------------------------------------------------------


async def _make_pipeline(session: AsyncSession) -> AIPipelineRun:
    run, org_id = await _make_simulation_run(session)
    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id)
    session.add(pipeline)
    await session.flush()
    return pipeline


async def test_non_batch_stage_has_null_batch_index(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    stage = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        batch_index=None,
        input_hash="a" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "evidence_preparation"),
    )
    session.add(stage)
    await session.flush()
    await session.refresh(stage)

    assert stage.batch_index is None
    assert stage.status == AIPipelineStageStatus.QUEUED
    assert stage.attempt_count == 0


async def test_persona_behavior_stage_batch_index_zero_is_preserved(session: AsyncSession):
    """batch_index=0, `None`/falsy ile karistirilmamali (0 gecerli bir indekstir)."""

    pipeline = await _make_pipeline(session)
    stage = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=0,
        input_hash="b" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "persona_behavior", "0"),
    )
    session.add(stage)
    await session.flush()
    stage_id = stage.id
    session.expunge(stage)

    reloaded = await session.get(AIPipelineStage, stage_id)
    assert reloaded is not None
    assert reloaded.batch_index == 0
    assert reloaded.batch_index is not None


async def test_multiple_batch_indexes_can_be_created_in_same_pipeline(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    for batch_index in range(3):
        session.add(
            AIPipelineStage(
                ai_pipeline_run_id=pipeline.id,
                stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
                batch_index=batch_index,
                input_hash="c" * 64,
                idempotency_key=_idempotency_key(str(pipeline.id), "persona_behavior", str(batch_index)),
            )
        )
    await session.flush()  # raise etmemeli

    result = await session.execute(
        select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline.id)
    )
    assert sorted(s.batch_index for s in result.scalars().all()) == [0, 1, 2]


async def test_duplicate_idempotency_key_is_rejected(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    key = _idempotency_key(str(pipeline.id), "aggregation")
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.AGGREGATION,
            input_hash="d" * 64,
            idempotency_key=key,
        )
    )
    await session.flush()

    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.AGGREGATION,
            input_hash="e" * 64,
            idempotency_key=key,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_same_stage_type_and_batch_index_with_different_idempotency_key_is_allowed(
    session: AsyncSession,
):
    """Ayni asamanin YENI bir prompt/model surumuyle (farkli idempotency_key
    ile) yeniden calistirilip eskisi silinmeden yeni bir satir olarak
    saklanabilmesi gerekir (bkz. app.models.ai_pipeline.AIPipelineStage
    dokstring'i - kasitli olarak (pipeline, stage_type, batch_index) uzerinde
    bir UNIQUE constraint YOKTUR)."""

    pipeline = await _make_pipeline(session)
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.UX_REPORT,
            input_hash="f" * 64,
            prompt_version="ux-report-v1",
            idempotency_key=_idempotency_key(str(pipeline.id), "ux_report", "v1"),
        )
    )
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.UX_REPORT,
            input_hash="f" * 64,
            prompt_version="ux-report-v2",
            idempotency_key=_idempotency_key(str(pipeline.id), "ux_report", "v2"),
        )
    )
    await session.flush()  # raise etmemeli

    result = await session.execute(
        select(AIPipelineStage).where(
            AIPipelineStage.ai_pipeline_run_id == pipeline.id,
            AIPipelineStage.stage_type == AIPipelineStageType.UX_REPORT,
        )
    )
    assert len(result.scalars().all()) == 2


async def test_negative_attempt_count_is_rejected(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
            input_hash="a" * 64,
            idempotency_key=_idempotency_key(str(pipeline.id), "neg-attempt"),
            attempt_count=-1,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_negative_input_tokens_is_rejected(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
            input_hash="a" * 64,
            idempotency_key=_idempotency_key(str(pipeline.id), "neg-input-tokens"),
            input_tokens=-1,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_negative_output_tokens_is_rejected(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
            input_hash="a" * 64,
            idempotency_key=_idempotency_key(str(pipeline.id), "neg-output-tokens"),
            output_tokens=-1,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_negative_batch_index_is_rejected(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    session.add(
        AIPipelineStage(
            ai_pipeline_run_id=pipeline.id,
            stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
            input_hash="a" * 64,
            idempotency_key=_idempotency_key(str(pipeline.id), "neg-batch-index"),
            batch_index=-1,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_ai_pipeline_stage_deleted_when_pipeline_run_is_deleted(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    stage = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        input_hash="a" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "cascade-test"),
    )
    session.add(stage)
    await session.flush()
    stage_id = stage.id

    await session.delete(pipeline)
    await session.flush()

    result = await session.execute(select(AIPipelineStage).where(AIPipelineStage.id == stage_id))
    assert result.scalar_one_or_none() is None


async def test_ai_pipeline_stage_deleted_transitively_when_simulation_run_is_deleted(
    session: AsyncSession,
):
    """SimulationRun -> AIPipelineRun -> AIPipelineStage zincirinin TAMAMI
    tek bir SimulationRun silme isleminde CASCADE ile temizlenmelidir."""

    run, org_id = await _make_simulation_run(session)
    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id)
    session.add(pipeline)
    await session.flush()

    stage = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        input_hash="a" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "transitive-cascade"),
    )
    session.add(stage)
    await session.flush()
    pipeline_id, stage_id = pipeline.id, stage.id

    await session.delete(run)
    await session.flush()

    assert (
        await session.execute(select(AIPipelineRun).where(AIPipelineRun.id == pipeline_id))
    ).scalar_one_or_none() is None
    assert (
        await session.execute(select(AIPipelineStage).where(AIPipelineStage.id == stage_id))
    ).scalar_one_or_none() is None


async def test_validated_output_json_round_trip(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    payload = {"findings": [{"priority": "high", "finding": "ornek"}], "confidence": 0.72}
    stage = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.UX_REPORT,
        input_hash="a" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "json-roundtrip"),
        validated_output=payload,
    )
    session.add(stage)
    await session.flush()
    stage_id = stage.id
    session.expunge(stage)

    reloaded = await session.get(AIPipelineStage, stage_id)
    assert reloaded is not None
    assert reloaded.validated_output == payload


def test_model_has_no_raw_prompt_or_provider_response_columns():
    """Urun karari geregi: ham prompt/provider cevabi/HTML/DOM/PII/kimlik
    bilgisi ASLA bu modelde saklanmamalidir (bkz. AIPipelineStage
    dokstring'i)."""

    forbidden_column_names = {
        "raw_prompt",
        "system_prompt",
        "raw_provider_response",
        "raw_html",
        "raw_dom",
        "request_body",
        "response_body",
        "api_key",
        "authorization_header",
        "stack_trace",
    }
    actual_columns = set(AIPipelineStage.__table__.columns.keys())
    assert actual_columns.isdisjoint(forbidden_column_names)


# --- Iliskiler -----------------------------------------------------------------


async def test_simulation_run_ai_pipeline_relationship_access(session: AsyncSession):
    run, org_id = await _make_simulation_run(session)
    pipeline = AIPipelineRun(organization_id=org_id, simulation_run_id=run.id)
    session.add(pipeline)
    await session.flush()
    session.expire(run)

    await session.refresh(run, attribute_names=["ai_pipeline_run"])
    assert run.ai_pipeline_run is not None
    assert run.ai_pipeline_run.id == pipeline.id


async def test_ai_pipeline_run_stages_relationship_access(session: AsyncSession):
    pipeline = await _make_pipeline(session)
    stage_a = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        input_hash="a" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "rel-a"),
    )
    stage_b = AIPipelineStage(
        ai_pipeline_run_id=pipeline.id,
        stage_type=AIPipelineStageType.AGGREGATION,
        input_hash="b" * 64,
        idempotency_key=_idempotency_key(str(pipeline.id), "rel-b"),
    )
    session.add_all([stage_a, stage_b])
    await session.flush()
    session.expire(pipeline)

    await session.refresh(pipeline, attribute_names=["stages"])
    assert {s.id for s in pipeline.stages} == {stage_a.id, stage_b.id}
