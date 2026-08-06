"""Faz 3B.2B: DB-destekli AI pipeline worker (claim/execute/persist/DAG) testleri.

Fixture deseni: bu dosya `conftest.py`nin rollback eden `session` fixture'ini
KULLANMAZ - worker'in claim/persist akisi GERCEK, ayri commit'lenmis
transaction'lar gerektirir (claim, execution'dan ONCE commit edilir). Bunun
yerine `tests/test_persona_launch_persistence.py` ile ayni desen kullanilir:
test engine uzerine kurulu bir `async_sessionmaker` + test sonunda TUM
tablolarin `TRUNCATE ... CASCADE` ile temizlenmesi.

Test DB'si Postgres'tir (asyncpg) - bu nedenle `SELECT ... FOR UPDATE SKIP
LOCKED` es-zamanli claim davranisi GERCEK iki ayri baglanti/session ile
anlamli sekilde test edilir (bkz. `test_two_workers_cannot_claim_same_stage`).
"""

from __future__ import annotations

import inspect
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    AIPipelineRun,
    AIPipelineStage,
    Organization,
    Persona,
    Project,
    Report,
    SimulationRun,
    TestDefinition,
    TestVariant,
)
from app.models.ai_pipeline import AIPipelineStageStatus, AIPipelineStageType, AIPipelineStatus
from app.models.simulations import SimulationStatus
from app.services.ai_pipeline import worker as worker_module
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.orchestration import initialize_ai_pipeline
from app.services.ai_pipeline.persistence import (
    decode_persona_batch_manifest,
    decode_persona_behavior_batch,
    rehydrate_persona_batches,
)
from app.services.ai_pipeline.provider import ProviderResult
from app.services.ai_pipeline.provider_errors import AIProviderTransportError
from app.services.ai_pipeline.schemas import (
    ScenarioInterpretation,
    TaskStep,
)
from app.services.ai_pipeline.stage_hashing import deterministic_stage_idempotency_key
from app.services.ai_pipeline.stage_runner import run_evidence_stage
from app.services.ai_pipeline.stage_sources import (
    build_evidence_stage_source,
    evidence_stage_source_input_hash,
)
from app.services.ai_pipeline.worker import (
    AIStageOutcome,
    ClaimedAIStage,
    _prepare_stage_execution,
    claim_next_ai_stage,
    enqueue_ready_successors,
    execute_claimed_ai_stage,
    persist_stage_success,
    process_one_ai_stage,
)
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables

pytestmark = pytest.mark.integration


# --- Fixtures --------------------------------------------------------------------


@pytest.fixture
async def maker(test_engine):
    """Test engine uzerine kurulu, GERCEK commit eden bir sessionmaker; test
    sonunda tum tablolar TRUNCATE ile temizlenir."""

    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


# --- Test provider'lari ----------------------------------------------------------


class CountingMockProvider:
    """`MockAIProvider`i saran, provider cagrilarini kaydeden wrapper."""

    def __init__(self):
        self._inner = MockAIProvider()
        self.provider_name = self._inner.provider_name
        self.model_name = self._inner.model_name
        self.is_mock = self._inner.is_mock
        self.configuration_fingerprint = self._inner.configuration_fingerprint
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


class FaultyProvider:
    """Belirli bir stage'de tasima hatasi firlatan provider."""

    provider_name = "faulty"
    model_name = "faulty-v1"
    is_mock = False
    configuration_fingerprint = MockAIProvider.configuration_fingerprint

    def __init__(self, *, fail_stage):
        self._inner = MockAIProvider()
        self._fail_stage = fail_stage
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        if stage_type is self._fail_stage:
            raise AIProviderTransportError("test tasima hatasi")
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


class BrokenScenarioProvider:
    """Sema-gecerli ama evidence-referansi GECERSIZ bir ScenarioInterpretation
    donduren provider - runner'in re-validasyonunun yakalamasi beklenir."""

    provider_name = "broken"
    model_name = "broken-v1"
    is_mock = False
    configuration_fingerprint = MockAIProvider.configuration_fingerprint

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        broken = ScenarioInterpretation(
            steps=(
                TaskStep(
                    step_id="step:1",
                    instruction="x",
                    success_criterion="y",
                    evidence_references=("metric:does_not_exist",),
                ),
            ),
            success_criteria=("z",),
            friction_hypotheses=(),
            limitations="ok",
        )
        return ProviderResult(
            output=broken,
            provider_name=self.provider_name,
            model_name=self.model_name,
            is_mock=False,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
            request_duration_ms=0,
            provider_request_id=None,
        )


