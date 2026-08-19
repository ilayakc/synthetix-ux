"""Faz 3B.2C: kontrollu retry, provider/model pinning, 600s stale RUNNING
recovery ve late-result/CAS korumasi testleri (50 senaryo).

Bu dosya, `app.services.ai_pipeline.worker`in Faz 3B.2C'de eklenen davranisini
dogrular. Var olan DAG/claim/pure-stage kapsamasi `test_ai_pipeline_worker.py`de
kalir; buradaki testler o dosyadaki seed/sürüş yardimcilarini YENIDEN KULLANIR
(kopyalamaz). SKIP LOCKED senaryolari GERCEK iki eszamanli PostgreSQL session'i
ile kanitlanir.

Numaralandirma, gorev spec'indeki 50 senaryoyu birebir izler:
- MODEL/MIGRATION (1-6)
- KEY/PINNING (7-15)
- RETRY (16-25)
- STALE RECOVERY (26-34)
- LATE RESULT/CAS (35-41)
- END-TO-END (42-50)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.ai_pipeline import (
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.services.ai_pipeline import worker as wk
from app.services.ai_pipeline.hashing import hash_prompt_descriptor
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.prompts import get_prompt
from app.services.ai_pipeline.provider_errors import (
    AIProviderInvalidOutputError,
    AIProviderTransportError,
)
from app.services.ai_pipeline.stage_hashing import (
    deterministic_stage_idempotency_key,
    provider_stage_execution_idempotency_key,
)
from app.services.ai_pipeline.worker import (
    ERROR_PROVIDER_CONFIGURATION_MISMATCH,
    ERROR_PROVIDER_NOT_CONFIGURED,
    ERROR_STAGE_EXECUTION_KEY_MISMATCH,
    ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED,
    ERROR_STALE_EXECUTION_CANCELLED,
    ERROR_STALE_EXECUTION_REQUEUED,
    MAX_STAGE_ATTEMPTS,
    STALE_RUNNING_TIMEOUT_SECONDS,
    AIStageOutcome,
    claim_next_ai_stage,
    execute_claimed_ai_stage,
    persist_stage_success,
    process_one_ai_stage,
    reap_stale_ai_stages,
)
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables

# Var olan worker testinin seed/sürüş/erisim yardimcilarini yeniden kullan.
from tests.test_ai_pipeline_worker import (
    BrokenScenarioProvider,
    CountingMockProvider,
    _get_pipeline,
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
    """Belirli bir provider stage'ini ilk `fail_times` cagrida `error` ile
    reddeder, sonra `MockAIProvider`e devrederek BASARIR. `provider_name`/
    `model_name`, inner mock'in dondurdugu `ProviderResult` ile TUTARLI olacak
    sekilde mock'in kimligini tasir (pin ile audit birebir eslessin)."""

    provider_name = MockAIProvider.provider_name
    model_name = MockAIProvider.model_name
    is_mock = False
    configuration_fingerprint = MockAIProvider.configuration_fingerprint

    def __init__(self, *, fail_stage, fail_times=1, error=None):
        self._inner = MockAIProvider()
        self._fail_stage = fail_stage
        self._remaining = fail_times
        self._error = error or AIProviderTransportError("gecici tasima hatasi")
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        if stage_type is self._fail_stage and self._remaining > 0:
            self._remaining -= 1
            raise self._error
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


class FlakyBatchProvider:
    """YALNIZCA belirli bir `(stage_type, batch_index)` batch'ini ilk cagrida
    reddeder; diger batch'ler ve stage'ler dogrudan basarir."""

    provider_name = MockAIProvider.provider_name
    model_name = MockAIProvider.model_name
    is_mock = False
    configuration_fingerprint = MockAIProvider.configuration_fingerprint

    def __init__(self, *, fail_batch_index):
        self._inner = MockAIProvider()
        self._fail_batch_index = fail_batch_index
        self._failed = False
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        if (
            stage_type is AIPipelineStageType.PERSONA_BEHAVIOR
            and batch_index == self._fail_batch_index
            and not self._failed
        ):
            self._failed = True
            raise AIProviderTransportError("bir batch gecici olarak basarisiz")
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


