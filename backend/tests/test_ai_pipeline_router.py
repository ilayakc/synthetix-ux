"""Faz 3C.1: salt-okunur AI pipeline durum/rapor endpoint'leri icin testler.

Iki endpoint kapsanir:
- GET /api/simulations/runs/{run_id}/ai-pipeline  (durum + ilerleme + stage ozetleri)
- GET /api/simulations/runs/{run_id}/ai-report     (tamamlanmis UX raporu)

Iki test stili kullanilir (bkz. tests/conftest.py tasarim notu):
- HTTP seviyesi (`client` fixture): tenant/auth izolasyonu, response sekli,
  guvenlik, OpenAPI, mutation-yok. Pipeline satirlari, HTTP istekleriyle AYNI
  test DB'sine yazan AYRI bir engine ile tohumlanir (bkz.
  tests/test_simulation_run_persona_count.py / _personas_endpoint.py deseni).
- Servis seviyesi (`session` fixture): ilerleme hesabi, kanonik siralama,
  beklenen stage sayisi, N+1 sorgu sayimi - kontrollu, izole transaction'da.

Bu dosya worker/orchestration/persistence testlerini TEKRARLAMAZ.
"""

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import (
    AIPipelineRun,
    AIPipelineStage,
    Organization,
    Persona,
    Project,
    SimulationRun,
    TestDefinition,
    TestVariant,
)
from app.models.ai_pipeline import (
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.simulations import SimulationStatus
from app.services import personas
from app.services.ai_pipeline import queries
from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.persistence import build_stage2_manifest, encode_stage_output
from app.services.ai_pipeline.schemas import (
    DEFAULT_PERSONA_BATCH_SIZE,
    UX_REPORT_DISCLAIMER,
    FindingPriority,
    PersonaContext,
    UXFinding,
    UXReport,
)
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


def _arun(func, *args, **kwargs):
    """`anyio.run` yalnizca positional arg kabul eder; kwargs'i partial ile sarar."""

    return anyio.run(functools.partial(func, *args, **kwargs))


# =============================================================================
# Saf (DB'siz) yardimcilar: gecerli manifest / UX raporu JSON'lari
# =============================================================================


def _manifest_json(num_personas: int) -> dict:
    """`num_personas` icin gecerli bir Stage 2 manifest JSON'i (in-memory)."""

    contexts = tuple(
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=index,
            label=f"Persona {index}",
            attributes={},
            population_weight=1,
        )
        for index in range(num_personas)
    )
    batches = build_persona_batches(contexts, batch_size=DEFAULT_PERSONA_BATCH_SIZE)
    manifest = build_stage2_manifest(batches)
    return encode_stage_output(AIPipelineStageType.PERSONA_BATCH_PREPARATION, manifest)


def _report_model(
    *, disclaimer: str = UX_REPORT_DISCLAIMER, finding_text: str = "Birincil CTA yeterince belirgin degil"
) -> UXReport:
    return UXReport(
        summary="Sentetik senaryo tahminlerine dayanan ozet.",
        findings=(
            UXFinding(
                finding_id="finding_1",
                priority=FindingPriority.HIGH,
                finding=finding_text,
                source_stage="aggregation",
                evidence_references=("metric:task_completion",),
                estimated_affected_users=10,
                recommendation="CTA'yi belirginlestir.",
                confidence=0.5,
            ),
        ),
        limitations="Kalibre edilmemis sentetik tahmin.",
        disclaimer=disclaimer,
    )


def _report_json(**kwargs: object) -> dict:
    return encode_stage_output(AIPipelineStageType.UX_REPORT, _report_model(**kwargs))


# =============================================================================
# Stage spec + pipeline tohumlama
# =============================================================================


@dataclass
class StageSpec:
    stage_type: AIPipelineStageType
    status: AIPipelineStageStatus
    batch_index: int | None = None
    validated_output: dict | None = None
    error_code: str | None = None
    attempt_count: int = 1


def _build_stage(pipeline_id: uuid.UUID, spec: StageSpec) -> AIPipelineStage:
    return AIPipelineStage(
        ai_pipeline_run_id=pipeline_id,
        stage_type=spec.stage_type,
        batch_index=spec.batch_index,
        status=spec.status,
        attempt_count=spec.attempt_count,
        input_hash="0" * 64,
        idempotency_key=uuid.uuid4().hex,
        validated_output=spec.validated_output,
        error_code=spec.error_code,
    )


_TERMINAL_PIPELINE = {
    AIPipelineStatus.SUCCEEDED,
    AIPipelineStatus.FAILED,
    AIPipelineStatus.CANCELLED,
}


