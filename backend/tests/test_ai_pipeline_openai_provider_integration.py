"""Faz 3D.1: DB-destekli, provider configuration fingerprint pin/mismatch testi.

`app.services.ai_pipeline.worker`in provider/model pinlemesine, Faz 3D.1'de
eklenen `configuration_fingerprint`i de kapsayacak sekilde genisletildigini
dogrular (bkz. tests/test_ai_pipeline_retry_stale.py'deki RenamedProvider
desenleriyle AYNI yaklasim). Gercek OpenAI API cagrisi YAPILMAZ.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.ai_pipeline import AIPipelineStageType, AIPipelineStatus
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.provider_errors import AIProviderTransportError
from app.services.ai_pipeline.worker import AIStageOutcome, process_one_ai_stage
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables
from tests.test_ai_pipeline_worker import _get_pipeline, _seed_pipeline

pytestmark = pytest.mark.integration


class _FingerprintOverrideProvider:
    """`MockAIProvider`i saran, YALNIZCA `configuration_fingerprint`i degistiren
    test wrapper'i - provider/model AYNI kalir, semantik fingerprint FARKLI
    (retry sirasinda pin uyusmazligi tetiklemesi beklenir). Istege bagli olarak
    ilk cagriyi (fail_first=True) GECICI bir hata ile reddeder - boylece pin
    korunurken stage QUEUED'a doner ve bir SONRAKI claim gercek bir "retry"
    olur (mismatch, yalnizca zaten pinlenmis bir satiri tekrar claim ederken
    anlamlidir)."""

    is_mock = False

    def __init__(self, *, fingerprint: str, fail_first: bool = False):
        self._inner = MockAIProvider()
        self.provider_name = self._inner.provider_name
        self.model_name = self._inner.model_name
        self.configuration_fingerprint = fingerprint
        self._fail_first = fail_first
        self.calls: list[tuple] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        if self._fail_first:
            self._fail_first = False
            raise AIProviderTransportError("gecici tasima hatasi")
        result = await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )
        return result.model_copy(update={"configuration_fingerprint": self.configuration_fingerprint})


@pytest.fixture
async def maker(test_engine):
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


async def test_retry_with_different_configuration_fingerprint_rejected_before_call(maker):
    env = await _seed_pipeline(maker)
    first = _FingerprintOverrideProvider(fingerprint="fingerprint-v1", fail_first=True)
    r1 = await process_one_ai_stage(maker, provider=first)
    assert r1.outcome == AIStageOutcome.SUCCEEDED  # Stage 1 (saf) - fingerprint kullanilmaz
    r2 = await process_one_ai_stage(maker, provider=first)
    assert r2.outcome == AIStageOutcome.SUCCEEDED  # Stage 2 (saf)
    r3 = await process_one_ai_stage(maker, provider=first)
    # Stage 3 (provider) - ilk cagri GECICI hatayla basarisiz olur; pin
    # ("fingerprint-v1") KORUNARAK stage QUEUED'a doner.
    assert r3.outcome == AIStageOutcome.RETRY_SCHEDULED
    assert r3.stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION

    second = _FingerprintOverrideProvider(fingerprint="fingerprint-v2-different")
    r4 = await process_one_ai_stage(maker, provider=second)
    assert r4.outcome == AIStageOutcome.PROVIDER_CONFIGURATION_MISMATCH
    assert second.calls == []  # provider CAGRILMADI

    pipeline = await _get_pipeline(maker, env.pipeline_id)
    assert pipeline.status == AIPipelineStatus.FAILED