class RenamedProvider:
    """Verilen kimlikle (provider_name/model_name) mock ciktisini uretir - farkli
    provider/model ile retry (pin uyusmazligi) senaryolari icin."""

    is_mock = False
    configuration_fingerprint = MockAIProvider.configuration_fingerprint

    def __init__(self, *, provider_name, model_name):
        self.provider_name = provider_name
        self.model_name = model_name
        self._inner = MockAIProvider()
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        result = await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )
        return result.model_copy(update={"provider_name": self.provider_name, "model_name": self.model_name})


class LyingModelProvider:
    """Pin icin `model_name` X kullanir ama `ProviderResult`ta FARKLI bir
    `model_name` (Y) rapor eder - persist'te execution-key uyusmazligini tetikler
    (integrity failure: cikti/successor yazilmamali)."""

    provider_name = MockAIProvider.provider_name
    model_name = MockAIProvider.model_name  # pin bunu kullanir
    is_mock = False
    configuration_fingerprint = MockAIProvider.configuration_fingerprint

    def __init__(self):
        self._inner = MockAIProvider()
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        result = await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )
        return result.model_copy(update={"model_name": "lied-about-model"})


# --- Ortak sürüş yardimcilari ----------------------------------------------------


async def _advance_pure_prefix(maker, provider) -> None:
    """Stage 1 (evidence) ve Stage 2 (batch) saf asamalarini calistirir; sonraki
    claim edilebilir asama SCENARIO_INTERPRETATION (ilk provider asamasi) olur."""

    r1 = await process_one_ai_stage(maker, provider=provider)
    assert r1.outcome == AIStageOutcome.SUCCEEDED
    assert r1.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION
    r2 = await process_one_ai_stage(maker, provider=provider)
    assert r2.outcome == AIStageOutcome.SUCCEEDED
    assert r2.stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION


async def _claim_one(maker):
    async with maker() as s:
        claimed = await claim_next_ai_stage(s)
        await s.commit()
    return claimed


async def _prepare(maker, claimed):
    async with maker() as s:
        return await wk._prepare_stage_execution(s, claimed)


async def _reap(maker, *, seconds_ahead: int = 10_000, **kwargs):
    """Tum RUNNING stage'leri kesin olarak stale yapan (buyuk ileri `now`)
    kolayligi; sinir testleri kendi `now`'unu verir."""

    now = datetime.now(UTC) + timedelta(seconds=seconds_ahead)
    return await reap_stale_ai_stages(maker, now=now, **kwargs)