async def _seed_pipeline_in_session(
    session: AsyncSession,
    run: SimulationRun,
    *,
    pipeline_status: AIPipelineStatus,
    stages: list[StageSpec],
    cancel_requested: bool = False,
) -> AIPipelineRun:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    pipeline = AIPipelineRun(
        organization_id=run.organization_id,
        simulation_run_id=run.id,
        status=pipeline_status,
        cancel_requested=cancel_requested,
        started_at=now if pipeline_status != AIPipelineStatus.QUEUED else None,
        finished_at=now if pipeline_status in _TERMINAL_PIPELINE else None,
    )
    session.add(pipeline)
    await session.flush()
    for spec in stages:
        session.add(_build_stage(pipeline.id, spec))
    await session.flush()
    return pipeline


async def _seed_pipeline_via_engine(
    run_id: str,
    *,
    pipeline_status: AIPipelineStatus,
    stages: list[StageSpec],
    cancel_requested: bool = False,
) -> str:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            pipeline = await _seed_pipeline_in_session(
                session,
                run,
                pipeline_status=pipeline_status,
                stages=stages,
                cancel_requested=cancel_requested,
            )
            await session.commit()
            return str(pipeline.id)
    finally:
        await engine.dispose()


async def _count_rows(model: type) -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return (await session.execute(select(func.count()).select_from(model))).scalar_one()
    finally:
        await engine.dispose()


async def _stage_status_snapshot(pipeline_id: str) -> dict[str, str]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            rows = (
                (
                    await session.execute(
                        select(AIPipelineStage).where(
                            AIPipelineStage.ai_pipeline_run_id == uuid.UUID(pipeline_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            return {str(row.id): row.status.value for row in rows}
    finally:
        await engine.dispose()


# =============================================================================
# Servis-seviyesi run factory (session fixture)
# =============================================================================


async def _make_run(
    session: AsyncSession,
    *,
    num_personas: int = 3,
    status: SimulationStatus = SimulationStatus.SUCCEEDED,
) -> tuple[Organization, SimulationRun]:
    org = Organization(name="AI Org", slug=f"ai-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    project = Project(organization_id=org.id, name="AI Project")
    session.add(project)
    await session.flush()
    definition = TestDefinition(organization_id=org.id, project_id=project.id, name="AI Test")
    session.add(definition)
    await session.flush()
    variant = TestVariant(
        organization_id=org.id, test_definition_id=definition.id, name="Variant A", config={}
    )
    session.add(variant)
    await session.flush()
    run = SimulationRun(
        organization_id=org.id,
        test_variant_id=variant.id,
        status=status,
        deterministic_seed=1,
        model_version="v0",
        input_snapshot={"persona_count": max(num_personas, 1)},
        result={"metrics": {}},
    )
    session.add(run)
    await session.flush()
    for index in range(num_personas):
        session.add(
            Persona(
                simulation_run_id=run.id,
                index=index,
                label=f"Persona {index}",
                attributes={},
                population_weight=1,
            )
        )
    await session.flush()
    return org, run


# =============================================================================
# HTTP yardimcilari (personas endpoint testiyle ayni desen)
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
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "AI pipeline endpoint testi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _basic_payload(project_id: str, *, persona_count: int = 1000) -> dict:
    return {
        "project_id": project_id,
        "name": f"AI pipeline testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": persona_count,
        "target_audience": "Genel kullanicilar",
        "persona_preset_id": personas.builtin_preset_id("general_web_users"),
        "authorization_confirmed": True,
        "modules": [],
    }


def _ab_payload(project_id: str) -> dict:
    payload = _basic_payload(project_id)
    payload["test_type"] = "ab_comparison"
    payload["new_url"] = "https://example.com/variant-b"
    return payload


def _launch_single(client: TestClient, *, persona_count: int = 1000) -> str:
    _register(client)
    project = _create_project(client)
    draft = client.post("/api/tests/drafts", headers=_csrf_headers(client)).json()
    payload = _basic_payload(project["id"], persona_count=persona_count)
    resp = client.patch(
        f"/api/tests/drafts/{draft['id']}", json={"payload": payload}, headers=_csrf_headers(client)
    )
    assert resp.status_code == 200, resp.text
    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    return launch.json()["simulation_run_ids"][0]


def _launch_ab(client: TestClient) -> tuple[str, str]:
    _register(client)
    project = _create_project(client)
    draft = client.post("/api/tests/drafts", headers=_csrf_headers(client)).json()
    payload = _ab_payload(project["id"])
    client.patch(f"/api/tests/drafts/{draft['id']}", json={"payload": payload}, headers=_csrf_headers(client))
    launch = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch.status_code == 200, launch.text
    ids = launch.json()["simulation_run_ids"]
    assert len(ids) == 2
    return ids[0], ids[1]


# Sik kullanilan stage kumeleri.
def _all_succeeded_stages(*, num_batches: int = 1, report: dict | None = None) -> list[StageSpec]:
    stages = [
        StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
        StageSpec(
            AIPipelineStageType.PERSONA_BATCH_PREPARATION,
            AIPipelineStageStatus.SUCCEEDED,
            # SUCCEEDED Stage 2 daima gecerli bir manifest tasir (aksi halde durum
            # endpoint'i hakli olarak butunluk hatasi verir). batch_count = num_batches.
            validated_output=_manifest_json(num_batches * DEFAULT_PERSONA_BATCH_SIZE),
        ),
        StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.SUCCEEDED),
    ]
    for batch_index in range(num_batches):
        stages.append(
            StageSpec(
                AIPipelineStageType.PERSONA_BEHAVIOR,
                AIPipelineStageStatus.SUCCEEDED,
                batch_index=batch_index,
            )
        )
    stages.append(StageSpec(AIPipelineStageType.AGGREGATION, AIPipelineStageStatus.SUCCEEDED))
    stages.append(
        StageSpec(
            AIPipelineStageType.UX_REPORT,
            AIPipelineStageStatus.SUCCEEDED,
            validated_output=report if report is not None else _report_json(),
        )
    )
    return stages


# =============================================================================
# 1-6  TENANT / AUTH
# =============================================================================


def test_same_org_can_read_pipeline_status(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.QUEUED,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.QUEUED)],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulation_run_id"] == run_id
    assert body["status"] == "queued"


@pytest.mark.security
def test_other_org_gets_404_for_pipeline(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.QUEUED,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.QUEUED)],
    )
    _register(client)  # org B
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 404


@pytest.mark.security
def test_other_org_cannot_distinguish_exists_vs_missing(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.RUNNING,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.RUNNING)],
    )
    _register(client)  # org B
    cross = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    missing = client.get(f"/api/simulations/runs/{uuid.uuid4()}/ai-pipeline")
    assert cross.status_code == missing.status_code == 404
    # Var olan (baska org'a ait) run ile hic olmayan run AYNI govdeyi dondurur.
    assert cross.json() == missing.json()