# --- Seed yardimcilari -----------------------------------------------------------


async def _seed_pipeline(
    maker,
    *,
    num_personas: int = 1,
    run_status: SimulationStatus = SimulationStatus.SUCCEEDED,
    with_report: bool = True,
    cancel_run: bool = False,
) -> SimpleNamespace:
    """Uygun bir SimulationRun + Report + Persona seti kurar ve
    `initialize_ai_pipeline` ile QUEUED bir EVIDENCE_PREPARATION stage'i olan
    bir pipeline olusturur; hepsini COMMIT eder."""

    persona_count = num_personas  # her persona population_weight=1
    async with maker() as s:
        org = Organization(name="W Org", slug=f"w-org-{uuid.uuid4().hex[:8]}")
        s.add(org)
        await s.flush()

        project = Project(organization_id=org.id, name="W Project")
        s.add(project)
        await s.flush()

        test_def = TestDefinition(organization_id=org.id, project_id=project.id, name="W Test")
        s.add(test_def)
        await s.flush()

        variant = TestVariant(
            organization_id=org.id, test_definition_id=test_def.id, name="Variant", config={}
        )
        s.add(variant)
        await s.flush()

        run = SimulationRun(
            organization_id=org.id,
            test_variant_id=variant.id,
            status=run_status,
            cancel_requested=False,
            deterministic_seed=7,
            model_version="v0",
            input_snapshot={
                "persona_count": persona_count,
                "source_type": "url",
                "modules": [],
                "wizard_test_type": "existing_site_basic_ux",
                "target_audience": "Genel kullanicilar",
            },
            result={
                "metrics": {
                    "task_completion_probability": {"point_estimate": 0.7},
                    "abandonment_probability": {"point_estimate": 0.2},
                    "contrast_check": {"pass": True, "avg_ratio": 4.5},
                },
                "page_feature_snapshot": {"nav_depth": 2, "primary_cta_count": 1},
            },
        )
        s.add(run)
        await s.flush()

        if with_report:
            s.add(Report(organization_id=org.id, simulation_run_id=run.id, title="R", content={}))
            await s.flush()

        for i in range(num_personas):
            s.add(
                Persona(
                    simulation_run_id=run.id,
                    index=i,
                    label=f"Persona {i}",
                    attributes={"age_range": "25_34"},
                    population_weight=1,
                )
            )
        await s.flush()

        pipeline = await initialize_ai_pipeline(
            s, organization_id=org.id, simulation_run_id=run.id, ai_requested=True
        )
        if cancel_run:
            run.cancel_requested = True
        await s.commit()

        return SimpleNamespace(
            org_id=org.id,
            run_id=run.id,
            pipeline_id=pipeline.id,
        )


async def _stages(maker, pipeline_id, *, stage_type=None, status=None) -> list[AIPipelineStage]:
    async with maker() as s:
        stmt = select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline_id)
        if stage_type is not None:
            stmt = stmt.where(AIPipelineStage.stage_type == stage_type)
        if status is not None:
            stmt = stmt.where(AIPipelineStage.status == status)
        stmt = stmt.order_by(AIPipelineStage.stage_type, AIPipelineStage.batch_index)
        return list((await s.execute(stmt)).scalars().all())


async def _get_stage(maker, stage_id) -> AIPipelineStage:
    async with maker() as s:
        return (await s.execute(select(AIPipelineStage).where(AIPipelineStage.id == stage_id))).scalar_one()


async def _get_pipeline(maker, pipeline_id) -> AIPipelineRun:
    async with maker() as s:
        return (await s.execute(select(AIPipelineRun).where(AIPipelineRun.id == pipeline_id))).scalar_one()


async def _get_run(maker, run_id) -> SimulationRun:
    async with maker() as s:
        return (await s.execute(select(SimulationRun).where(SimulationRun.id == run_id))).scalar_one()


async def _drive(maker, provider, *, max_steps: int = 80) -> list:
    results = []
    for _ in range(max_steps):
        r = await process_one_ai_stage(maker, provider=provider)
        results.append(r)
        if r.outcome == AIStageOutcome.NO_WORK:
            break
    return results