async def _stage3_queued_row(maker, pipeline_id) -> AIPipelineStage:
    rows = await _stages(maker, pipeline_id, stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert len(rows) == 1
    return rows[0]


# =================================================================================
# MODEL / MIGRATION (1-6)
# =================================================================================


def test_execution_key_column_in_model():  # (1a)
    col = AIPipelineStage.__table__.c.execution_idempotency_key
    assert col.nullable is True
    assert col.type.length == 128


async def test_execution_key_column_present_in_db(test_engine):  # (1b)
    def _cols(sync_conn):
        return {c["name"]: c for c in inspect(sync_conn).get_columns("ai_pipeline_stages")}

    async with test_engine.connect() as conn:
        cols = await conn.run_sync(_cols)
    assert "execution_idempotency_key" in cols
    assert cols["execution_idempotency_key"]["nullable"] is True


async def test_single_alembic_head():  # (2)
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    # Tek Alembic head invariant'i. En son ilerletme: page_analyses.next_attempt_at
    # (kalici gecikmeli yeniden deneme; bkz. b7d2f9a4c1e8).
    assert heads == ["b7d2f9a4c1e8"]


async def test_migration_down_revision_chains_to_prior_head():  # (3)
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config("alembic.ini")
    cfg.set_main_option("script_location", "migrations")
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("a3e9c1d75f28")
    assert rev.down_revision == "d62d90ed418a"


async def test_migration_upgrade_and_downgrade_bodies_complete():  # (4)
    # Yapisal butunluk: upgrade kolonu+unique constraint ekler, downgrade ikisini
    # de geri alir (bos govde/half-finished DEGIL). Yikici tam roundtrip
    # `test_database.py::test_alembic_downgrade_and_upgrade_roundtrip` tarafindan
    # tum sema uzerinde ayrica calistirilir (bu migration da dahil).
    import importlib

    mod = importlib.import_module("migrations.versions.a3e9c1d75f28_ai_pipeline_stage_execution_key")
    import inspect as _inspect

    up_src = _inspect.getsource(mod.upgrade)
    down_src = _inspect.getsource(mod.downgrade)
    assert "add_column" in up_src and "execution_idempotency_key" in up_src
    assert "create_unique_constraint" in up_src
    assert "drop_constraint" in down_src and "drop_column" in down_src


async def test_existing_rows_allow_null_execution_key(maker):  # (5)
    env = await _seed_pipeline(maker)
    # initialize_ai_pipeline sadece QUEUED Stage 1 uretir; henuz pinlenmemis =>
    # execution_idempotency_key NULL kalabilir (backfill/placeholder YOK).
    rows = await _stages(maker, env.pipeline_id)
    assert len(rows) == 1
    assert rows[0].execution_idempotency_key is None


async def test_execution_key_has_unique_constraint_in_db(test_engine):  # (6)
    def _uniques(sync_conn):
        return inspect(sync_conn).get_unique_constraints("ai_pipeline_stages")

    async with test_engine.connect() as conn:
        uniques = await conn.run_sync(_uniques)
    cols = [tuple(u["column_names"]) for u in uniques]
    assert ("execution_idempotency_key",) in cols
    # Mantiksal `idempotency_key` UNIQUE'i de KORUNUR.
    assert ("idempotency_key",) in cols


# =================================================================================
# KEY / PINNING (7-15)
# =================================================================================


async def test_pure_stage_does_not_pin_provider(maker):  # (7)
    env = await _seed_pipeline(maker)
    r = await process_one_ai_stage(maker, provider=None)  # Stage 1 saf
    assert r.outcome == AIStageOutcome.SUCCEEDED
    stage = (await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.EVIDENCE_PREPARATION))[0]
    # Saf asamada execution key = logical key (deterministik), gercek provider YOK.
    assert stage.execution_idempotency_key == stage.idempotency_key


async def test_logical_key_is_provider_independent(maker):  # (8)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    # QUEUED Stage 3'un logical key'i provider'dan BAGIMSIZ deterministik degerdir.
    expected = deterministic_stage_idempotency_key(
        simulation_run_id=env.run_id,
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        input_hash=stage3.input_hash,
    )
    assert stage3.idempotency_key == expected


async def test_execution_key_matches_runner_audit_key(maker):  # (9)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    prompt = get_prompt(AIPipelineStageType.SCENARIO_INTERPRETATION)
    expected = provider_stage_execution_idempotency_key(
        simulation_run_id=env.run_id,
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt_version=prompt.prompt_version,
        prompt_hash=hash_prompt_descriptor(prompt),
        provider=provider.provider_name,
        model_name=provider.model_name,
        input_hash=stage3.input_hash,
        provider_configuration_fingerprint=provider.configuration_fingerprint,
    )
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.SUCCEEDED
    stage3 = await _get_stage(maker, stage3.id)
    assert stage3.execution_idempotency_key == expected
    # Logical key'den FARKLIDIR (provider/model'e bagli).
    assert stage3.execution_idempotency_key != stage3.idempotency_key


async def test_success_persists_execution_key(maker):  # (10)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.SUCCEEDED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.SUCCEEDED
    assert stage3.execution_idempotency_key is not None
    assert stage3.provider == provider.provider_name
    assert stage3.model_name == provider.model_name


async def test_first_execution_pins_before_provider_call(maker):  # (11)
    env = await _seed_pipeline(maker)
    # Stage 3 ilk denemede retryable HATA verir; pin provider cagrisindan ONCE
    # yapildigi icin, QUEUED'a donen satirda pin ZATEN mevcut olmalidir.
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.RETRY_SCHEDULED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.QUEUED
    assert stage3.execution_idempotency_key is not None  # pin provider cagrisindan ONCE
    assert stage3.provider == provider.provider_name
    assert stage3.model_name == provider.model_name