def test_unauthenticated_request_rejected(client: TestClient):
    response = client.get(f"/api/simulations/runs/{uuid.uuid4()}/ai-pipeline")
    assert response.status_code == 401


@pytest.mark.security
def test_wrong_org_attempt_does_not_mutate_db(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.RUNNING,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.RUNNING)],
    )
    before = _arun(_count_rows, AIPipelineStage)
    _register(client)  # org B
    assert client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").status_code == 404
    assert client.get(f"/api/simulations/runs/{run_id}/ai-report").status_code == 404
    after = _arun(_count_rows, AIPipelineStage)
    assert before == after


async def test_pipeline_and_report_share_same_tenant_helper(session: AsyncSession, monkeypatch):
    """Iki servis fonksiyonu da AYNI `_load_scoped_run` helper'ini cagirir."""

    _org, run = await _make_run(session)
    calls: list[uuid.UUID] = []
    real = queries._load_scoped_run

    async def _spy(sess, *, organization_id, simulation_run_id):
        calls.append(simulation_run_id)
        return await real(sess, organization_id=organization_id, simulation_run_id=simulation_run_id)

    monkeypatch.setattr(queries, "_load_scoped_run", _spy)

    with pytest.raises(queries.PipelineNotFoundError):
        await queries.get_ai_pipeline_status(
            session, organization_id=run.organization_id, simulation_run_id=run.id
        )
    with pytest.raises(queries.PipelineNotFoundError):
        await queries.get_ai_pipeline_report(
            session, organization_id=run.organization_id, simulation_run_id=run.id
        )
    assert calls == [run.id, run.id]


# =============================================================================
# 7-17  STATUS
# =============================================================================


def test_status_404_when_no_pipeline(client: TestClient):
    run_id = _launch_single(client)
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 404
    assert response.json()["detail"] == queries.ERROR_PIPELINE_NOT_FOUND


@pytest.mark.parametrize(
    "status,stage_status",
    [
        (AIPipelineStatus.QUEUED, AIPipelineStageStatus.QUEUED),
        (AIPipelineStatus.RUNNING, AIPipelineStageStatus.RUNNING),
    ],
)
def test_status_queued_and_running_summarized(client, status, stage_status):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=status,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, stage_status)],
    )
    body = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").json()
    assert body["status"] == status.value
    assert body["report_available"] is False
    assert len(body["stages"]) == 1
    assert body["stages"][0]["status"] == stage_status.value


