"""Faz 3B.2A: `initialize_ai_pipeline` DB-entegrasyon testleri.

Fixture deseni `test_ai_pipeline_models.py` ile ayni (`session` fixture'i,
`pytest.mark.integration`). Her test kendi izole transaction'inda calisir ve
sonunda rollback edilir.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.services.ai_pipeline import orchestration
from app.services.ai_pipeline.orchestration import (
    AINotRequestedError,
    InvalidPersonaSetError,
    MissingReportError,
    PopulationTotalMismatchError,
    SimulationRunNotEligibleError,
    SimulationRunNotFoundError,
    _validate_persona_set,
    initialize_ai_pipeline,
)
from app.services.ai_pipeline.stage_hashing import deterministic_stage_idempotency_key
from app.services.ai_pipeline.stage_sources import (
    build_evidence_stage_source,
    evidence_stage_source_input_hash,
)

pytestmark = pytest.mark.integration


async def _make_eligible_run(
    session: AsyncSession,
    *,
    persona_count: int = 30,
    num_personas: int = 3,
    status: SimulationStatus = SimulationStatus.SUCCEEDED,
    cancel_requested: bool = False,
    with_report: bool = True,
    with_personas: bool = True,
    weights: list[int] | None = None,
) -> tuple[SimulationRun, uuid.UUID]:
    org = Organization(name="Orch Org", slug=f"orch-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()

    project = Project(organization_id=org.id, name="Orch Project")
    session.add(project)
    await session.flush()

    test_definition = TestDefinition(organization_id=org.id, project_id=project.id, name="Orch Test")
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
        status=status,
        cancel_requested=cancel_requested,
        deterministic_seed=42,
        model_version="v0",
        input_snapshot={
            "persona_count": persona_count,
            "source_type": "url",
            "modules": [],
        },
        result={
            "metrics": {"task_completion_probability": {"point_estimate": 0.8}},
            "page_feature_snapshot": {"nav_depth": 2, "primary_cta_count": 1},
        },
    )
    session.add(run)
    await session.flush()

    if with_report:
        session.add(
            Report(
                organization_id=org.id,
                simulation_run_id=run.id,
                title="Rapor",
                content={},
            )
        )
        await session.flush()

    if with_personas:
        if weights is None:
            base = persona_count // num_personas
            weights = [base] * num_personas
            weights[-1] += persona_count - sum(weights)
        for index, weight in enumerate(weights):
            session.add(
                Persona(
                    simulation_run_id=run.id,
                    index=index,
                    label=f"Persona {index}",
                    attributes={"age_range": "25_34"},
                    population_weight=weight,
                )
            )
        await session.flush()

    return run, org.id


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- Basari yolu -----------------------------------------------------------------


async def test_initialize_success_creates_one_run_and_one_queued_evidence_stage(session: AsyncSession):
    run, org_id = await _make_eligible_run(session)

    pipeline = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )

    assert pipeline.simulation_run_id == run.id
    assert pipeline.organization_id == org_id
    assert pipeline.status == AIPipelineStatus.QUEUED

    assert await _count(session, AIPipelineRun) == 1
    stages = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(stages) == 1
    stage = stages[0]
    assert stage.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION
    assert stage.status == AIPipelineStageStatus.QUEUED
    assert stage.batch_index is None
    assert stage.attempt_count == 0
    assert len(stage.input_hash) == 64
    assert len(stage.idempotency_key) == 64


async def test_initialization_does_not_execute_stage1_or_call_provider(session: AsyncSession):
    run, org_id = await _make_eligible_run(session)
    pipeline = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    stage = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline.id)
            )
        )
        .scalars()
        .one()
    )
    # Stage CALISTIRILMADI: cikti/hash/provider metadata bos.
    assert stage.validated_output is None
    assert stage.output_hash is None
    assert stage.provider is None
    assert stage.model_name is None
    assert stage.prompt_hash is None
    assert stage.error_code is None


async def test_queued_evidence_stage_idempotency_key_is_deterministic(session: AsyncSession):
    run, org_id = await _make_eligible_run(session)
    pipeline = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    stage = (
        (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline.id)
            )
        )
        .scalars()
        .one()
    )
    # Bagimsiz olarak PAYLASILAN yardimcilarla yeniden hesapla -> AYNI olmali
    # (worker ileride ayni degeri uretecektir).
    source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
    expected_input_hash = evidence_stage_source_input_hash(source)
    expected_key = deterministic_stage_idempotency_key(
        simulation_run_id=run.id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        input_hash=expected_input_hash,
    )
    assert stage.input_hash == expected_input_hash
    assert stage.idempotency_key == expected_key


# --- Idempotency -----------------------------------------------------------------


async def test_second_identical_call_returns_same_pipeline_no_duplicate(session: AsyncSession):
    run, org_id = await _make_eligible_run(session)
    first = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    second = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    assert first.id == second.id
    assert await _count(session, AIPipelineRun) == 1
    assert await _count(session, AIPipelineStage) == 1


async def test_integrity_error_race_returns_existing_row(session: AsyncSession, monkeypatch):
    """Es-zamanli yaris (iki cagri da "satir yok" gorur): ilk existence
    kontrolu None dondururken DB'de satir olusursa, insert UNIQUE ihlali verir;
    kod SAVEPOINT'i geri alip mevcut satiri yeniden sorgular ve dondurur.

    Gercek cok-baglantili es-zamanlilik test kosumunda saglanamadigi icin,
    ilk existence kontrolunu tek seferlik None dondurmeye zorlayarak Integrity
    error dalini deterministik olarak tetikliyoruz (bkz. rapor, bilinen riskler).
    """

    run, org_id = await _make_eligible_run(session)
    # Once gercek bir pipeline satiri olustur (baska bir "cagri" gibi).
    existing = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )

    real_loader = orchestration._load_existing_pipeline_run
    calls = {"n": 0}

    async def _flaky_loader(sess, sim_id):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # ilk kontrol: "satir yok" (yaris penceresi)
        return await real_loader(sess, sim_id)

    monkeypatch.setattr(orchestration, "_load_existing_pipeline_run", _flaky_loader)

    result = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    assert result.id == existing.id
    assert await _count(session, AIPipelineRun) == 1


# --- Cross-tenant / IDOR (guvenlik) ----------------------------------------------


def _assert_no_cross_tenant_leak(exc: SimulationRunNotFoundError, *, org_id, pipeline_id) -> None:
    """Hata mesaji/attribute'lari, run'in baska bir tenant altinda VAR oldugunu
    (org id, pipeline id veya "zaten var" imasi) ELE VERMEMELIDIR."""

    message = str(exc)
    assert str(org_id) not in message
    assert str(pipeline_id) not in message
    lowered = message.lower()
    for leak in ("zaten", "already", "exist", "mevcut", "pipeline"):
        assert leak not in lowered
    # `code` de sabit/generic olmali (varlik sizdirmamali).
    assert exc.code == orchestration.ERROR_SIMULATION_RUN_NOT_FOUND


async def test_wrong_org_with_existing_pipeline_denied_without_leak(session: AsyncSession):
    """Baska bir org'un simulation_run_id'si icin ZATEN bir pipeline varsa,
    yanlis org ile cagri, mevcut pipeline'i DONDURMEMELI; ayni generic
    `SimulationRunNotFoundError` firlatmalidir (cross-tenant varlik sizmadan)."""

    run, org_id = await _make_eligible_run(session)
    existing = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    other_org = uuid.uuid4()
    assert other_org != org_id

    with pytest.raises(SimulationRunNotFoundError) as excinfo:
        await initialize_ai_pipeline(
            session, organization_id=other_org, simulation_run_id=run.id, ai_requested=True
        )
    _assert_no_cross_tenant_leak(excinfo.value, org_id=org_id, pipeline_id=existing.id)


async def test_wrong_org_without_existing_pipeline_denied_same_shape(session: AsyncSession):
    """Yanlis org + hic pipeline YOK durumu, "yanlis org + pipeline baska yerde
    VAR" durumuyla AYNI hata tip/sekline sahip olmali - iki durum cagiran
    acisindan ayirt edilemez (IDOR karsiti cekirdek kanit)."""

    run, org_id = await _make_eligible_run(session)
    # Bu run icin HIC pipeline olusturmuyoruz.
    other_org = uuid.uuid4()

    with pytest.raises(SimulationRunNotFoundError) as excinfo:
        await initialize_ai_pipeline(
            session, organization_id=other_org, simulation_run_id=run.id, ai_requested=True
        )
    exc = excinfo.value
    assert exc.code == orchestration.ERROR_SIMULATION_RUN_NOT_FOUND
    # Mesaj, "pipeline var" durumundakiyle ayni generic ifade olmali.
    lowered = str(exc).lower()
    for leak in ("zaten", "already", "exist", "pipeline"):
        assert leak not in lowered


async def test_wrong_org_attempt_leaves_all_rows_untouched(session: AsyncSession):
    """Yanlis-org denemesinden SONRA AIPipelineRun/Stage sayilari DEGISMEMELI
    (kaza eseri olusturma yok, mevcut satira dokunulmuyor)."""

    run, org_id = await _make_eligible_run(session)
    await initialize_ai_pipeline(session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True)
    runs_before = await _count(session, AIPipelineRun)
    stages_before = await _count(session, AIPipelineStage)

    with pytest.raises(SimulationRunNotFoundError):
        await initialize_ai_pipeline(
            session, organization_id=uuid.uuid4(), simulation_run_id=run.id, ai_requested=True
        )

    assert await _count(session, AIPipelineRun) == runs_before
    assert await _count(session, AIPipelineStage) == stages_before


async def test_same_org_existing_pipeline_still_returned(session: AsyncSession):
    """Regresyon: AYNI org + mevcut pipeline hala ayni pipeline'i dondurur
    (tenant-scoped yeniden siralama, idempotent get-or-return'i bozmamali)."""

    run, org_id = await _make_eligible_run(session)
    first = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    second = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    assert first.id == second.id
    assert await _count(session, AIPipelineRun) == 1
    assert await _count(session, AIPipelineStage) == 1


# --- On kosul ihlalleri ----------------------------------------------------------


async def test_ai_requested_false_rejected(session: AsyncSession):
    run, org_id = await _make_eligible_run(session)
    with pytest.raises(AINotRequestedError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=False
        )
    assert await _count(session, AIPipelineRun) == 0


async def test_unknown_run_or_wrong_org_rejected(session: AsyncSession):
    run, org_id = await _make_eligible_run(session)
    with pytest.raises(SimulationRunNotFoundError):
        await initialize_ai_pipeline(
            session, organization_id=uuid.uuid4(), simulation_run_id=run.id, ai_requested=True
        )
    with pytest.raises(SimulationRunNotFoundError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=uuid.uuid4(), ai_requested=True
        )


@pytest.mark.parametrize(
    "status", [SimulationStatus.FAILED, SimulationStatus.RUNNING, SimulationStatus.QUEUED]
)
async def test_non_succeeded_run_rejected(session: AsyncSession, status):
    run, org_id = await _make_eligible_run(session, status=status)
    with pytest.raises(SimulationRunNotEligibleError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )
    assert await _count(session, AIPipelineRun) == 0


async def test_cancelled_run_rejected(session: AsyncSession):
    run, org_id = await _make_eligible_run(session, cancel_requested=True)
    with pytest.raises(SimulationRunNotEligibleError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )


async def test_missing_report_rejected(session: AsyncSession):
    run, org_id = await _make_eligible_run(session, with_report=False)
    with pytest.raises(MissingReportError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )
    assert await _count(session, AIPipelineRun) == 0


async def test_missing_personas_rejected(session: AsyncSession):
    run, org_id = await _make_eligible_run(session, with_personas=False)
    with pytest.raises(InvalidPersonaSetError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )


async def test_population_total_mismatch_rejected(session: AsyncSession):
    # persona_count 30 ama agirlik toplami 25.
    run, org_id = await _make_eligible_run(session, persona_count=30, weights=[10, 10, 5])
    with pytest.raises(PopulationTotalMismatchError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )
    assert await _count(session, AIPipelineRun) == 0


async def test_more_than_100_personas_rejected(session: AsyncSession):
    weights = [1] * 101
    run, org_id = await _make_eligible_run(session, persona_count=101, num_personas=101, weights=weights)
    with pytest.raises(InvalidPersonaSetError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )
    assert await _count(session, AIPipelineRun) == 0


async def test_validation_failure_leaves_no_partial_rows(session: AsyncSession):
    run, org_id = await _make_eligible_run(session, persona_count=30, weights=[10, 10, 5])
    with pytest.raises(PopulationTotalMismatchError):
        await initialize_ai_pipeline(
            session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
        )
    assert await _count(session, AIPipelineRun) == 0
    assert await _count(session, AIPipelineStage) == 0


async def test_zero_or_negative_population_weight_rejected_by_validator():
    """DB CheckConstraint pozitif olmayan agirligi zaten engeller; validator'in
    savunma katmanini in-memory olarak dogrudan test ederiz."""

    personas = [
        Persona(simulation_run_id=uuid.uuid4(), index=0, label="p0", attributes={}, population_weight=10),
        Persona(simulation_run_id=uuid.uuid4(), index=1, label="p1", attributes={}, population_weight=0),
    ]
    with pytest.raises(InvalidPersonaSetError):
        _validate_persona_set(personas, expected_total=10)


# --- Transaction siniri ----------------------------------------------------------


async def test_initialize_does_not_commit_caller_owns_transaction(session: AsyncSession, monkeypatch):
    """Servis `commit` ETMEZ (cagiran taraf commit eder) ama `flush` yaptigi
    icin satirlar ayni session icinde sorgulanabilir."""

    run, org_id = await _make_eligible_run(session)
    commit_spy = AsyncMock(wraps=session.commit)
    monkeypatch.setattr(session, "commit", commit_spy)

    pipeline = await initialize_ai_pipeline(
        session, organization_id=org_id, simulation_run_id=run.id, ai_requested=True
    )
    commit_spy.assert_not_awaited()
    # flush edildigi icin ayni session icinde gorunur:
    found = (
        await session.execute(select(AIPipelineRun).where(AIPipelineRun.id == pipeline.id))
    ).scalar_one_or_none()
    assert found is not None


# --- Regresyon: paylasilan hash yardimcilari birebir ayni --------------------------


async def test_orchestration_input_hash_matches_stage_runner_helper(session: AsyncSession):
    """orchestration ve stage_runner AYNI evidence input_hash yardimcisini
    kullanir - stage_sources'tan hesaplanan hash, stage_hashing'in evidence
    yardimcisiyla birebir ayni olmalidir."""

    from app.services.ai_pipeline.stage_hashing import evidence_stage_input_hash

    run, org_id = await _make_eligible_run(session)
    source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
    via_source = evidence_stage_source_input_hash(source)
    via_direct = evidence_stage_input_hash(
        source_type=source.source_type,
        metrics=source.metrics,
        page_features=source.page_features,
        selected_modules=source.selected_modules,
        module_results=source.module_results,
    )
    assert via_source == via_direct