async def _claim_execute(maker, provider):
    """Claim (commit) + input hazirla + execute; (claimed, run_result) dondurur."""

    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    assert claimed is not None
    async with maker() as s:
        plan = await _prepare_stage_execution(s, claimed)
    run_result = await execute_claimed_ai_stage(claimed, plan, provider=provider)
    return claimed, run_result


# =================================================================================
# CLAIM
# =================================================================================


async def test_claim_sets_running(maker):  # (1)
    env = await _seed_pipeline(maker)
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    assert claimed is not None
    assert claimed.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION
    stage = await _get_stage(maker, claimed.stage_id)
    assert stage.status == AIPipelineStageStatus.RUNNING
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.RUNNING
    assert pipeline.started_at is not None


async def test_claim_increments_attempt_count(maker):  # (2)
    await _seed_pipeline(maker)
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    stage = await _get_stage(maker, claimed.stage_id)
    assert stage.attempt_count == 1
    assert claimed.claimed_attempt_count == 1


async def test_two_workers_cannot_claim_same_stage(maker):  # (3) SKIP LOCKED proof
    await _seed_pipeline(maker)
    async with maker() as s1, maker() as s2:
        c1 = await claim_next_ai_stage(s1)  # kilitler, henuz commit yok
        c2 = await claim_next_ai_stage(s2)  # SKIP LOCKED -> None
        assert c1 is not None
        assert c2 is None
        await s1.commit()


async def test_claim_is_deterministically_ordered(maker):  # (4)
    first = await _seed_pipeline(maker)  # once olusturulan pipeline
    await _seed_pipeline(maker)  # sonra olusturulan pipeline
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    # En eski pipeline (created_at ASC) once claim edilmeli.
    assert claimed.pipeline_id == first.pipeline_id


async def test_no_work_returns_no_work(maker):  # (5)
    result = await process_one_ai_stage(maker, provider=None)
    assert result.outcome == AIStageOutcome.NO_WORK


async def test_claim_transaction_releases_row_lock(maker):  # (6)
    await _seed_pipeline(maker)
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    # Claim commit edildikten SONRA satir uzerinde acik lock KALMAMALI:
    # ayri bir session FOR UPDATE NOWAIT ile satiri kilitleyebilmeli.
    async with maker() as s:
        row = (
            await s.execute(
                select(AIPipelineStage)
                .where(AIPipelineStage.id == claimed.stage_id)
                .with_for_update(nowait=True)
            )
        ).scalar_one()
        assert row.status == AIPipelineStageStatus.RUNNING
        await s.rollback()


# =================================================================================
# STAGE EXECUTION
# =================================================================================


async def test_stage1_uses_shared_source_helper(maker):  # (7)
    env = await _seed_pipeline(maker)
    result = await process_one_ai_stage(maker, provider=None)
    assert result.outcome == AIStageOutcome.SUCCEEDED
    assert result.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION

    run = await _get_run(maker, env.run_id)
    source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
    expected_input_hash = evidence_stage_source_input_hash(source)
    stage = await _get_stage(maker, result.stage_id)
    assert stage.input_hash == expected_input_hash


async def test_stage1_queued_key_matches_runner_audit_key(maker):  # (8)
    env = await _seed_pipeline(maker)
    run = await _get_run(maker, env.run_id)
    source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
    input_hash = evidence_stage_source_input_hash(source)
    expected_key = deterministic_stage_idempotency_key(
        simulation_run_id=env.run_id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        input_hash=input_hash,
    )
    # Runner'in ic olarak urettigi audit key ile QUEUED key BIREBIR ayni olmali.
    runner_result = run_evidence_stage(
        simulation_run_id=env.run_id,
        source_type=source.source_type,
        metrics=source.metrics,
        page_features=source.page_features,
        selected_modules=source.selected_modules,
        module_results=source.module_results,
    )
    assert runner_result.audit.idempotency_key == expected_key

    # process_one basariyla yazdiysa (FAILED degil) strict eslesme dogrulanmis demektir.
    result = await process_one_ai_stage(maker, provider=None)
    assert result.outcome == AIStageOutcome.SUCCEEDED
    stage = await _get_stage(maker, result.stage_id)
    assert stage.idempotency_key == expected_key