def test_status_succeeded_report_available_true(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    body = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").json()
    assert body["status"] == "succeeded"
    assert body["report_available"] is True
    assert body["progress_percent"] == 100


@pytest.mark.parametrize(
    "status", [AIPipelineStatus.FAILED, AIPipelineStatus.PARTIAL, AIPipelineStatus.CANCELLED]
)
def test_status_non_success_report_available_false(client, status):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=status,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.PERSONA_BATCH_PREPARATION,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
            ),
        ],
    )
    body = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").json()
    assert body["status"] == status.value
    assert body["report_available"] is False


def test_status_succeeded_missing_ux_stage_is_integrity_error(client: TestClient):
    run_id = _launch_single(client)
    # Pipeline SUCCEEDED ama UX_REPORT stage yok -> status endpoint de
    # sessizce report_available=false GOSTERMEMELI, kontrollu 500 vermeli
    # (rapor endpoint'iyle ayni butunluk sozlesmesi).
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED)],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 500
    assert response.json()["detail"] == queries.ERROR_REPORT_INTEGRITY


def test_status_succeeded_ux_stage_not_succeeded_is_integrity_error(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.UX_REPORT,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
            ),
        ],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 500
    assert response.json()["detail"] == queries.ERROR_REPORT_INTEGRITY


def test_status_succeeded_null_validated_output_is_integrity_error(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=[
            StageSpec(
                AIPipelineStageType.UX_REPORT,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output=None,
            )
        ],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 500
    assert response.json()["detail"] == queries.ERROR_REPORT_INTEGRITY


@pytest.mark.security
def test_status_succeeded_corrupt_validated_output_rejected_without_leaking(client: TestClient):
    run_id = _launch_single(client)
    corrupt = {"report_version": "ux-report-v1", "secret_prompt": "SIZAN HAM PROMPT METNI"}
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=[
            StageSpec(
                AIPipelineStageType.UX_REPORT,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output=corrupt,
            )
        ],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 500
    assert response.json()["detail"] == queries.ERROR_REPORT_INTEGRITY
    assert "SIZAN" not in response.text
    assert "secret_prompt" not in response.text


def test_status_and_report_share_single_validated_final_report_helper(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
):
    """Durum ve rapor endpoint'lerinin AYNI paylasilan dogrulama yardimcisini
    cagirdigini kanitlar - mantik iki yerde KOPYALANMAZ. HTTP seviyesinde iki
    endpoint de ayni SUCCEEDED pipeline'a karsi cagrilir; spy'in iki kez (her
    endpoint icin bir kez) tetiklendigi dogrulanir."""

    calls: list[uuid.UUID] = []
    original = queries._get_validated_final_report

    def _spy(pipeline: AIPipelineRun) -> UXReport:
        calls.append(pipeline.id)
        return original(pipeline)

    monkeypatch.setattr(queries, "_get_validated_final_report", _spy)

    run_id = _launch_single(client)
    pipeline_id = _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )

    status_response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    report_response = client.get(f"/api/simulations/runs/{run_id}/ai-report")

    assert status_response.status_code == 200
    assert status_response.json()["report_available"] is True
    assert report_response.status_code == 200
    assert calls == [uuid.UUID(pipeline_id), uuid.UUID(pipeline_id)]


@pytest.mark.parametrize(
    "status",
    [AIPipelineStatus.QUEUED, AIPipelineStatus.RUNNING],
)
def test_status_non_succeeded_never_calls_final_report_helper(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, status: AIPipelineStatus
):
    calls: list[uuid.UUID] = []
    original = queries._get_validated_final_report

    def _spy(pipeline: AIPipelineRun) -> UXReport:
        calls.append(pipeline.id)
        return original(pipeline)

    monkeypatch.setattr(queries, "_get_validated_final_report", _spy)

    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=status,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.QUEUED)],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 200
    assert response.json()["report_available"] is False
    assert calls == []


@pytest.mark.parametrize(
    "status",
    [AIPipelineStatus.FAILED, AIPipelineStatus.PARTIAL, AIPipelineStatus.CANCELLED],
)
def test_status_failed_partial_cancelled_never_calls_final_report_helper(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, status: AIPipelineStatus
):
    calls: list[uuid.UUID] = []
    original = queries._get_validated_final_report

    def _spy(pipeline: AIPipelineRun) -> UXReport:
        calls.append(pipeline.id)
        return original(pipeline)

    monkeypatch.setattr(queries, "_get_validated_final_report", _spy)

    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=status,
        stages=[
            StageSpec(
                AIPipelineStageType.EVIDENCE_PREPARATION,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
            )
        ],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline")
    assert response.status_code == 200
    assert response.json()["report_available"] is False
    assert calls == []