async def test_retry_uses_same_provider_model_key(maker):  # (11b/12)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    await process_one_ai_stage(maker, provider=provider)  # attempt1 fail -> QUEUED
    pinned = await _stage3_queued_row(maker, env.pipeline_id)
    key_after_fail = pinned.execution_idempotency_key
    r2 = await process_one_ai_stage(maker, provider=provider)  # attempt2 basari
    assert r2.outcome == AIStageOutcome.SUCCEEDED
    stage3 = await _get_stage(maker, pinned.id)
    assert stage3.execution_idempotency_key == key_after_fail
    assert stage3.provider == provider.provider_name
    assert stage3.model_name == provider.model_name


async def test_retry_with_different_provider_rejected_before_call(maker):  # (13)
    env = await _seed_pipeline(maker)
    first = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, first)
    await process_one_ai_stage(maker, provider=first)  # pin=mock, attempt1 fail
    other = RenamedProvider(provider_name="other-provider", model_name="other-model")
    r = await process_one_ai_stage(maker, provider=other)
    assert r.outcome == AIStageOutcome.PROVIDER_CONFIGURATION_MISMATCH
    assert r.error_code == ERROR_PROVIDER_CONFIGURATION_MISMATCH
    assert other.calls == []  # provider CAGRILMADI
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.FAILED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED


async def test_retry_with_different_model_rejected_before_call(maker):  # (14)
    env = await _seed_pipeline(maker)
    first = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, first)
    await process_one_ai_stage(maker, provider=first)  # pin=mock/deterministic-template-v1
    # Ayni provider adi, FARKLI model -> yine pin uyusmazligi.
    other = RenamedProvider(provider_name=MockAIProvider.provider_name, model_name="different-model")
    r = await process_one_ai_stage(maker, provider=other)
    assert r.outcome == AIStageOutcome.PROVIDER_CONFIGURATION_MISMATCH
    assert other.calls == []
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.FAILED


async def test_execution_key_mismatch_writes_no_output_or_successor(maker):  # (15)
    env = await _seed_pipeline(maker)
    provider = LyingModelProvider()  # pin model X, audit model Y -> mismatch
    await _advance_pure_prefix(maker, provider)
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.FAILED
    assert r.error_code == ERROR_STAGE_EXECUTION_KEY_MISMATCH
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.FAILED
    assert stage3.validated_output is None
    # Successor (PERSONA_BEHAVIOR) OLUSTURULMAMALI.
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert behavior == []


# =================================================================================
# RETRY (16-25)
# =================================================================================


async def test_first_retryable_error_requeues_stage(maker):  # (16)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.RETRY_SCHEDULED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.QUEUED
    assert stage3.finished_at is None


async def test_pipeline_stays_running_during_retry(maker):  # (17)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    await process_one_ai_stage(maker, provider=provider)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.RUNNING
    assert pipeline.finished_at is None


async def test_attempt_count_preserved_then_incremented_on_next_claim(maker):  # (18)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    await process_one_ai_stage(maker, provider=provider)  # attempt1 fail
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.attempt_count == 1  # requeue attempt_count'u ARTIRMAZ
    await process_one_ai_stage(maker, provider=provider)  # attempt2 (basari)
    stage3 = await _get_stage(maker, stage3.id)
    assert stage3.attempt_count == 2  # artis yalnizca claim'de


async def test_second_retryable_error_fails_stage_and_pipeline(maker):  # (19)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=2)
    await _advance_pure_prefix(maker, provider)
    r1 = await process_one_ai_stage(maker, provider=provider)
    assert r1.outcome == AIStageOutcome.RETRY_SCHEDULED
    r2 = await process_one_ai_stage(maker, provider=provider)
    assert r2.outcome == AIStageOutcome.FAILED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.FAILED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED


async def test_repairable_validation_error_retries_once_then_fails(maker):  # (20)
    env = await _seed_pipeline(maker)
    provider = BrokenScenarioProvider()  # sema-gecerli ama kaniti bozuk -> bir kez onarim retry'i
    await _advance_pure_prefix(maker, provider)
    first = await process_one_ai_stage(maker, provider=provider)
    assert first.outcome == AIStageOutcome.RETRY_SCHEDULED
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.FAILED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.FAILED
    assert stage3.attempt_count == 2  # bounded: yalnizca bir onarim retry'i