async def test_stage1_and_stage2_run_without_provider(maker):  # (9) - Stage 1/2 provider'siz
    await _seed_pipeline(maker)
    r1 = await process_one_ai_stage(maker, provider=None)
    r2 = await process_one_ai_stage(maker, provider=None)
    assert r1.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION
    assert r1.outcome == AIStageOutcome.SUCCEEDED
    assert r2.stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION
    assert r2.outcome == AIStageOutcome.SUCCEEDED


async def test_pure_stages_never_call_provider(maker):  # (9) - Stage 5 dahil
    await _seed_pipeline(maker, num_personas=1)
    provider = CountingMockProvider()
    await _drive(maker, provider)
    called_stage_types = {c[0] for c in provider.calls}
    assert called_stage_types <= {
        AIPipelineStageType.SCENARIO_INTERPRETATION,
        AIPipelineStageType.PERSONA_BEHAVIOR,
        AIPipelineStageType.UX_REPORT,
    }
    # Saf asamalar (1/2/5) hicbir provider cagrisi uretmedi.
    assert AIPipelineStageType.AGGREGATION not in called_stage_types
    assert AIPipelineStageType.EVIDENCE_PREPARATION not in called_stage_types


async def test_provider_stages_make_one_call_each(maker):  # (10)
    await _seed_pipeline(maker, num_personas=1)
    provider = CountingMockProvider()
    await _drive(maker, provider)
    # 1 persona -> 1 batch -> scenario + 1 behavior + ux_report = 3 cagri.
    assert provider.calls == [
        (AIPipelineStageType.SCENARIO_INTERPRETATION, None),
        (AIPipelineStageType.PERSONA_BEHAVIOR, 0),
        (AIPipelineStageType.UX_REPORT, None),
    ]


async def test_stage2_output_has_no_persona_attributes(maker):  # (11)
    await _seed_pipeline(maker, num_personas=3)
    await process_one_ai_stage(maker, provider=None)  # Stage 1
    r2 = await process_one_ai_stage(maker, provider=None)  # Stage 2
    assert r2.stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION
    stage = await _get_stage(maker, r2.stage_id)
    payload = stage.validated_output
    assert payload["manifest_version"]
    for batch in payload["batches"]:
        for entry in batch["personas"]:
            assert "attributes" not in entry
            assert "label" not in entry
            assert set(entry) == {"persona_id", "index", "population_weight"}


async def test_stage4_processes_only_its_batch_index(maker):  # (12)
    await _seed_pipeline(maker, num_personas=16)  # 2 batch
    provider = MockAIProvider()
    # Stage 1,2,3'u calistir, sonra ilk behavior batch'i.
    seen_behavior = False
    for _ in range(20):
        r = await process_one_ai_stage(maker, provider=provider)
        if r.stage_type == AIPipelineStageType.PERSONA_BEHAVIOR:
            stage = await _get_stage(maker, r.stage_id)
            decoded = decode_persona_behavior_batch(stage.validated_output)
            assert decoded.batch_index == stage.batch_index
            # Yalnizca kendi batch'inin persona'larini isledi (<=15).
            assert len(decoded.persona_results) <= 15
            seen_behavior = True
            break
    assert seen_behavior


async def test_aggregation_needs_all_behavior_outputs(maker):  # (13)/(17)
    env = await _seed_pipeline(maker, num_personas=16)  # 2 batch
    provider = MockAIProvider()
    # Stage 1,2,3 + tek behavior batch isle, sonra dur.
    processed_behavior = 0
    for _ in range(20):
        r = await process_one_ai_stage(maker, provider=provider)
        if r.stage_type == AIPipelineStageType.PERSONA_BEHAVIOR:
            processed_behavior += 1
            if processed_behavior == 1:
                break
    # Yalnizca 1 behavior bitti -> AGGREGATION HENUZ olusmamali.
    agg = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.AGGREGATION)
    assert agg == []


# =================================================================================
# DAG
# =================================================================================


async def test_stage1_success_creates_only_stage2(maker):  # (14)
    env = await _seed_pipeline(maker)
    await process_one_ai_stage(maker, provider=None)
    all_stages = await _stages(maker, env.pipeline_id)
    types = sorted(s.stage_type.value for s in all_stages)
    assert types == ["evidence_preparation", "persona_batch_preparation"]


async def test_stage2_success_creates_only_stage3(maker):  # (15)
    env = await _seed_pipeline(maker)
    await process_one_ai_stage(maker, provider=None)
    await process_one_ai_stage(maker, provider=None)
    all_stages = await _stages(maker, env.pipeline_id)
    types = sorted(s.stage_type.value for s in all_stages)
    assert types == [
        "evidence_preparation",
        "persona_batch_preparation",
        "scenario_interpretation",
    ]