async def test_stages_returned_in_canonical_order(session: AsyncSession):
    _org, run = await _make_run(session, num_personas=3)
    # Ters kanonik sirada ekle - siralama helper'inin duzelttigini kanitla.
    reversed_specs = [
        StageSpec(AIPipelineStageType.UX_REPORT, AIPipelineStageStatus.QUEUED),
        StageSpec(AIPipelineStageType.AGGREGATION, AIPipelineStageStatus.QUEUED),
        StageSpec(AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageStatus.QUEUED, batch_index=0),
        StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.SUCCEEDED),
        StageSpec(
            AIPipelineStageType.PERSONA_BATCH_PREPARATION,
            AIPipelineStageStatus.SUCCEEDED,
            validated_output=_manifest_json(3),
        ),
        StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
    ]
    await _seed_pipeline_in_session(
        session, run, pipeline_status=AIPipelineStatus.RUNNING, stages=reversed_specs
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    ordered_types = [s.stage_type for s in result.stages]
    assert ordered_types == [
        "evidence_preparation",
        "persona_batch_preparation",
        "scenario_interpretation",
        "persona_behavior",
        "aggregation",
        "ux_report",
    ]


async def test_persona_behavior_ordered_by_batch_index(session: AsyncSession):
    _org, run = await _make_run(session, num_personas=45)
    stage2 = _manifest_json(45)  # 3 batch
    specs = [
        StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
        StageSpec(
            AIPipelineStageType.PERSONA_BATCH_PREPARATION,
            AIPipelineStageStatus.SUCCEEDED,
            validated_output=stage2,
        ),
        StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.SUCCEEDED),
        # Batch'leri ters sirada ekle.
        StageSpec(AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageStatus.QUEUED, batch_index=2),
        StageSpec(AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageStatus.QUEUED, batch_index=0),
        StageSpec(AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageStatus.QUEUED, batch_index=1),
    ]
    await _seed_pipeline_in_session(session, run, pipeline_status=AIPipelineStatus.RUNNING, stages=specs)
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    behavior_batches = [s.batch_index for s in result.stages if s.stage_type == "persona_behavior"]
    assert behavior_batches == [0, 1, 2]


async def test_failed_init_pipeline_without_personas_returns_controlled_status(session: AsyncSession):
    """Regresyon (gorev talimati Part 1): kalici init hatasi sonrasi kaydedilen
    FAILED pipeline'in (Persona satiri YOK) DURUM sorgusu, 500 (integrity)
    YERINE kontrollu bir "failed" durumu + guvenli stage error_code dondurur -
    boylece frontend infinite `ai_pipeline_not_found` yerine gercek durumu gorur.
    (bkz. orchestration.record_initialization_failure)."""

    _org, run = await _make_run(session, num_personas=0)
    await _seed_pipeline_in_session(
        session,
        run,
        pipeline_status=AIPipelineStatus.FAILED,
        stages=[
            StageSpec(
                AIPipelineStageType.EVIDENCE_PREPARATION,
                AIPipelineStageStatus.FAILED,
                error_code="missing_report",
            )
        ],
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    assert result.status == "failed"
    assert result.report_available is False
    assert result.stages[0].error_code == "missing_report"


def test_status_response_omits_validated_output_and_hashes(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    raw = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").text.lower()
    for banned in (
        "validated_output",
        "input_hash",
        "output_hash",
        "prompt_hash",
        "idempotency",
        "provider",
        "model_name",
        "estimated_cost",
        "token",
    ):
        assert banned not in raw, f"yasakli alan sizdi: {banned}"


def test_status_returns_safe_error_code_not_raw_message(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.FAILED,
        stages=[
            StageSpec(
                AIPipelineStageType.EVIDENCE_PREPARATION,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
                attempt_count=3,
            )
        ],
    )
    body = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").json()
    stage = body["stages"][0]
    assert stage["error_code"] == "provider_timeout"
    assert stage["attempt_count"] == 3


async def test_status_no_n_plus_one_queries(session: AsyncSession):
    _org, run = await _make_run(session, num_personas=45)
    stages = _all_succeeded_stages(num_batches=3, report=_report_json())
    # Stage 2 gecerli manifest (3 batch) tasisin ki fallback persona sorgusu olmasin.
    stages[1] = StageSpec(
        AIPipelineStageType.PERSONA_BATCH_PREPARATION,
        AIPipelineStageStatus.SUCCEEDED,
        validated_output=_manifest_json(45),
    )
    await _seed_pipeline_in_session(session, run, pipeline_status=AIPipelineStatus.SUCCEEDED, stages=stages)

    counter: list[str] = []

    def _before(conn, cursor, statement, params, ctx, many):
        counter.append(statement)

    from sqlalchemy import event

    event.listen(Engine, "before_cursor_execute", _before)
    try:
        await queries.get_ai_pipeline_status(
            session, organization_id=run.organization_id, simulation_run_id=run.id
        )
    finally:
        event.remove(Engine, "before_cursor_execute", _before)

    # 9 stage olmasina ragmen sorgu sayisi sabit/kucuk kalmali (N+1 yok).
    assert len(counter) <= 6, f"cok fazla sorgu (N+1?): {len(counter)}"


# =============================================================================
# 18-29  PROGRESS
# =============================================================================


@pytest.mark.parametrize(
    "num_personas,expected_total",
    [(1, 6), (15, 6), (16, 7), (100, 12)],
)
async def test_expected_total_from_persona_count_fallback(session, num_personas, expected_total):
    _org, run = await _make_run(session, num_personas=num_personas)
    # Stage 2 SUCCEEDED DEGIL -> persona sayisi + batch-size sabiti fallback'i.
    await _seed_pipeline_in_session(
        session,
        run,
        pipeline_status=AIPipelineStatus.RUNNING,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED)],
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    assert result.expected_stage_count == expected_total


async def test_stage2_manifest_batch_count_is_authoritative(session: AsyncSession):
    # 100 persona satiri (fallback olsaydi 7 batch -> beklenen 12) ama manifest
    # yalnizca 16 persona (2 batch) tasir -> beklenen 7 olmali (manifest kazanir).
    _org, run = await _make_run(session, num_personas=100)
    await _seed_pipeline_in_session(
        session,
        run,
        pipeline_status=AIPipelineStatus.RUNNING,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.PERSONA_BATCH_PREPARATION,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output=_manifest_json(16),
            ),
        ],
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    assert result.expected_stage_count == 7