async def test_provider_missing_is_not_retried(maker):  # (21)
    env = await _seed_pipeline(maker)
    await _advance_pure_prefix(maker, None)  # saf asamalar provider'siz calisir
    r = await process_one_ai_stage(maker, provider=None)  # Stage 3 provider gerekir
    assert r.outcome == AIStageOutcome.FAILED
    assert r.error_code == ERROR_PROVIDER_NOT_CONFIGURED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.FAILED
    assert stage3.attempt_count == 1


async def test_validation_error_is_not_retried(maker):  # (22)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(
        fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION,
        fail_times=1,
        error=AIProviderInvalidOutputError("gecersiz cikti"),
    )
    await _advance_pure_prefix(maker, provider)
    r = await process_one_ai_stage(maker, provider=provider)
    assert r.outcome == AIStageOutcome.FAILED  # non-retryable -> terminal
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.attempt_count == 1
    assert provider.calls.count((AIPipelineStageType.SCENARIO_INTERPRETATION, None)) == 1


async def test_retry_does_not_change_keys(maker):  # (23)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    await process_one_ai_stage(maker, provider=provider)
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    logical_before = stage3.idempotency_key
    exec_before = stage3.execution_idempotency_key
    await process_one_ai_stage(maker, provider=provider)  # attempt2 basari
    stage3 = await _get_stage(maker, stage3.id)
    assert stage3.idempotency_key == logical_before
    assert stage3.execution_idempotency_key == exec_before


async def test_retry_creates_no_successor(maker):  # (24)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    await _advance_pure_prefix(maker, provider)
    await process_one_ai_stage(maker, provider=provider)  # retry
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert behavior == []  # successor YOK


async def test_maximum_two_attempts(maker):  # (25)
    await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=5)
    await _advance_pure_prefix(maker, provider)
    await process_one_ai_stage(maker, provider=provider)  # attempt1
    await process_one_ai_stage(maker, provider=provider)  # attempt2 (terminal)
    # 3. cagri artik claim edecek is BULAMAZ (stage FAILED).
    r3 = await process_one_ai_stage(maker, provider=provider)
    assert r3.outcome == AIStageOutcome.NO_WORK
    stage3_calls = provider.calls.count((AIPipelineStageType.SCENARIO_INTERPRETATION, None))
    assert stage3_calls == MAX_STAGE_ATTEMPTS == 2


# =================================================================================
# STALE RECOVERY (26-34)
# =================================================================================


async def _claim_stage1_running(maker):
    """Stage 1'i RUNNING'e claim eder ve (pipeline_id, stage, updated_at) dondurur."""

    claimed = await _claim_one(maker)
    assert claimed is not None
    stage = await _get_stage(maker, claimed.stage_id)
    return claimed, stage


async def test_599s_is_not_stale(maker):  # (26)
    await _seed_pipeline(maker)
    _claimed, stage = await _claim_stage1_running(maker)
    now = stage.updated_at + timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS - 1)
    result = await reap_stale_ai_stages(maker, now=now)
    assert result.scanned == 0
    assert result.requeued == 0
    fresh = await _get_stage(maker, stage.id)
    assert fresh.status == AIPipelineStageStatus.RUNNING


async def test_600s_is_stale_and_requeued(maker):  # (27)/(28)
    env = await _seed_pipeline(maker)
    _claimed, stage = await _claim_stage1_running(maker)
    now = stage.updated_at + timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS)
    result = await reap_stale_ai_stages(maker, now=now)
    assert result.scanned == 1
    assert result.requeued == 1
    fresh = await _get_stage(maker, stage.id)
    assert fresh.status == AIPipelineStageStatus.QUEUED
    assert fresh.error_code == ERROR_STALE_EXECUTION_REQUEUED
    assert fresh.attempt_count == 1  # ARTIRILMAZ
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.RUNNING


async def test_last_attempt_stale_fails(maker):  # (29)
    env = await _seed_pipeline(maker)
    _claimed, stage = await _claim_stage1_running(maker)
    async with maker() as s:
        row = (await s.execute(select(AIPipelineStage).where(AIPipelineStage.id == stage.id))).scalar_one()
        row.attempt_count = MAX_STAGE_ATTEMPTS
        await s.commit()
    fresh = await _get_stage(maker, stage.id)  # updated_at yeniden bumped
    now = fresh.updated_at + timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS)
    result = await reap_stale_ai_stages(maker, now=now)
    assert result.failed == 1
    fresh = await _get_stage(maker, stage.id)
    assert fresh.status == AIPipelineStageStatus.FAILED
    assert fresh.error_code == ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED


