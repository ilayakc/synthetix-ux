"""Faz 3D.2.1: DB-destekli, provider timeout -> retry/CAS/lease testi.

Bir provider cagrisi `AIProviderTimeoutError` (retryable) ile basarisiz
oldugunda, stage'in DB'de sonsuza kadar RUNNING durumunda kalan bir "orphan"
olarak KALMADIGINI, bunun yerine mevcut retry/CAS/lease sisteminin (bkz.
app.services.ai_pipeline.worker) onu QUEUED'a geri dondurdugunu dogrular.
Gercek Ollama/OpenAI API cagrisi YAPILMAZ."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.ai_pipeline import AIPipelineStageStatus, AIPipelineStageType
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.provider_errors import AIProviderTimeoutError
from app.services.ai_pipeline.worker import AIStageOutcome, process_one_ai_stage
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables
from tests.test_ai_pipeline_worker import _seed_pipeline, _stages

pytestmark = pytest.mark.integration


class _TimeoutOnceProvider:
    """Ilk `generate_structured` cagrisinda `AIProviderTimeoutError` firlatan,
    sonraki cagrilarda `MockAIProvider`e delege eden test wrapper'i - gercek
    bir `OllamaProvider`in timeout davranisini (retryable, DB'ye hicbir ham
    hata detayi yazilmadan) simule eder."""

    is_mock = False

    def __init__(self) -> None:
        self._inner = MockAIProvider()
        self.provider_name = self._inner.provider_name
        self.model_name = self._inner.model_name
        self.configuration_fingerprint = self._inner.configuration_fingerprint
        self._raised = False
        self.calls = 0

    async def generate_structured(self, **kwargs):
        self.calls += 1
        if not self._raised:
            self._raised = True
            raise AIProviderTimeoutError("ollama istegi zaman asimina ugradi")
        return await self._inner.generate_structured(**kwargs)


@pytest.fixture
async def maker(test_engine):
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


async def test_provider_timeout_does_not_orphan_running_stage(maker):
    env = await _seed_pipeline(maker)
    provider = _TimeoutOnceProvider()

    r1 = await process_one_ai_stage(maker, provider=provider)  # stage 1 (saf)
    assert r1.outcome == AIStageOutcome.SUCCEEDED
    r2 = await process_one_ai_stage(maker, provider=provider)  # stage 2 (saf)
    assert r2.outcome == AIStageOutcome.SUCCEEDED

    r3 = await process_one_ai_stage(maker, provider=provider)  # stage 3 - timeout
    assert r3.outcome == AIStageOutcome.RETRY_SCHEDULED
    assert r3.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION

    stages = await _stages(maker, env.pipeline_id, stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION)
    assert len(stages) == 1
    # Stage RUNNING'de "orphan" olarak KALMADI - retry politikasi onu QUEUED'a
    # geri dondurdu (bkz. app.services.ai_pipeline.worker.persist_stage_retry).
    assert stages[0].status == AIPipelineStageStatus.QUEUED
    assert stages[0].attempt_count == 1
    # Yalnizca sanitize/sabit `error_code` saklanir - ham "zaman asimina
    # ugradi" hata METNI (provider'in exception mesaji) DB'ye ASLA yazilmaz.
    assert stages[0].error_code == "provider_timeout"

    r4 = await process_one_ai_stage(maker, provider=provider)  # retry basarili
    assert r4.outcome == AIStageOutcome.SUCCEEDED
    assert provider.calls == 2