async def test_stage3_success_creates_correct_batch_count(maker):  # (16)
    env = await _seed_pipeline(maker, num_personas=16)  # 2 batch
    provider = MockAIProvider()
    for _ in range(3):  # Stage 1,2,3
        await process_one_ai_stage(maker, provider=provider)
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert len(behavior) == 2
    assert sorted(s.batch_index for s in behavior) == [0, 1]
    assert all(s.status == AIPipelineStageStatus.QUEUED for s in behavior)


async def test_last_behavior_creates_single_aggregation(maker):  # (18)
    env = await _seed_pipeline(maker, num_personas=16)
    provider = MockAIProvider()
    await _drive(maker, provider)
    agg = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.AGGREGATION)
    assert len(agg) == 1


async def test_aggregation_success_creates_only_ux_report(maker):  # (19)
    env = await _seed_pipeline(maker, num_personas=1)
    provider = MockAIProvider()
    await _drive(maker, provider)
    ux = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.UX_REPORT)
    assert len(ux) == 1


async def test_ux_report_success_completes_pipeline(maker):  # (20)
    env = await _seed_pipeline(maker, num_personas=1)
    provider = MockAIProvider()
    await _drive(maker, provider)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED
    assert pipeline.finished_at is not None


async def test_successor_creation_is_idempotent(maker):  # (21)
    env = await _seed_pipeline(maker)
    r1 = await process_one_ai_stage(maker, provider=None)  # Stage 1 -> Stage 2

    # AYNI Stage 1 ciktisini (birebir ayni evidence source'tan) yeniden uret ve
    # enqueue_ready_successors'i ikinci kez cagir -> UNIQUE idempotency_key
    # sayesinde ikinci bir Stage 2 satiri OLUSMAMALI.
    run = await _get_run(maker, env.run_id)
    source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
    run_result = run_evidence_stage(
        simulation_run_id=env.run_id,
        source_type=source.source_type,
        metrics=source.metrics,
        page_features=source.page_features,
        selected_modules=source.selected_modules,
        module_results=source.module_results,
    )
    async with maker() as s:
        pipeline = (
            await s.execute(select(AIPipelineRun).where(AIPipelineRun.id == env.pipeline_id))
        ).scalar_one()
        stage1 = (
            await s.execute(select(AIPipelineStage).where(AIPipelineStage.id == r1.stage_id))
        ).scalar_one()
        claimed = ClaimedAIStage(
            pipeline_id=env.pipeline_id,
            stage_id=stage1.id,
            stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
            batch_index=None,
            claimed_attempt_count=stage1.attempt_count,
            claimed_manual_retry_count=pipeline.manual_retry_count,
            simulation_run_id=env.run_id,
            organization_id=env.org_id,
        )
        await enqueue_ready_successors(s, claimed=claimed, pipeline=pipeline, run_result=run_result)
        await s.commit()
    stage2 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BATCH_PREPARATION)
    assert len(stage2) == 1  # duplicate OLUSMADI


async def test_fan_out_order_is_deterministic(maker):  # (22)
    env = await _seed_pipeline(maker, num_personas=100)  # 7 batch
    provider = MockAIProvider()
    for _ in range(3):  # Stage 1,2,3
        await process_one_ai_stage(maker, provider=provider)
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert [s.batch_index for s in behavior] == list(range(7))


# =================================================================================
# CAS / LATE RESULT
# =================================================================================


async def test_wrong_attempt_count_rejected(maker):  # (23)
    await _seed_pipeline(maker)
    claimed, run_result = await _claim_execute(maker, provider=None)
    # attempt_count'u DB'de degistir (baska bir jenerasyon gibi).
    async with maker() as s:
        stage = (
            await s.execute(select(AIPipelineStage).where(AIPipelineStage.id == claimed.stage_id))
        ).scalar_one()
        stage.attempt_count = 99
        await s.commit()
    result = await persist_stage_success(maker, claimed, run_result, provider=None)
    assert result.outcome == AIStageOutcome.STALE_RESULT_REJECTED
    stage = await _get_stage(maker, claimed.stage_id)
    assert stage.status == AIPipelineStageStatus.RUNNING
    assert stage.validated_output is None