async def test_reaper_skips_locked_stage_real_two_sessions(maker):  # (30)/(31)
    await _seed_pipeline(maker)
    _claimed, stage = await _claim_stage1_running(maker)
    cutoff = stage.updated_at + timedelta(seconds=STALE_RUNNING_TIMEOUT_SECONDS)
    async with maker() as s1, maker() as s2:
        locked = await wk._select_stale_ai_stages(s1, cutoff=cutoff, limit=10)
        assert len(locked) == 1  # s1 kilitledi (henuz commit yok)
        skipped = await wk._select_stale_ai_stages(s2, cutoff=cutoff, limit=10)
        assert skipped == []  # SKIP LOCKED -> ikinci reaper alamaz
        await s1.rollback()


async def test_reaper_deterministic_order_and_limit(maker):  # (32)
    a = await _seed_pipeline(maker)
    b = await _seed_pipeline(maker)
    c = await _seed_pipeline(maker)
    # Uc pipeline'in Stage 1'lerini sirayla (created_at ASC) RUNNING'e claim et.
    for _ in range(3):
        await _claim_one(maker)
    result = await _reap(maker, limit=2)
    assert result.scanned == 2
    assert result.requeued == 2
    # En yeni pipeline (c) HALA RUNNING olmali (limit sinirinda disarida kaldi).
    c_stage = (await _stages(maker, c.pipeline_id))[0]
    assert c_stage.status == AIPipelineStageStatus.RUNNING
    for env in (a, b):
        st = (await _stages(maker, env.pipeline_id))[0]
        assert st.status == AIPipelineStageStatus.QUEUED


async def test_cancelled_pipeline_stale_stage_not_requeued(maker):  # (33)
    env = await _seed_pipeline(maker, cancel_run=True)
    _claimed, stage = await _claim_stage1_running(maker)
    result = await _reap(maker)
    assert result.cancelled == 1
    assert result.requeued == 0
    fresh = await _get_stage(maker, stage.id)
    assert fresh.status == AIPipelineStageStatus.CANCELLED
    assert fresh.error_code == ERROR_STALE_EXECUTION_CANCELLED
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.CANCELLED


async def test_stale_requeue_preserves_provider_pin(maker):  # (34)
    await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    # Stage 3'u claim et + pinle, calistirma (RUNNING kalir).
    claimed = await _claim_one(maker)
    assert claimed.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION
    pin = await wk._pin_provider_execution(maker, claimed, provider=provider)
    assert pin is wk._PinResult.OK
    pinned = await _get_stage(maker, claimed.stage_id)
    exec_key = pinned.execution_idempotency_key
    assert exec_key is not None
    result = await _reap(maker)
    assert result.requeued == 1
    fresh = await _get_stage(maker, claimed.stage_id)
    assert fresh.status == AIPipelineStageStatus.QUEUED
    assert fresh.execution_idempotency_key == exec_key  # pin KORUNUR
    assert fresh.provider == provider.provider_name
    assert fresh.model_name == provider.model_name
    assert fresh.attempt_count == 1


# =================================================================================
# LATE RESULT / CAS (35-41)
# =================================================================================


async def _stage3_late_result_setup(maker, provider):
    """Worker A Stage 3'u claim+execute eder; stale reaper requeue eder; Worker B
    claim+execute eder. `(claimedA, resultA, claimedB, resultB)` dondurur - A henuz
    persist ETMEMISTIR."""

    await _advance_pure_prefix(maker, provider)
    claimedA = await _claim_one(maker)
    assert claimedA.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION
    assert (await wk._pin_provider_execution(maker, claimedA, provider=provider)) is wk._PinResult.OK
    planA = await _prepare(maker, claimedA)
    resultA = await execute_claimed_ai_stage(claimedA, planA, provider=provider)
    # Stale reaper: attempt1 RUNNING -> QUEUED (attempt_count 1).
    assert (await _reap(maker)).requeued == 1
    # Worker B claim (attempt2) + execute.
    claimedB = await _claim_one(maker)
    assert claimedB.claimed_attempt_count == 2
    assert (await wk._pin_provider_execution(maker, claimedB, provider=provider)) is wk._PinResult.OK
    planB = await _prepare(maker, claimedB)
    resultB = await execute_claimed_ai_stage(claimedB, planB, provider=provider)
    return claimedA, resultA, claimedB, resultB


