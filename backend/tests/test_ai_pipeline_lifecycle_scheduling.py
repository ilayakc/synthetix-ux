"""Faz 3C.2B2: AI pipeline lifecycle arq scheduling (wrapper + cron kayit)
testleri. Desen `tests/test_ai_pipeline_worker_scheduling.py` ile AYNIDIR.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import worker as worker_module
from app.services.ai_pipeline import lifecycle
from app.services.ai_pipeline import scheduling as sched
from app.services.ai_pipeline.scheduling import (
    INITIALIZATION_TASK_NAME,
    SETTLEMENT_TASK_NAME,
    resolve_ctx_provider,
    run_ai_pipeline_initialization_job,
    run_ai_reservation_settlement_job,
)
from app.worker import (
    WorkerSettings,
    initialize_ai_pipeline_groups_job,
    settle_ai_reservations_job,
)
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables

pytestmark = pytest.mark.integration


@pytest.fixture
async def maker(test_engine):
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


# =================================================================================
# REGISTRATION (spec 41-43)
# =================================================================================


def test_init_task_registered_exactly_once():
    matches = [f for f in WorkerSettings.functions if f is initialize_ai_pipeline_groups_job]
    assert len(matches) == 1


def test_settlement_task_registered_exactly_once():
    matches = [f for f in WorkerSettings.functions if f is settle_ai_reservations_job]
    assert len(matches) == 1


def test_init_cron_registered_exactly_once():
    matches = [c for c in WorkerSettings.cron_jobs if c.coroutine is initialize_ai_pipeline_groups_job]
    assert len(matches) == 1


def test_settlement_cron_registered_exactly_once():
    matches = [c for c in WorkerSettings.cron_jobs if c.coroutine is settle_ai_reservations_job]
    assert len(matches) == 1


def test_existing_processor_reaper_baseline_crons_preserved():
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
        worker_module.process_ai_pipeline_stage_job,
        worker_module.reap_stale_ai_pipeline_stages_job,
    }
    assert baseline_funcs.issubset(set(WorkerSettings.functions))
    baseline_cron_coros = {c.coroutine for c in WorkerSettings.cron_jobs}
    assert baseline_funcs.issubset(baseline_cron_coros)


def test_job_and_cron_names_stable_and_unique():
    names = [f.__name__ for f in WorkerSettings.functions]
    assert len(names) == len(set(names))
    assert "initialize_ai_pipeline_groups_job" in names
    assert "settle_ai_reservations_job" in names
    cron_names = [c.name for c in WorkerSettings.cron_jobs]
    assert len(cron_names) == len(set(cron_names))


def test_init_and_settlement_cron_seconds_do_not_collide_with_each_other():
    assert WorkerSettings._AI_INIT_CYCLE_SECONDS.isdisjoint(WorkerSettings._AI_SETTLEMENT_CYCLE_SECONDS)


# =================================================================================
# WRAPPER BEHAVIOR (spec 44-48)
# =================================================================================


async def test_init_wrapper_calls_cycle_exactly_once(monkeypatch):
    calls = {"n": 0}

    async def _fake_cycle(session_maker, *, limit):
        calls["n"] += 1
        return lifecycle.InitializationCycleResult()

    monkeypatch.setattr(sched, "run_ai_pipeline_initialization_cycle", _fake_cycle)
    result = await run_ai_pipeline_initialization_job({})

    assert calls["n"] == 1
    assert result["task"] == INITIALIZATION_TASK_NAME


async def test_settlement_wrapper_calls_cycle_exactly_once(monkeypatch):
    calls = {"n": 0}

    async def _fake_cycle(session_maker, *, limit):
        calls["n"] += 1
        return lifecycle.SettlementCycleResult()

    monkeypatch.setattr(sched, "run_ai_reservation_settlement_cycle", _fake_cycle)
    result = await run_ai_reservation_settlement_job({})

    assert calls["n"] == 1
    assert result["task"] == SETTLEMENT_TASK_NAME


async def test_init_wrapper_requires_no_provider_context(maker):
    # ctx tamamen bos (provider anahtari YOK) - wrapper hata vermeden calisir.
    result = await run_ai_pipeline_initialization_job({}, session_maker=maker)
    assert result["task"] == INITIALIZATION_TASK_NAME


async def test_settlement_wrapper_requires_no_provider_context(maker):
    result = await run_ai_reservation_settlement_job({}, session_maker=maker)
    assert result["task"] == SETTLEMENT_TASK_NAME


def test_lifecycle_and_scheduling_modules_do_not_auto_create_mock_or_call_network():
    # `MockAIProvider(` artik (Faz 3D.2-LOCAL) `app.worker.on_startup` icinde,
    # `OpenAIProvider(`/`OllamaProvider(` ile AYNI sekilde `ai_report_provider_ready`
    # + acikca secilmis `ai_report_provider=="mock"` KAPISININ ARKASINDA kosullu
    # olarak gecer - gizli bir otomatik fallback DEGIL, ACIKCA secilen bir
    # secenektir; bu nedenle metin taramasindan CIKARILDI.
    for module in (lifecycle, sched, worker_module):
        source = inspect.getsource(module)
        for forbidden in ("httpx", "requests", "import socket"):
            assert forbidden not in source
    # Import edilmis WorkerSettings.on_startup GERCEK provider olusturmaz.
    assert resolve_ctx_provider({}) is None


async def test_init_wrapper_reraises_unexpected_exception(monkeypatch):
    async def _boom(session_maker, *, limit):
        raise RuntimeError("beklenmeyen hata")

    monkeypatch.setattr(sched, "run_ai_pipeline_initialization_cycle", _boom)
    with pytest.raises(RuntimeError):
        await run_ai_pipeline_initialization_job({})


async def test_settlement_wrapper_reraises_unexpected_exception(monkeypatch):
    async def _boom(session_maker, *, limit):
        raise RuntimeError("beklenmeyen hata")

    monkeypatch.setattr(sched, "run_ai_reservation_settlement_cycle", _boom)
    with pytest.raises(RuntimeError):
        await run_ai_reservation_settlement_job({})