async def test_non_running_stage_rejected(maker):  # (24)
    await _seed_pipeline(maker)
    claimed, run_result = await _claim_execute(maker, provider=None)
    async with maker() as s:
        stage = (
            await s.execute(select(AIPipelineStage).where(AIPipelineStage.id == claimed.stage_id))
        ).scalar_one()
        stage.status = AIPipelineStageStatus.FAILED
        await s.commit()
    result = await persist_stage_success(maker, claimed, run_result, provider=None)
    assert result.outcome == AIStageOutcome.STALE_RESULT_REJECTED


async def test_cancelled_pipeline_rejects_late_result(maker):  # (25)/(26)
    env = await _seed_pipeline(maker)
    claimed, run_result = await _claim_execute(maker, provider=None)
    async with maker() as s:
        pipeline = (
            await s.execute(select(AIPipelineRun).where(AIPipelineRun.id == claimed.pipeline_id))
        ).scalar_one()
        pipeline.status = AIPipelineStatus.CANCELLED
        await s.commit()
    result = await persist_stage_success(maker, claimed, run_result, provider=None)
    assert result.outcome == AIStageOutcome.STALE_RESULT_REJECTED
    # (26) Late result successor OLUSTURMAZ.
    stage2 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BATCH_PREPARATION)
    assert stage2 == []


async def test_duplicate_persist_effective_once(maker):  # (27)
    env = await _seed_pipeline(maker)
    claimed, run_result = await _claim_execute(maker, provider=None)
    first = await persist_stage_success(maker, claimed, run_result, provider=None)
    second = await persist_stage_success(maker, claimed, run_result, provider=None)
    assert first.outcome == AIStageOutcome.SUCCEEDED
    assert second.outcome == AIStageOutcome.STALE_RESULT_REJECTED
    stage2 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BATCH_PREPARATION)
    assert len(stage2) == 1  # yalnizca bir kez etkili oldu


# =================================================================================
# FAILURE / CANCEL
# =================================================================================


async def test_provider_error_fails_stage_and_pipeline(maker):  # (28)/(30)
    env = await _seed_pipeline(maker, num_personas=1)
    provider = FaultyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION)
    results = await _drive(maker, provider)
    outcomes = [r.outcome for r in results]
    assert AIStageOutcome.FAILED in outcomes
    scenario = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert scenario[0].status == AIPipelineStageStatus.FAILED
    assert scenario[0].error_code == "provider_transport_error"
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED
    # (30) successor (Stage 4) OLUSMADI.
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert behavior == []


async def test_validation_error_sanitized(maker):  # (29)/(31)
    env = await _seed_pipeline(maker, num_personas=1)
    provider = BrokenScenarioProvider()
    await _drive(maker, provider)
    scenario = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION)
    stage = scenario[0]
    assert stage.status == AIPipelineStageStatus.FAILED
    assert stage.error_code == "invalid_evidence_reference"
    # (31) raw prompt/response/secret yok: yalnizca kisa kod; validated_output bos.
    assert stage.validated_output is None
    assert " " not in stage.error_code  # kisa kod, mesaj/traceback DEGIL
    assert "\n" not in stage.error_code


async def test_cancellation_before_provider(maker):  # (32)
    env = await _seed_pipeline(maker, num_personas=1, cancel_run=True)
    provider = CountingMockProvider()
    result = await process_one_ai_stage(maker, provider=provider)
    assert result.outcome == AIStageOutcome.CANCELLED
    assert provider.calls == []  # provider CAGRILMADI
    stage = await _get_stage(maker, result.stage_id)
    assert stage.status == AIPipelineStageStatus.CANCELLED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.CANCELLED
    # successor OLUSMADI.
    stage2 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BATCH_PREPARATION)
    assert stage2 == []


async def test_cancellation_does_not_modify_run_or_report(maker):  # (33)
    env = await _seed_pipeline(maker, num_personas=1, cancel_run=True)
    await process_one_ai_stage(maker, provider=MockAIProvider())
    run = await _get_run(maker, env.run_id)
    assert run.status == SimulationStatus.SUCCEEDED  # DEGISMEDI
    assert run.result is not None
    async with maker() as s:
        report_count = (
            await s.execute(
                select(func.count()).select_from(Report).where(Report.simulation_run_id == env.run_id)
            )
        ).scalar_one()
    assert report_count == 1  # Report DEGISMEDI/SILINMEDI


