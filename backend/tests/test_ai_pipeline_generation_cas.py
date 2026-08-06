"""Faz 3C.3 SORUN 1: manuel retry JENERASYON (ABA / late-result) CAS testleri.

Bu dosya, `worker._relock_for_persist`e eklenen jenerasyon CAS'ini
(`AIPipelineRun.manual_retry_count == ClaimedAIStage.claimed_manual_retry_count`)
dogrular. Senaryo: manuel retry stage'i QUEUED'a dondurup `attempt_count`i 0'a
sifirlar; yeni bir worker AYNI `attempt_count` degerine (1) yeniden ulasabilir
(ABA). Jenerasyon eslesmesi olmadan eski jenerasyona ait GEC gelen bir sonuc
yeni execution gibi kabul edilebilirdi; jenerasyon CAS bunu STALE olarak reddeder.

Testler `test_ai_pipeline_retry_stale.py`/`test_ai_pipeline_worker.py`daki GERCEK
(commit eden, ayri DB session'lari acan) seed/sürüş yardimcilarini YENIDEN
KULLANIR. Her `process_one_ai_stage`/`persist_*`/`_claim_one` cagrisi kendi
BAGIMSIZ, commit edilmis DB transaction'ini acar - yani "eski worker" ile "retry
sonrasi yeni worker" arasindaki late-result yarisi gercek DB satir kilitleri
(`_relock_for_persist` FOR UPDATE) ve commit sirasi uzerinden reproduce edilir.
"""

from __future__ import annotations

import anyio
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker
from sqlalchemy.pool import NullPool