async def test_late_attempt1_result_rejected_after_attempt2(maker):  # (35)
    await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _claimedB, _resultB = await _stage3_late_result_setup(maker, provider)
    rA = await persist_stage_success(maker, claimedA, resultA, provider=provider)
    assert rA.outcome == AIStageOutcome.STALE_RESULT_REJECTED


async def test_late_result_writes_no_validated_output(maker):  # (36)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _claimedB, _resultB = await _stage3_late_result_setup(maker, provider)
    await persist_stage_success(maker, claimedA, resultA, provider=provider)
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.validated_output is None  # eski sonuc cikti YAZMAZ


async def test_late_result_does_not_change_totals(maker):  # (37)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _claimedB, _resultB = await _stage3_late_result_setup(maker, provider)
    before = await _get_pipeline(maker, env.pipeline_id)
    tin, tout = before.total_input_tokens, before.total_output_tokens
    await persist_stage_success(maker, claimedA, resultA, provider=provider)
    after = await _get_pipeline(maker, env.pipeline_id)
    assert (after.total_input_tokens, after.total_output_tokens) == (tin, tout)


async def test_late_result_creates_no_successor(maker):  # (38)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    claimedA, resultA, _claimedB, _resultB = await _stage3_late_result_setup(maker, provider)
    await persist_stage_success(maker, claimedA, resultA, provider=provider)
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert behavior == []  # A reddedildi -> successor yok


async def test_attempt2_result_accepted(maker):  # (39)/(40)
    env = await _seed_pipeline(maker)  # 1 persona -> 1 behavior batch
    provider = CountingMockProvider()
    claimedA, resultA, claimedB, resultB = await _stage3_late_result_setup(maker, provider)
    await persist_stage_success(maker, claimedA, resultA, provider=provider)  # reddedilir
    rB = await persist_stage_success(maker, claimedB, resultB, provider=provider)
    assert rB.outcome == AIStageOutcome.SUCCEEDED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.status == AIPipelineStageStatus.SUCCEEDED
    # Successor tam BIR kez olusur (1 persona -> 1 PERSONA_BEHAVIOR).
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert len(behavior) == 1


async def test_duplicate_success_does_not_double_totals(maker):  # (41)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _advance_pure_prefix(maker, provider)
    claimed = await _claim_one(maker)
    assert (await wk._pin_provider_execution(maker, claimed, provider=provider)) is wk._PinResult.OK
    plan = await _prepare(maker, claimed)
    result = await execute_claimed_ai_stage(claimed, plan, provider=provider)
    r1 = await persist_stage_success(maker, claimed, result, provider=provider)
    assert r1.outcome == AIStageOutcome.SUCCEEDED
    after_first = await _get_pipeline(maker, env.pipeline_id)
    tin, tout = after_first.total_input_tokens, after_first.total_output_tokens
    # Ayni claim ile ikinci persist: CAS (status/attempt) uymaz -> reddedilir.
    r2 = await persist_stage_success(maker, claimed, result, provider=provider)
    assert r2.outcome == AIStageOutcome.STALE_RESULT_REJECTED
    after_second = await _get_pipeline(maker, env.pipeline_id)
    assert (after_second.total_input_tokens, after_second.total_output_tokens) == (tin, tout)


# =================================================================================
# END-TO-END (42-50)
# =================================================================================


async def _drive_all(maker, provider, *, max_steps=200):
    outcomes = []
    for _ in range(max_steps):
        r = await process_one_ai_stage(maker, provider=provider)
        outcomes.append(r.outcome)
        if r.outcome == AIStageOutcome.NO_WORK:
            break
    return outcomes


async def test_retryable_then_success_completes_pipeline(maker):  # (42)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=1)
    outcomes = await _drive_all(maker, provider)
    assert AIStageOutcome.RETRY_SCHEDULED in outcomes
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