async def test_only_succeeded_stages_increase_progress(session: AsyncSession):
    _org, run = await _make_run(session, num_personas=15)  # 1 batch -> 6 stage beklenir
    await _seed_pipeline_in_session(
        session,
        run,
        pipeline_status=AIPipelineStatus.RUNNING,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.PERSONA_BATCH_PREPARATION,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output=_manifest_json(15),
            ),
            StageSpec(AIPipelineStageType.SCENARIO_INTERPRETATION, AIPipelineStageStatus.RUNNING),
            StageSpec(AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageStatus.QUEUED, batch_index=0),
        ],
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    # 2 succeeded / 6 expected -> round(200/6)=33.
    assert result.succeeded_stage_count == 2
    assert result.running_stage_count == 1
    assert result.queued_stage_count == 1
    assert result.progress_percent == 33


@pytest.mark.parametrize(
    "status", [AIPipelineStatus.FAILED, AIPipelineStatus.CANCELLED, AIPipelineStatus.PARTIAL]
)
async def test_terminal_failure_not_auto_100(session, status):
    _org, run = await _make_run(session, num_personas=15)
    await _seed_pipeline_in_session(
        session,
        run,
        pipeline_status=status,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.PERSONA_BATCH_PREPARATION,
                AIPipelineStageStatus.FAILED,
                error_code="output_validation_failed",
            ),
        ],
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    assert result.progress_percent < 100
    assert result.progress_percent == 17  # 1/6 -> round(100/6)=17