# =================================================================================
# END-TO-END CORE
# =================================================================================


async def test_end_to_end_single_persona_completes(maker):  # (34)
    env = await _seed_pipeline(maker, num_personas=1)
    provider = MockAIProvider()
    results = await _drive(maker, provider)
    assert results[-1].outcome == AIStageOutcome.NO_WORK
    succeeded = [r for r in results if r.outcome == AIStageOutcome.SUCCEEDED]
    stage_types = {r.stage_type for r in succeeded}
    assert stage_types == {
        AIPipelineStageType.EVIDENCE_PREPARATION,
        AIPipelineStageType.PERSONA_BATCH_PREPARATION,
        AIPipelineStageType.SCENARIO_INTERPRETATION,
        AIPipelineStageType.PERSONA_BEHAVIOR,
        AIPipelineStageType.AGGREGATION,
        AIPipelineStageType.UX_REPORT,
    }
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED
    ux = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.UX_REPORT)
    assert ux[0].validated_output is not None
    assert ux[0].validated_output["report_version"]


async def test_16_personas_two_behavior_stages(maker):  # (35)
    env = await _seed_pipeline(maker, num_personas=16)
    await _drive(maker, MockAIProvider())
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert len(behavior) == 2
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


async def test_100_personas_seven_behavior_and_nine_provider_calls(maker):  # (36)
    env = await _seed_pipeline(maker, num_personas=100)
    provider = CountingMockProvider()
    await _drive(maker, provider)
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert len(behavior) == 7
    # 1 scenario + 7 behavior + 1 ux_report = 9.
    assert len(provider.calls) == 9
    # Her batch TAM olarak bir kez islenir (claim sirasi created_at/id ile
    # deterministik olsa da batch_index sirasi olmak zorunda degildir).
    behavior_calls = [c for c in provider.calls if c[0] is AIPipelineStageType.PERSONA_BEHAVIOR]
    assert sorted(c[1] for c in behavior_calls) == list(range(7))
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


async def test_stage2_manifest_rehydration_after_full_run(maker):  # (37)
    env = await _seed_pipeline(maker, num_personas=16)
    await _drive(maker, MockAIProvider())
    stage2 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BATCH_PREPARATION)
    manifest = decode_persona_batch_manifest(stage2[0].validated_output)
    async with maker() as s:
        persona_rows = list(
            (await s.execute(select(Persona).where(Persona.simulation_run_id == env.run_id))).scalars().all()
        )
    rebuilt = rehydrate_persona_batches(manifest, persona_rows)  # mismatch olsa exception atardi
    assert len(rebuilt) == manifest.batch_count


async def test_worker_source_has_no_auto_mock_fallback(maker):  # (39)
    # `MockAIProvider(` artik (Faz 3D.2-LOCAL) `on_startup` icinde, `OpenAIProvider(`/
    # `OllamaProvider(` ile AYNI sekilde `ai_report_provider_ready` + acikca secilmis
    # `ai_report_provider=="mock"` KAPISININ ARKASINDA kosullu olarak gecer - gizli bir
    # otomatik fallback DEGIL, ACIKCA secilen bir secenektir; bu nedenle metin
    # taramasindan CIKARILDI.
    source = inspect.getsource(worker_module)
    for forbidden in ("httpx", "requests", "import socket", "environ"):
        assert forbidden not in source


async def test_process_one_has_no_org_parameter_and_claims_globally(maker):  # (40)
    # (a) process_one_ai_stage DISARIDAN org/id almaz (yalnizca sessionmaker + provider).
    params = list(inspect.signature(process_one_ai_stage).parameters)
    assert params == ["session_maker", "provider"]
    assert not any("organization" in p or "org" in p for p in params)

    # (b) Farkli iki organizasyona ait pipeline'lar, DISARIDAN org girdisi
    #     VERILMEDEN, yalnizca internal claim ile islenebilir.
    env_a = await _seed_pipeline(maker, num_personas=1)
    env_b = await _seed_pipeline(maker, num_personas=1)
    assert env_a.org_id != env_b.org_id
    provider = MockAIProvider()
    await _drive(maker, provider)
    pa = await _get_pipeline(maker, env_a.pipeline_id)
    pb = await _get_pipeline(maker, env_b.pipeline_id)
    assert pa.status == AIPipelineStatus.SUCCEEDED
    assert pb.status == AIPipelineStatus.SUCCEEDED