async def test_two_retryable_errors_terminal_failed(maker):  # (43)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=2)
    await _drive_all(maker, provider)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED


async def test_stale_stage4_batch_reclaimed_and_succeeds(maker):  # (44)
    env = await _seed_pipeline(maker, num_personas=16)  # 2 behavior batch
    provider = CountingMockProvider()
    # Stage 1,2,3'u tamamla (Stage 3 -> iki PERSONA_BEHAVIOR successor).
    await _advance_pure_prefix(maker, provider)
    r3 = await process_one_ai_stage(maker, provider=provider)
    assert r3.outcome == AIStageOutcome.SUCCEEDED
    # Bir PERSONA_BEHAVIOR batch'ini claim et + pinle, calistirma (RUNNING stale).
    claimed = await _claim_one(maker)
    assert claimed.stage_type == AIPipelineStageType.PERSONA_BEHAVIOR
    assert (await wk._pin_provider_execution(maker, claimed, provider=provider)) is wk._PinResult.OK
    assert (await _reap(maker)).requeued == 1
    fresh = await _get_stage(maker, claimed.stage_id)
    assert fresh.status == AIPipelineStageStatus.QUEUED
    # Kalan islerin hepsini sur -> pipeline tamamlanir.
    await _drive_all(maker, provider)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED


async def test_one_stage4_retry_others_succeed_then_fan_in(maker):  # (45)
    env = await _seed_pipeline(maker, num_personas=16)  # 2 batch
    provider = FlakyBatchProvider(fail_batch_index=0)  # batch 0 bir kez basarisiz
    outcomes = await _drive_all(maker, provider)
    assert AIStageOutcome.RETRY_SCHEDULED in outcomes
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED
    # Fan-in: tam BIR aggregation, hepsi tamamlaninca.
    agg = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.AGGREGATION)
    assert len(agg) == 1
    assert agg[0].status == AIPipelineStageStatus.SUCCEEDED


@pytest.mark.parametrize(
    "num_personas,expected_batches",
    [(1, 1), (16, 2), (100, 7)],
)
async def test_persona_counts_dag_unchanged(maker, num_personas, expected_batches):  # (46)
    env = await _seed_pipeline(maker, num_personas=num_personas)
    provider = CountingMockProvider()
    await _drive_all(maker, provider)
    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.SUCCEEDED
    behavior = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.PERSONA_BEHAVIOR)
    assert len(behavior) == expected_batches
    assert all(s.status == AIPipelineStageStatus.SUCCEEDED for s in behavior)


async def test_mock_provider_not_auto_selected(maker):  # (48)
    env = await _seed_pipeline(maker)
    await _advance_pure_prefix(maker, None)
    # Provider gerektiren asama, provider=None ile Mock'a DUSMEZ; kontrollu FAILED.
    r = await process_one_ai_stage(maker, provider=None)
    assert r.outcome == AIStageOutcome.FAILED
    assert r.error_code == ERROR_PROVIDER_NOT_CONFIGURED
    stage3 = await _stage3_queued_row(maker, env.pipeline_id)
    assert stage3.provider is None  # hicbir provider (mock dahil) atanmadi


async def test_failures_write_only_short_error_codes(maker):  # (49)
    env = await _seed_pipeline(maker)
    provider = FlakyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION, fail_times=2)
    await _drive_all(maker, provider)
    # Tum stage error_code'lari kisa/sabit kodlardir (ham mesaj/gövde DEGIL) ve
    # validated_output basarisiz asamada YAZILMAZ.
    all_stages = await _stages(maker, env.pipeline_id)
    for st in all_stages:
        if st.error_code is not None:
            assert len(st.error_code) <= 100
            assert " " not in st.error_code  # kisa slug, cumle degil
        if st.status == AIPipelineStageStatus.FAILED:
            assert st.validated_output is None


async def test_full_success_run_records_no_raw_text(maker):  # (49b)
    env = await _seed_pipeline(maker)
    provider = CountingMockProvider()
    await _drive_all(maker, provider)
    all_stages = await _stages(maker, env.pipeline_id)
    for st in all_stages:
        # Model yalnizca hash/metadata tasir; ham prompt/response alani YOKTUR.
        assert not hasattr(st, "raw_prompt")
        assert not hasattr(st, "raw_response")