from app.models.ai_pipeline import (
    AIPipelineRun,
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.services.ai_pipeline import mutations
from app.services.ai_pipeline import worker as wk
from app.services.ai_pipeline.worker import (
    AIStageOutcome,
    execute_claimed_ai_stage,
    persist_stage_failure,
    persist_stage_retry,
    persist_stage_success,
    process_one_ai_stage,
)
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables
from tests.test_ai_pipeline_retry_stale import (
    _advance_pure_prefix,
    _claim_one,
    _prepare,
    _stage3_queued_row,
)
from tests.test_ai_pipeline_worker import (
    CountingMockProvider,
    _get_pipeline,
    _get_stage,
    _seed_pipeline,
    _stages,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def maker(test_engine):
    """Commit eden, ayri DB session'lari acan session_maker (bkz. retry_stale)."""

    session_maker = _async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


# --- Jenerasyon bump yardimcilari (manuel retry etkisinin worker-CAS seviyesinde
# ayni DB'de, ayri commit edilmis bir transaction icinde simulasyonu) ------------


async def _bump_generation_requeue(maker, pipeline_id, stage_id) -> None:
    """Manuel retry etkisini birebir taklit eder (worker-CAS seviyesinde): AYRI,
    commit edilmis bir transaction'da pipeline `manual_retry_count`i +1 yapar,
    stage'i QUEUED'a dondurur ve `attempt_count`i 0'a sifirlar (execution/logical
    key + provider/model pin KORUNUR - tipki mutations.retry_ai_pipeline_group)."""

    async with maker() as s:
        pipeline = (
            await s.execute(select(AIPipelineRun).where(AIPipelineRun.id == pipeline_id))
        ).scalar_one()
        stage = (await s.execute(select(AIPipelineStage).where(AIPipelineStage.id == stage_id))).scalar_one()
        pipeline.manual_retry_count += 1
        pipeline.status = AIPipelineStatus.QUEUED
        pipeline.finished_at = None
        stage.status = AIPipelineStageStatus.QUEUED
        stage.attempt_count = 0
        stage.error_code = None
        stage.started_at = None
        stage.finished_at = None
        await s.commit()


async def _bump_generation_only(maker, pipeline_id) -> None:
    """YALNIZCA `manual_retry_count`i +1 yapar (stage RUNNING/attempt DEGISMEZ) -
    jenerasyon CAS'ini attempt/status CAS'inden IZOLE etmek icin."""

    async with maker() as s:
        pipeline = (
            await s.execute(select(AIPipelineRun).where(AIPipelineRun.id == pipeline_id))
        ).scalar_one()
        pipeline.manual_retry_count += 1
        await s.commit()


async def _stage3_generation_aba_setup(maker, provider):
    """Worker A Stage 3'u jenerasyon 0 altinda claim+pin+execute eder (persist
    ETMEZ). Manuel retry jenerasyon bump'i (attempt 0'a, gen 0->1) simule edilir;
    Worker B claim (attempt tekrar 1, gen 1) + execute eder.

    `(claimedA, resultA, claimedB, resultB)` dondurur. KRITIK: claimedA ve claimedB
    AYNI `claimed_attempt_count` (1) degerine sahiptir - yalnizca jenerasyon
    farklidir (ABA)."""

    await _advance_pure_prefix(maker, provider)
    claimedA = await _claim_one(maker)
    assert claimedA.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION
    assert claimedA.claimed_manual_retry_count == 0
    assert claimedA.claimed_attempt_count == 1
    assert (await wk._pin_provider_execution(maker, claimedA, provider=provider)) is wk._PinResult.OK
    planA = await _prepare(maker, claimedA)
    resultA = await execute_claimed_ai_stage(claimedA, planA, provider=provider)

    await _bump_generation_requeue(maker, claimedA.pipeline_id, claimedA.stage_id)

    claimedB = await _claim_one(maker)
    assert claimedB.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION
    assert claimedB.claimed_manual_retry_count == 1
    assert claimedB.claimed_attempt_count == 1  # ABA: A ile AYNI attempt_count
    assert (await wk._pin_provider_execution(maker, claimedB, provider=provider)) is wk._PinResult.OK
    planB = await _prepare(maker, claimedB)
    resultB = await execute_claimed_ai_stage(claimedB, planB, provider=provider)
    return claimedA, resultA, claimedB, resultB


# =================================================================================
# ABA / GENERATION (12)
# =================================================================================


async def test_claim_carries_generation_zero(maker):  # (1)
    await _seed_pipeline(maker)
    claimed = await _claim_one(maker)
    assert claimed is not None
    assert claimed.claimed_manual_retry_count == 0


async def test_generation_zero_success_accepted(maker):  # (2)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    r = await process_one_ai_stage(maker, provider=provider)  # Stage 3, gen 0
    assert r.outcome == AIStageOutcome.SUCCEEDED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.SUCCEEDED


async def test_generation_zero_result_rejected_after_bump_to_one(maker):  # (3)
    await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _cB, _rB = await _stage3_generation_aba_setup(maker, provider)
    rA = await persist_stage_success(maker, claimedA, resultA, provider=provider)
    assert rA.outcome == AIStageOutcome.STALE_RESULT_REJECTED


async def test_old_result_writes_no_validated_output(maker):  # (4)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _cB, _rB = await _stage3_generation_aba_setup(maker, provider)
    await persist_stage_success(maker, claimedA, resultA, provider=provider)
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.validated_output is None  # eski jenerasyon cikti YAZMAZ


async def test_old_result_does_not_change_totals(maker):  # (5)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _cB, _rB = await _stage3_generation_aba_setup(maker, provider)
    before = await _get_pipeline(maker, env.pipeline_id)
    tin, tout = before.total_input_tokens, before.total_output_tokens
    await persist_stage_success(maker, claimedA, resultA, provider=provider)
    after = await _get_pipeline(maker, env.pipeline_id)
    assert (after.total_input_tokens, after.total_output_tokens) == (tin, tout)


async def test_old_result_creates_no_successor(maker):  # (6)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _cB, _rB = await _stage3_generation_aba_setup(maker, provider)
    await persist_stage_success(maker, claimedA, resultA, provider=provider)
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert behavior == []  # eski jenerasyon successor YARATMAZ


async def test_new_generation_result_accepted(maker):  # (7)
    env = await _seed_pipeline(maker)  # 1 persona -> 1 behavior batch
    provider = CountingMockProvider()
    claimedA, resultA, claimedB, resultB = await _stage3_generation_aba_setup(maker, provider)
    await persist_stage_success(maker, claimedA, resultA, provider=provider)  # reddedilir
    rB = await persist_stage_success(maker, claimedB, resultB, provider=provider)
    assert rB.outcome == AIStageOutcome.SUCCEEDED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.SUCCEEDED
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert len(behavior) == 1  # successor tam BIR kez (yeni jenerasyondan)


async def test_same_attempt_count_but_old_generation_rejected(maker):  # (8)
    await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, claimedB, _rB = await _stage3_generation_aba_setup(maker, provider)
    # Ikisi de attempt_count 1; yalnizca jenerasyon farkli.
    assert claimedA.claimed_attempt_count == claimedB.claimed_attempt_count == 1
    assert claimedA.claimed_manual_retry_count == 0
    assert claimedB.claimed_manual_retry_count == 1
    rA = await persist_stage_success(maker, claimedA, resultA, provider=provider)
    assert rA.outcome == AIStageOutcome.STALE_RESULT_REJECTED


async def test_pin_returns_stale_on_generation_mismatch_before_provider_call(maker):  # (9)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    claimedA = await _claim_one(maker)  # Stage 3, gen 0, RUNNING attempt 1 (henuz pin YOK)
    assert claimedA.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION
    # Yalnizca jenerasyonu bump'la (stage RUNNING/attempt DEGISMEZ) -> attempt/status
    # CAS gecerdi ama jenerasyon uymaz.
    await _bump_generation_only(maker, env.pipeline_id)
    calls_before = len(provider.calls)
    pin = await wk._pin_provider_execution(maker, claimedA, provider=provider)
    assert pin is wk._PinResult.STALE  # provider CAGRILMADAN once STALE
    assert len(provider.calls) == calls_before  # provider CAGRILMADI


async def test_failure_and_retry_persistence_reject_old_generation(maker):  # (10)
    # Failure yolu (require_runnable_pipeline=False) da jenerasyon CAS uygular.
    await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    claimedA = await _claim_one(maker)
    assert (await wk._pin_provider_execution(maker, claimedA, provider=provider)) is wk._PinResult.OK
    await _bump_generation_only(maker, claimedA.pipeline_id)
    rf = await persist_stage_failure(maker, claimedA, error_code="provider_timeout")
    assert rf.outcome == AIStageOutcome.STALE_RESULT_REJECTED

    # Retry yolu (require_runnable_pipeline=True) da reddeder.
    await _seed_pipeline(maker)
    provider2 = CountingMockProvider()
    await _advance_pure_prefix(maker, provider2)
    claimedB = await _claim_one(maker)
    assert (await wk._pin_provider_execution(maker, claimedB, provider=provider2)) is wk._PinResult.OK
    await _bump_generation_only(maker, claimedB.pipeline_id)
    rr = await persist_stage_retry(maker, claimedB, error_code="provider_timeout")
    assert rr.outcome == AIStageOutcome.STALE_RESULT_REJECTED


async def test_stage4_old_batch_result_creates_no_fan_in(maker):  # (11)
    env = await _seed_pipeline(maker, num_personas=16)  # 2 behavior batch
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    r3 = await process_one_ai_stage(maker, provider=provider)  # Stage 3 -> 2 PERSONA_BEHAVIOR
    assert r3.outcome == AIStageOutcome.SUCCEEDED
    # Bir behavior batch'i gen 0 altinda claim+pin+execute et (persist ETME).
    claimedA = await _claim_one(maker)
    assert claimedA.stage_type == AIPipelineStageType.PERSONA_BEHAVIOR
    assert (await wk._pin_provider_execution(maker, claimedA, provider=provider)) is wk._PinResult.OK
    planA = await _prepare(maker, claimedA)
    resultA = await execute_claimed_ai_stage(claimedA, planA, provider=provider)
    # Jenerasyon bump (bu batch'i requeue eder).
    await _bump_generation_requeue(maker, claimedA.pipeline_id, claimedA.stage_id)
    # Eski gen batch sonucunu persist et -> STALE; AGGREGATION (fan-in) OLUSMAZ.
    rA = await persist_stage_success(maker, claimedA, resultA, provider=provider)
    assert rA.outcome == AIStageOutcome.STALE_RESULT_REJECTED
    agg = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.AGGREGATION)
    assert agg == []


async def test_ab_retry_bumps_all_sibling_generations(session):  # (12)
    """A/B grubunda manuel retry TUM sibling pipeline generation'larini (SUCCEEDED
    korunan dahil) esit yeni jenerasyona getirir - dolayisiyla HERHANGI bir
    sibling uzerinde ucusta olan (eski gen) bir worker sonucu da reddedilir."""

    from tests.test_ai_pipeline_mutations import (
        _failed_pipeline,
        _make_group,
        _make_org,
        _succeeded_pipeline,
    )

    org = await _make_org(session)
    runs, pipelines, _res = await _make_group(
        session, org=org, pipelines=[_succeeded_pipeline(), _failed_pipeline()]
    )
    assert {p.manual_retry_count for p in pipelines} == {0}
    await mutations.retry_ai_pipeline_group(session, organization_id=org.id, simulation_run_id=runs[1].id)
    # TUM sibling'lar (SUCCEEDED preserved A dahil) AYNI yeni jenerasyon 1.
    fresh = (
        (
            await session.execute(
                select(AIPipelineRun).where(AIPipelineRun.simulation_run_id.in_([r.id for r in runs]))
            )
        )
        .scalars()
        .all()
    )
    assert {p.manual_retry_count for p in fresh} == {1}


# =================================================================================
# CONCURRENCY (gercek iki-session)
# =================================================================================


async def test_stale_requeue_then_old_result_rejected_real_sessions(maker):  # (C-generation)
    """GERCEK ayri DB session'lari: worker A Stage 3'u claim+execute eder; stale
    reaper AYRI bir transaction'da requeue eder; worker B AYRI bir transaction'da
    claim+execute eder; A'nin gec sonucu (jenerasyon degismese de attempt CAS ile,
    jenerasyon degisirse jenerasyon CAS ile) reddedilir. Bu test attempt-CAS ile
    jenerasyon-CAS'in birlikte late-result'u reddettigini gercek commit sirasiyla
    dogrular."""

    await _seed_pipeline(maker)
    provider = CountingMockProvider()
    # A: claim + pin + execute (persist YOK).
    await _advance_pure_prefix(maker, provider)
    claimedA = await _claim_one(maker)
    assert (await wk._pin_provider_execution(maker, claimedA, provider=provider)) is wk._PinResult.OK
    planA = await _prepare(maker, claimedA)
    resultA = await execute_claimed_ai_stage(claimedA, planA, provider=provider)
    # Jenerasyon bump (ayri transaction) - manuel retry.
    await _bump_generation_requeue(maker, claimedA.pipeline_id, claimedA.stage_id)
    fresh = await _get_stage(maker, claimedA.stage_id)
    assert fresh.status == AIPipelineStageStatus.QUEUED
    # B: yeni jenerasyonda claim + execute + persist (basarili).
    claimedB = await _claim_one(maker)
    assert (await wk._pin_provider_execution(maker, claimedB, provider=provider)) is wk._PinResult.OK
    planB = await _prepare(maker, claimedB)
    resultB = await execute_claimed_ai_stage(claimedB, planB, provider=provider)
    rB = await persist_stage_success(maker, claimedB, resultB, provider=provider)
    assert rB.outcome == AIStageOutcome.SUCCEEDED
    # A gec gelir -> reddedilir (stage artik SUCCEEDED + jenerasyon 1).
    rA = await persist_stage_success(maker, claimedA, resultA, provider=provider)
    assert rA.outcome == AIStageOutcome.STALE_RESULT_REJECTED


async def _seed_committed_failed_group(*, ab: bool) -> tuple:
    """Ayri bir engine ile, RELEASED AI rezervasyonlu + FAILED pipeline'li (ab ise
    iki sibling) bir grup COMMIT eder. `(org_id, run_id)` dondurur."""

    from tests.test_ai_pipeline_mutations import _failed_pipeline, _make_group, _make_org

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            org = await _make_org(s)
            specs = [_failed_pipeline(), _failed_pipeline()] if ab else [_failed_pipeline()]
            runs, _pipelines, _res = await _make_group(s, org=org, pipelines=specs)
            org_id, run_id = org.id, runs[0].id
            await s.commit()
        return org_id, run_id
    finally:
        await engine.dispose()


async def _retry_own_session(org_id, run_id, results, barrier) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            await barrier.wait()  # iki task ayni anda advisory lock yarisina girsin
            try:
                r = await mutations.retry_ai_pipeline_group(
                    s, organization_id=org_id, simulation_run_id=run_id
                )
                await s.commit()
                results.append(("ok", r.charged_chips))
            except mutations.AIPipelineMutationError as exc:
                await s.rollback()
                results.append(("conflict", exc.code))
    finally:
        await engine.dispose()


async def test_concurrent_retry_single_reservation_real_two_sessions():  # (C-retry)
    """GERCEK iki eszamanli session: ayni gruba iki paralel manuel retry.
    `pg_advisory_xact_lock` (salt=3) serilestirir; TAM BIR tanesi -50 uygular,
    digeri conflict (pipeline artik QUEUED). Cift ucret/cift rezervasyon OLMAZ."""

    org_id, run_id = await _seed_committed_failed_group(ab=False)
    results: list[tuple[str, object]] = []
    barrier = anyio.Event()

    async def _release_barrier():
        # Iki task da baglantiyi acsin, sonra ikisini birden serbest birak.
        await anyio.sleep(0.2)
        barrier.set()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_retry_own_session, org_id, run_id, results, barrier)
            tg.start_soon(_retry_own_session, org_id, run_id, results, barrier)
            tg.start_soon(_release_barrier)

        oks = [r for r in results if r[0] == "ok"]
        conflicts = [r for r in results if r[0] == "conflict"]
        assert len(oks) == 1, results  # TAM BIR -50
        assert len(conflicts) == 1, results

        # DB'de yalnizca tek yeni RESERVED rezervasyon (jenerasyon 1).
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                pipeline = (
                    await s.execute(select(AIPipelineRun).where(AIPipelineRun.simulation_run_id == run_id))
                ).scalar_one()
                assert pipeline.manual_retry_count == 1  # yalnizca BIR kez artti
        finally:
            await engine.dispose()
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)