async def test_succeeded_pipeline_is_exactly_100(session: AsyncSession):
    _org, run = await _make_run(session, num_personas=15)
    await _seed_pipeline_in_session(
        session,
        run,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    result = await queries.get_ai_pipeline_status(
        session, organization_id=run.organization_id, simulation_run_id=run.id
    )
    assert result.progress_percent == 100


@pytest.mark.parametrize(
    "succeeded,expected,status,want",
    [
        (0, 6, AIPipelineStatus.QUEUED, 0),
        (3, 6, AIPipelineStatus.RUNNING, 50),
        (1, 6, AIPipelineStatus.RUNNING, 17),
        (5, 6, AIPipelineStatus.RUNNING, 83),
        (999, 6, AIPipelineStatus.RUNNING, 100),  # 0-100 disina cikamaz
        (0, 0, AIPipelineStatus.RUNNING, 0),  # sifira bolme yok
        (2, 6, AIPipelineStatus.FAILED, 33),  # terminal-fail otomatik 100 degil
        (6, 6, AIPipelineStatus.SUCCEEDED, 100),  # SUCCEEDED tam 100
    ],
)
def test_compute_progress_percent_deterministic(succeeded, expected, status, want):
    assert (
        queries.compute_progress_percent(
            succeeded_stage_count=succeeded, expected_total=expected, pipeline_status=status
        )
        == want
    )


def test_expected_stage_count_helper():
    assert queries.expected_stage_count(0) == 5
    assert queries.expected_stage_count(7) == 12


# =============================================================================
# 30-40  REPORT
# =============================================================================


def test_report_success_returns_structured_report(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulation_run_id"] == run_id
    assert body["report"]["report_version"] == "ux-report-v1"
    assert body["report"]["findings"][0]["finding_id"] == "finding_1"
    assert {"provider", "model_name", "instruction_version"} <= body.keys()


def test_report_always_includes_synthetic_disclaimer(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    body = client.get(f"/api/simulations/runs/{run_id}/ai-report").json()
    assert body["synthetic_disclaimer"] == queries.SYNTHETIC_ESTIMATE_DISCLAIMER


@pytest.mark.security
def test_report_disclaimer_is_server_controlled(client: TestClient):
    """UXReport.disclaimer manipule edilse bile ust duzey uyari SABIT kalir."""

    run_id = _launch_single(client)
    tampered = _report_json(disclaimer="MANIPULE EDILMIS SAHTE UYARI")
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(report=tampered),
    )
    body = client.get(f"/api/simulations/runs/{run_id}/ai-report").json()
    # LLM kaynakli alan degismis olabilir; ama sunucu-kontrollu uyari sabit.
    assert body["synthetic_disclaimer"] == queries.SYNTHETIC_ESTIMATE_DISCLAIMER
    assert body["report"]["disclaimer"] == "MANIPULE EDILMIS SAHTE UYARI"


def test_report_content_format_is_not_html(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    body = response.json()
    assert body["content_format"] == "structured_json"
    assert "html" not in body["content_format"].lower()
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("status", [AIPipelineStatus.QUEUED, AIPipelineStatus.RUNNING])
def test_report_not_ready_when_queued_or_running(client, status):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=status,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.RUNNING)],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.status_code == 409
    assert response.json()["detail"] == queries.ERROR_REPORT_NOT_READY


@pytest.mark.parametrize(
    "status", [AIPipelineStatus.FAILED, AIPipelineStatus.PARTIAL, AIPipelineStatus.CANCELLED]
)
def test_report_not_available_when_failed_partial_cancelled(client, status):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=status,
        stages=[
            StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED),
            StageSpec(
                AIPipelineStageType.SCENARIO_INTERPRETATION,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
            ),
        ],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.status_code == 409
    assert response.json()["detail"] == queries.ERROR_REPORT_NOT_AVAILABLE


def test_report_missing_ux_stage_is_integrity_error(client: TestClient):
    run_id = _launch_single(client)
    # Pipeline SUCCEEDED ama UX_REPORT stage yok -> kontrollu 500.
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=[StageSpec(AIPipelineStageType.EVIDENCE_PREPARATION, AIPipelineStageStatus.SUCCEEDED)],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.status_code == 500
    assert response.json()["detail"] == queries.ERROR_REPORT_INTEGRITY


@pytest.mark.security
def test_report_corrupt_output_rejected_without_leaking(client: TestClient):
    run_id = _launch_single(client)
    corrupt = {"report_version": "ux-report-v1", "secret_prompt": "SIZAN HAM PROMPT METNI"}
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=[
            StageSpec(
                AIPipelineStageType.UX_REPORT,
                AIPipelineStageStatus.SUCCEEDED,
                validated_output=corrupt,
            )
        ],
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.status_code == 500
    assert response.json()["detail"] == queries.ERROR_REPORT_INTEGRITY
    assert "SIZAN" not in response.text
    assert "secret_prompt" not in response.text


def test_report_excludes_intermediate_stage_outputs(client: TestClient):
    run_id = _launch_single(client)
    stages = _all_succeeded_stages()
    # Ara (evidence) stage'ine ayirt edici bir sentinel yerlestir; rapor
    # response'u YALNIZCA UX_REPORT ciktisini tasimali, ara ciktilari ASLA.
    stages[0] = StageSpec(
        AIPipelineStageType.EVIDENCE_PREPARATION,
        AIPipelineStageStatus.SUCCEEDED,
        validated_output={"leak": "INTERMEDIATE_STAGE_SECRET_MARKER"},
    )
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=stages,
    )
    raw = client.get(f"/api/simulations/runs/{run_id}/ai-report").text
    assert "INTERMEDIATE_STAGE_SECRET_MARKER" not in raw


