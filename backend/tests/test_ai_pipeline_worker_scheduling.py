"""Faz 3B.2D: AI pipeline worker cekirdegini arq zamanlama katmanina baglayan
ince wrapper/cron/provider-context/gozlemlenebilirlik testleri.

Bu dosya, `app.services.ai_pipeline.scheduling` (cycle wrapper'lari) ve
`app.worker` (arq task/cron kaydi) davranisini dogrular. Cekirdek claim/execute/
persist/DAG/retry/stale kapsamasi `test_ai_pipeline_worker.py` ve
`test_ai_pipeline_retry_stale.py`de KALIR; buradaki testler o dosyalardaki
seed/erisim yardimcilarini YENIDEN KULLANIR (kopyalamaz).

Fixture deseni (bkz. test_ai_pipeline_worker.py): rollback eden `session`
fixture'i DEGIL; test engine uzerine kurulu GERCEK commit eden `async_
sessionmaker` + test sonunda TRUNCATE. Provider HER ZAMAN ctx'e ACIKCA enjekte
edilir - hicbir test otomatik Mock olusturmasina guvenmez.
"""

from __future__ import annotations

import inspect
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import worker as worker_module
from app.models.ai_pipeline import (
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.simulations import SimulationStatus
from app.services.ai_pipeline import scheduling as sched
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.provider_errors import AIProviderTransportError
from app.services.ai_pipeline.scheduling import (
    AI_PIPELINE_PROVIDER_CTX_KEY,
    PROCESSOR_TASK_NAME,
    REAPER_TASK_NAME,
    InvalidCtxProviderError,
    resolve_ctx_provider,
    run_ai_pipeline_reap_cycle,
    run_ai_pipeline_stage_cycle,
)
from app.services.ai_pipeline.worker import (
    STALE_RUNNING_TIMEOUT_SECONDS,
    AIStageOutcome,
    AIStageProcessResult,
    StaleRecoveryResult,
    claim_next_ai_stage,
)
from app.worker import (
    WorkerSettings,
    process_ai_pipeline_stage_job,
    reap_stale_ai_pipeline_stages_job,
)
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables

# Cekirdek worker testinin seed/erisim yardimcilarini yeniden kullan.
from tests.test_ai_pipeline_worker import (
    BrokenScenarioProvider,
    CountingMockProvider,
    _get_pipeline,
    _get_run,
    _get_stage,
    _seed_pipeline,
    _stages,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def maker(test_engine):
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


# --- Test provider'lari ----------------------------------------------------------


class FlakyProvider:
    """Belirtilen provider stage'ini ilk `fail_times` cagrida `AIProviderTransport
    Error` (retryable) ile reddeder, sonra `MockAIProvider`e devrederek BASARIR."""

    def __init__(self, *, fail_stage: AIPipelineStageType, fail_times: int = 1) -> None:
        self._inner = MockAIProvider()
        self.provider_name = self._inner.provider_name
        self.model_name = self._inner.model_name
        self.is_mock = self._inner.is_mock
        self.configuration_fingerprint = self._inner.configuration_fingerprint
        self._fail_stage = fail_stage
        self._fail_times = fail_times
        self._seen = 0

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        if stage_type is self._fail_stage and self._seen < self._fail_times:
            self._seen += 1
            raise AIProviderTransportError("test tasima hatasi")
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


# --- ctx / surus yardimcilari ----------------------------------------------------


def _ctx(provider: object = "__absent__") -> dict[str, object]:
    """Provider verilmezse anahtar HIC eklenmez (== disabled); verilirse ACIKCA enjekte edilir."""

    if provider == "__absent__":
        return {}
    return {AI_PIPELINE_PROVIDER_CTX_KEY: provider}


async def _processor(maker, provider: object = "__absent__") -> dict:
    return await run_ai_pipeline_stage_cycle(_ctx(provider), session_maker=maker)


async def _reaper(maker) -> dict:
    return await run_ai_pipeline_reap_cycle(_ctx(), session_maker=maker)


async def _drive_processor(maker, provider, *, max_steps: int = 80) -> list[dict]:
    results: list[dict] = []
    for _ in range(max_steps):
        r = await _processor(maker, provider)
        results.append(r)
        if r.get("outcome") == AIStageOutcome.NO_WORK.value:
            break
    return results


async def _make_stage_stale(maker, stage_id) -> None:
    """RUNNING bir stage'in `updated_at`ini ham SQL ile geriye alir (ORM
    `onupdate=func.now()` TETIKLENMEZ), boylece cekirdek reaper onu (varsayilan
    600s esigiyle) stale gorur - testte 600s beklemeye gerek kalmaz."""

    old = datetime.now(UTC) - timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS + 100)
    async with maker() as s:
        await s.execute(
            text("UPDATE ai_pipeline_stages SET updated_at = :ts WHERE id = :sid"),
            {"ts": old, "sid": stage_id},
        )
        await s.commit()


# =================================================================================
# REGISTRATION
# =================================================================================


def test_processor_task_registered_exactly_once():
    matches = [f for f in WorkerSettings.functions if f is process_ai_pipeline_stage_job]
    assert len(matches) == 1


def test_reaper_task_registered_exactly_once():
    matches = [f for f in WorkerSettings.functions if f is reap_stale_ai_pipeline_stages_job]
    assert len(matches) == 1


def test_processor_cron_registered_exactly_once():
    matches = [c for c in WorkerSettings.cron_jobs if c.coroutine is process_ai_pipeline_stage_job]
    assert len(matches) == 1


def test_reaper_cron_registered_exactly_once():
    matches = [c for c in WorkerSettings.cron_jobs if c.coroutine is reap_stale_ai_pipeline_stages_job]
    assert len(matches) == 1


def test_baseline_worker_registrations_preserved():
    # Mevcut baseline simulation/page/design task ve cron kayitlari DEGISMEDEN durur.
    baseline_funcs = {
        worker_module.ping_redis,
        worker_module.process_queued_simulations,
        worker_module.reap_stale_simulations,
        worker_module.fail_simulations_blocked_by_page_analysis,
        worker_module.process_queued_page_analyses,
        worker_module.reap_stale_page_analyses,
        worker_module.purge_expired_page_analysis_screenshots,
        worker_module.purge_expired_design_assets,
        worker_module.process_queued_design_generations,
        worker_module.reap_stale_design_generations,
        worker_module.purge_expired_design_generations,
    }
    assert baseline_funcs.issubset(set(WorkerSettings.functions))
    baseline_cron_coros = {c.coroutine for c in WorkerSettings.cron_jobs}
    assert baseline_funcs.issubset(baseline_cron_coros)


def test_job_names_stable_and_unique():
    # Fonksiyon adlari (arq is adi kaynagi) benzersiz + AI job adlari kararli.
    names = [f.__name__ for f in WorkerSettings.functions]
    assert len(names) == len(set(names))
    assert "process_ai_pipeline_stage_job" in names
    assert "reap_stale_ai_pipeline_stages_job" in names
    # Cron is adlari benzersiz (mevcut baseline dahil hicbir cakisma yok).
    cron_names = [c.name for c in WorkerSettings.cron_jobs]
    assert len(cron_names) == len(set(cron_names))


def test_importing_worker_does_no_provider_or_network_call():
    # Modul import edildiginde otomatik provider/network YOK - kaynak taramasi.
    # `MockAIProvider(` artik (Faz 3D.2-LOCAL) `on_startup` icinde, `OpenAIProvider(`/
    # `OllamaProvider(` ile AYNI sekilde `ai_report_provider_ready` + acikca secilmis
    # `ai_report_provider=="mock"` KAPISININ ARKASINDA kosullu olarak gecer - bu artik
    # gizli bir otomatik fallback DEGIL, digerleriyle esdeger, ACIKCA secilen bir
    # secenektir; bu nedenle metin taramasindan CIKARILDI (bkz. app.worker.on_startup).
    for module in (sched, worker_module):
        source = inspect.getsource(module)
        for forbidden in ("httpx", "requests", "import socket", "os.environ", "os.getenv"):
            assert forbidden not in source
    # Import edilmis WorkerSettings.on_startup GERCEK provider olusturmaz (ctx'e
    # yalnizca None sozlesme anahtari koyar) - dogrudan kanit: ctx bos ise resolve None.
    assert resolve_ctx_provider({}) is None


# =================================================================================
# DISABLED / PROVIDER CONTEXT
# =================================================================================


async def test_no_provider_claims_nothing_and_stage_stays_queued(maker):
    env = await _seed_pipeline(maker)
    result = await _processor(maker)  # ctx'de provider YOK
    assert result["status"] == "disabled"
    assert result["claimed"] is False
    stages = await _stages(maker, env.pipeline_id)
    assert len(stages) == 1
    assert stages[0].status == AIPipelineStageStatus.QUEUED
    assert stages[0].attempt_count == 0  # CLAIM edilmedi -> attempt artmadi


async def test_no_provider_pipeline_not_failed(maker):
    env = await _seed_pipeline(maker)
    await _processor(maker)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.QUEUED


async def test_no_provider_baseline_simulation_run_unchanged(maker):
    env = await _seed_pipeline(maker)
    before = await _get_run(maker, env.run_id)
    await _processor(maker)
    after = await _get_run(maker, env.run_id)
    assert after.status == before.status == SimulationStatus.SUCCEEDED


async def test_disabled_result_is_safe_and_small(maker):
    result = await _processor(maker)  # bos DB, provider yok
    assert result == {
        "task": PROCESSOR_TASK_NAME,
        "status": "disabled",
        "reason": "no_provider",
        "claimed": False,
    }


async def test_wrong_provider_type_rejected_without_claim(maker):
    env = await _seed_pipeline(maker)
    result = await _processor(maker, provider="not-a-provider")  # yanlis tip
    assert result["status"] == "disabled"
    assert result["reason"] == "invalid_provider_context"
    assert result["claimed"] is False
    stages = await _stages(maker, env.pipeline_id)
    assert stages[0].status == AIPipelineStageStatus.QUEUED  # CLAIM EDILMEDI


async def test_wrong_provider_type_detected_before_any_db_touch():
    # resolve_ctx_provider yanlis tipte InvalidCtxProviderError firlatir (stage claim yok).
    with pytest.raises(InvalidCtxProviderError):
        resolve_ctx_provider({AI_PIPELINE_PROVIDER_CTX_KEY: object()})


async def test_no_auto_mock_and_missing_config_stays_disabled(maker):
    # ctx tamamen bos (config eksikligi) -> Mock fallback URETILMEZ, None doner.
    assert resolve_ctx_provider({}) is None
    assert resolve_ctx_provider({AI_PIPELINE_PROVIDER_CTX_KEY: None}) is None


# =================================================================================
# PROCESSOR WRAPPER
# =================================================================================


async def test_process_one_called_exactly_once(monkeypatch, maker):
    calls = {"n": 0}

    async def fake_process_one(session_maker, *, provider=None):
        calls["n"] += 1
        return AIStageProcessResult(outcome=AIStageOutcome.NO_WORK)

    monkeypatch.setattr(sched, "process_one_ai_stage", fake_process_one)
    await _processor(maker, MockAIProvider())
    assert calls["n"] == 1  # tek invocation'da tam bir kez


async def test_wrapper_advances_at_most_one_stage(maker):
    env = await _seed_pipeline(maker)
    r = await _processor(maker, MockAIProvider())
    assert r["status"] == "processed"
    assert r["outcome"] == AIStageOutcome.SUCCEEDED.value
    # Yalnizca Stage 1 ilerledi; successor (Stage 2) QUEUED, digerleri yok.
    succeeded = await _stages(maker, env.pipeline_id, status=AIPipelineStageStatus.SUCCEEDED)
    assert len(succeeded) == 1
    assert succeeded[0].stage_type == AIPipelineStageType.EVIDENCE_PREPARATION
    queued = await _stages(maker, env.pipeline_id, status=AIPipelineStageStatus.QUEUED)
    assert {s.stage_type for s in queued} == {AIPipelineStageType.PERSONA_BATCH_PREPARATION}


async def test_no_work_returns_safe_result(maker):
    r = await _processor(maker, MockAIProvider())  # bos DB ama provider VAR
    assert r["status"] == "processed"
    assert r["outcome"] == AIStageOutcome.NO_WORK.value
    assert r["pipeline_id"] is None
    assert r["stage_id"] is None


async def test_succeeded_summary_fields(maker):
    env = await _seed_pipeline(maker)
    r = await _processor(maker, MockAIProvider())
    assert r["outcome"] == AIStageOutcome.SUCCEEDED.value
    assert r["stage_type"] == AIPipelineStageType.EVIDENCE_PREPARATION.value
    assert r["pipeline_id"] == str(env.pipeline_id)
    assert r["error_code"] is None


async def test_retry_scheduled_summary(maker):
    await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    outcomes = [r["outcome"] for r in await _drive_processor(maker, provider)]
    assert AIStageOutcome.RETRY_SCHEDULED.value in outcomes


async def test_failed_summary(maker):
    env = await _seed_pipeline(maker)
    # Sema-gecerli ama evidence-referansi gecersiz senaryo -> non-retryable FAILED.
    outcomes = [r["outcome"] for r in await _drive_processor(maker, BrokenScenarioProvider())]
    assert AIStageOutcome.FAILED.value in outcomes
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED


async def test_wrapper_passes_provider_and_session_maker(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    provider = CountingMockProvider()
    await _drive_processor(maker, provider)
    # Provider ilettiği icin gercek provider cagrilari gerceklesti (scenario+behavior+ux).
    assert len(provider.calls) == 3
    # Dogru session_maker ilettigi icin pipeline (test DB'sinde) tamamlandi.
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


def test_wrapper_does_not_reimplement_core_logic():
    # Wrapper cekirdek claim/DAG/CAS/pin mantigini KOPYALAMAZ (kaynak taramasi).
    source = inspect.getsource(sched)
    for forbidden in (
        "claim_next_ai_stage",
        "enqueue_ready_successors",
        "with_for_update",
        "_pin_provider_execution",
        "SKIP LOCKED",
    ):
        assert forbidden not in source


# =================================================================================
# REAPER WRAPPER
# =================================================================================


async def test_reaper_calls_core_exactly_once(monkeypatch, maker):
    calls = {"n": 0}

    async def fake_reap(session_maker):
        calls["n"] += 1
        return StaleRecoveryResult()

    monkeypatch.setattr(sched, "reap_stale_ai_stages", fake_reap)
    await _reaper(maker)
    assert calls["n"] == 1


async def test_reaper_runs_without_provider(maker):
    # Provider ctx'de olmasa bile reaper calisir ve guvenli ozet doner.
    r = await _reaper(maker)
    assert r["task"] == REAPER_TASK_NAME
    assert r == {"task": REAPER_TASK_NAME, "scanned": 0, "requeued": 0, "failed": 0, "cancelled": 0}


def test_reaper_does_not_redefine_retry_constants():
    source = inspect.getsource(sched)
    # Sabitler yalnizca retry_policy/cekirdekte tanimlidir - burada YENIDEN tanimlanmaz.
    assert "MAX_STAGE_ATTEMPTS" not in source
    assert "STALE_RUNNING_TIMEOUT_SECONDS" not in source
    assert "timedelta" not in source  # cutoff/stale hesabi kopyalanmaz


async def test_reaper_returns_counts_for_requeued_stage(maker):
    env = await _seed_pipeline(maker)
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    assert claimed is not None
    await _make_stage_stale(maker, claimed.stage_id)
    r = await _reaper(maker)
    assert r["scanned"] == 1
    assert r["requeued"] == 1
    assert r["failed"] == 0
    fresh = await _get_stage(maker, claimed.stage_id)
    assert fresh.status == AIPipelineStageStatus.QUEUED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.RUNNING


async def test_reaper_does_not_swallow_unexpected_exception(monkeypatch, maker):
    async def boom(session_maker):
        raise RuntimeError("infra failure")

    monkeypatch.setattr(sched, "reap_stale_ai_stages", boom)
    with pytest.raises(RuntimeError):
        await _reaper(maker)


# =================================================================================
# TRANSACTION / CONCURRENCY REGRESSION (wrapper seviyesinde)
# =================================================================================


async def test_processor_cycle_creates_no_duplicate_successors(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    await _drive_processor(maker, MockAIProvider())
    all_stages = await _stages(maker, env.pipeline_id)
    # 1 persona -> her stage tipinden TAM bir satir (behavior dahil), duplicate yok.
    by_type = [s.stage_type for s in all_stages]
    assert len(by_type) == len(set(by_type)) == 6


async def test_processor_cycle_does_not_double_count_tokens(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    await _drive_processor(maker, MockAIProvider())
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    stages = await _stages(maker, env.pipeline_id, status=AIPipelineStageStatus.SUCCEEDED)
    # Pipeline toplamlari, stage toplamlarinin TAM olarak bir katidir (cift sayim yok).
    assert pipeline.total_input_tokens == sum(s.input_tokens for s in stages)
    assert pipeline.total_output_tokens == sum(s.output_tokens for s in stages)


async def test_stale_reaper_and_processor_preserve_cas(maker):
    # Stale reap sonrasi (attempt korunur) processor kaldigi yerden ilerler ve
    # eski/stale sonuc CAS ile reddedilmis olur - pipeline tutarli tamamlanir.
    env = await _seed_pipeline(maker, num_personas=1)
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    assert claimed is not None
    await _make_stage_stale(maker, claimed.stage_id)
    await _reaper(maker)  # Stage 1 RUNNING->QUEUED
    await _drive_processor(maker, MockAIProvider())
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED
    stage1 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.EVIDENCE_PREPARATION)
    assert len(stage1) == 1  # duplicate stage olusmadi


# =================================================================================
# LOGGING / SECURITY
# =================================================================================


async def test_logs_contain_no_sensitive_payload(maker, caplog):
    caplog.set_level(logging.DEBUG, logger="synthetix.ai_pipeline_worker")
    await _seed_pipeline(maker, num_personas=1)
    await _drive_processor(maker, MockAIProvider())
    await _reaper(maker)
    blob = "\n".join(
        rec.getMessage() + " " + " ".join(f"{k}={v}" for k, v in rec.__dict__.items())
        for rec in caplog.records
    )
    # validated_output/prompt/persona icerigine ait ayirt edici metinler + sirlar YOK.
    for forbidden in ("sentetik", "prompt", "api_key", "authorization", "Persona ", "attributes"):
        assert forbidden not in blob


async def test_expected_outcomes_do_not_log_traceback(maker, caplog):
    caplog.set_level(logging.DEBUG, logger="synthetix.ai_pipeline_worker")
    await _seed_pipeline(maker, num_personas=1)
    await _processor(maker)  # disabled
    await _drive_processor(maker, MockAIProvider())  # succeeded... + no_work
    assert all(rec.exc_info is None for rec in caplog.records)


async def test_unexpected_exception_logged_and_reraised(monkeypatch, maker, caplog):
    caplog.set_level(logging.DEBUG, logger="synthetix.ai_pipeline_worker")

    async def boom(session_maker, *, provider=None):
        raise RuntimeError("infra boom")

    monkeypatch.setattr(sched, "process_one_ai_stage", boom)
    with pytest.raises(RuntimeError):
        await _processor(maker, MockAIProvider())
    err_records = [r for r in caplog.records if r.exc_info is not None]
    assert err_records  # guvenli loglandi (exc_info ile) ve YENIDEN firlatildi


async def test_info_log_only_carries_safe_metadata(maker, caplog):
    caplog.set_level(logging.DEBUG, logger="synthetix.ai_pipeline_worker")
    await _seed_pipeline(maker, num_personas=1)
    await _processor(maker, MockAIProvider())
    info_recs = [r for r in caplog.records if r.getMessage() == "ai pipeline stage islendi"]
    assert info_recs
    allowed = {
        "task",
        "outcome",
        "pipeline_id",
        "stage_id",
        "stage_type",
        "batch_index",
        "pipeline_completed",
        "duration_ms",
        "category",
    }
    extras = {k for k in info_recs[0].__dict__ if k not in _STD_LOGRECORD_ATTRS}
    assert extras.issubset(allowed)


_STD_LOGRECORD_ATTRS = set(logging.LogRecord("x", 0, "x", 0, "x", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


# =================================================================================
# END-TO-END SCHEDULING
# =================================================================================


async def test_processor_job_completes_pipeline_stage1_to_6(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    results = await _drive_processor(maker, MockAIProvider())
    assert results[-1]["outcome"] == AIStageOutcome.NO_WORK.value
    succeeded_types = {r["stage_type"] for r in results if r["outcome"] == AIStageOutcome.SUCCEEDED.value}
    assert succeeded_types == {t.value for t in AIPipelineStageType}
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED
    # Pipeline'i tamamlayan son adim `pipeline_completed=True` bildirir.
    completed = [r for r in results if r.get("pipeline_completed")]
    assert len(completed) == 1


async def test_pipeline_waits_at_queued_when_provider_removed_then_resumes(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    provider = MockAIProvider()
    # Provider'li iken pure asamalari (1,2) ilerlet; Stage 3 (provider) QUEUED olana kadar.
    for _ in range(20):
        stage3 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION)
        if stage3 and stage3[0].status == AIPipelineStageStatus.QUEUED:
            break
        r = await _processor(maker, provider)
        if (
            r["outcome"]
            in {
                AIStageOutcome.SUCCEEDED.value,
                AIStageOutcome.NO_WORK.value,
            }
            and r.get("stage_type") == AIPipelineStageType.PERSONA_BATCH_PREPARATION.value
        ):
            # Stage 2 basarili -> Stage 3 artik QUEUED; provider'i cikar.
            break
    # Provider'i KALDIR: Stage 3 QUEUED'de guvenle bekler, pipeline FAILED olmaz.
    disabled = await _processor(maker)
    assert disabled["status"] == "disabled"
    stage3 = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert stage3[0].status == AIPipelineStageStatus.QUEUED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status != AIPipelineStatus.FAILED
    # Provider yeniden enjekte edilince ayni pipeline kaldigi yerden tamamlanir.
    await _drive_processor(maker, provider)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


async def test_retryable_error_via_job_then_next_call_completes(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    results = await _drive_processor(maker, provider)
    outcomes = [r["outcome"] for r in results]
    assert AIStageOutcome.RETRY_SCHEDULED.value in outcomes
    # Retry sonrasi tekrar islenip pipeline tamamlanir.
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


async def test_stale_reaper_job_requeues_running_stage(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    assert claimed is not None
    fresh = await _get_stage(maker, claimed.stage_id)
    assert fresh.status == AIPipelineStageStatus.RUNNING
    await _make_stage_stale(maker, claimed.stage_id)
    r = await _reaper(maker)
    assert r["requeued"] == 1
    fresh = await _get_stage(maker, claimed.stage_id)
    assert fresh.status == AIPipelineStageStatus.QUEUED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.RUNNING


async def test_ai_pipeline_failed_but_simulation_run_stays_succeeded(maker):
    env = await _seed_pipeline(maker, num_personas=1)
    await _drive_processor(maker, BrokenScenarioProvider())
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED
    run = await _get_run(maker, env.run_id)
    # AI pipeline FAILED olsa bile baseline SimulationRun DEGISMEZ.
    assert run.status == SimulationStatus.SUCCEEDED


async def test_disabled_pipeline_leaves_baseline_worker_untouched(maker):
    # AI ozelligi kapali (provider yok) iken baseline run kayitlari ve durumu
    # tamamen etkilenmez - AI processor yalnizca no-op yapar.
    env = await _seed_pipeline(maker, num_personas=1)
    for _ in range(3):
        await _processor(maker)
        await _reaper(maker)
    run = await _get_run(maker, env.run_id)
    assert run.status == SimulationStatus.SUCCEEDED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.QUEUED