@pytest.mark.security
def test_variant_a_report_returns_only_a(client: TestClient):
    run_a, run_b = _launch_ab(client)
    _arun(
        _seed_pipeline_via_engine,
        run_a,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(report=_report_json(finding_text="A varyanti bulgusu")),
    )
    # B icin pipeline yok -> B raporu 404, A raporu doner.
    body_a = client.get(f"/api/simulations/runs/{run_a}/ai-report").json()
    assert body_a["simulation_run_id"] == run_a
    assert body_a["report"]["findings"][0]["finding"] == "A varyanti bulgusu"
    assert client.get(f"/api/simulations/runs/{run_b}/ai-report").status_code == 404


def test_report_404_when_no_pipeline(client: TestClient):
    run_id = _launch_single(client)
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.status_code == 404
    assert response.json()["detail"] == queries.ERROR_PIPELINE_NOT_FOUND


# =============================================================================
# 41-50  REGRESSION / SECURITY
# =============================================================================


def test_baseline_run_response_unchanged(client: TestClient):
    run_id = _launch_single(client)
    body = client.get(f"/api/simulations/runs/{run_id}").json()
    # Mevcut sozlesme alanlari korunur (yeni endpoint bunlari degistirmez).
    for key in ("id", "organization_id", "status", "progress_percent", "not_real_user_data_label"):
        assert key in body
    assert "ai_pipeline" not in body


def test_ai_failure_does_not_affect_baseline_run(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.FAILED,
        stages=[
            StageSpec(
                AIPipelineStageType.EVIDENCE_PREPARATION,
                AIPipelineStageStatus.FAILED,
                error_code="provider_timeout",
            )
        ],
    )
    # Pipeline FAILED olsa da baseline run response'u kendi durumunu korur.
    run_body = client.get(f"/api/simulations/runs/{run_id}").json()
    assert run_body["status"] in {"queued", "running", "succeeded"}
    pipeline_body = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").json()
    assert pipeline_body["status"] == "failed"


def test_personas_endpoint_still_works(client: TestClient):
    run_id = _launch_single(client)
    response = client.get(f"/api/simulations/runs/{run_id}/personas")
    assert response.status_code == 200
    assert len(response.json()) > 0


@pytest.mark.security
def test_cross_tenant_report_isolation(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    snapshot = _snapshot_cookies(client)
    _register(client)  # org B
    assert client.get(f"/api/simulations/runs/{run_id}/ai-report").status_code == 404
    _restore_cookies(client, snapshot)
    assert client.get(f"/api/simulations/runs/{run_id}/ai-report").status_code == 200


@pytest.mark.security
def test_no_raw_prompt_or_secret_in_response(client: TestClient):
    run_id = _launch_single(client)
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    pipeline_text = client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").text.lower()
    report_text = client.get(f"/api/simulations/runs/{run_id}/ai-report").text.lower()
    for banned in ("prompt", "api_key", "authorization", "secret", "bearer"):
        assert banned not in pipeline_text
        assert banned not in report_text


@pytest.mark.security
def test_script_text_returned_as_escaped_json_not_html(client: TestClient):
    """UX bulgusundaki <script> metni JSON string olarak doner; asla HTML degil."""

    run_id = _launch_single(client)
    payload = _report_json(finding_text="<script>alert('xss')</script>")
    _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(report=payload),
    )
    response = client.get(f"/api/simulations/runs/{run_id}/ai-report")
    assert response.headers["content-type"].startswith("application/json")
    # JSON string olarak dogru sekilde geri gelir (executable HTML olarak DEGIL).
    assert response.json()["report"]["findings"][0]["finding"] == "<script>alert('xss')</script>"


def test_openapi_exposes_two_new_get_endpoints(client: TestClient):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "get" in paths["/api/simulations/runs/{run_id}/ai-pipeline"]
    assert "get" in paths["/api/simulations/runs/{run_id}/ai-report"]


def test_endpoints_perform_no_db_mutation(client: TestClient):
    run_id = _launch_single(client)
    pipeline_id = _arun(
        _seed_pipeline_via_engine,
        run_id,
        pipeline_status=AIPipelineStatus.SUCCEEDED,
        stages=_all_succeeded_stages(),
    )
    before_runs = _arun(_count_rows, AIPipelineRun)
    before_stages = _arun(_count_rows, AIPipelineStage)
    before_status = _arun(_stage_status_snapshot, pipeline_id)

    assert client.get(f"/api/simulations/runs/{run_id}/ai-pipeline").status_code == 200
    assert client.get(f"/api/simulations/runs/{run_id}/ai-report").status_code == 200

    assert _arun(_count_rows, AIPipelineRun) == before_runs
    assert _arun(_count_rows, AIPipelineStage) == before_stages
    assert _arun(_stage_status_snapshot, pipeline_id) == before_status
