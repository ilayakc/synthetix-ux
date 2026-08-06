"""Faz 3D.3B: `ollama_pipeline_smoke` icin AG-SIZ testler.

Bu dosyada GERCEK bir Ollama daemon'ina/network'e ASLA baglanilmaz - bir
`_FakeProvider` (kismen `MockAIProvider`e delege eden, ama secilen asamalarda
kontrollu sekilde hata/bozuk cikti uretebilen bir test double'i) kullanilir.
Gercek `stage_runner`/`validation`/`aggregation` kodu DEGISTIRILMEDEN, olduğu
gibi calistirilir.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from app.config import settings
from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline import ollama_pipeline_smoke as module
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.ollama_pipeline_smoke import (
    _build_synthetic_personas,
    _print_report,
    main,
    run_pipeline_smoke,
)
from app.services.ai_pipeline.provider import ProviderResult
from app.services.ai_pipeline.provider_errors import AIProviderInvalidOutputError
from app.services.ai_pipeline.schemas import (
    LikelyCompletion,
    PersonaBehaviorBatchOutput,
    PersonaBehaviorEstimate,
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
)

pytestmark = pytest.mark.unit


class _FakeProvider:
    """`AIProvider` protokolunu uygular; varsayilan olarak `MockAIProvider`e
    delege eder, ama secilen asamalarda kontrollu sekilde hata/bozuk cikti
    uretebilir (offline test icin)."""

    provider_name = "fake"
    model_name = "fake-model"
    is_mock = True
    configuration_fingerprint = "fake-v1"

    def __init__(
        self,
        *,
        fail_at: AIPipelineStageType | None = None,
        behavior_index_override: tuple[int, ...] | None = None,
        bad_evidence_reference: bool = False,
    ) -> None:
        self.fail_at = fail_at
        self.behavior_index_override = behavior_index_override
        self.bad_evidence_reference = bad_evidence_reference
        self.call_count = 0
        self._mock = MockAIProvider()

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.call_count += 1
        if self.fail_at is not None and stage_type == self.fail_at:
            raise AIProviderInvalidOutputError("simulated failure for testing")

        if stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION and self.bad_evidence_reference:
            bad_scenario = ScenarioInterpretation(
                steps=(
                    TaskStep(
                        step_id="s1",
                        instruction="x",
                        success_criterion="y",
                        evidence_references=("metric:does_not_exist",),
                    ),
                ),
                success_criteria=("done",),
                limitations="test",
            )
            return ProviderResult(
                output=bad_scenario,
                provider_name=self.provider_name,
                model_name=self.model_name,
                is_mock=True,
                configuration_fingerprint=self.configuration_fingerprint,
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0.0,
                request_duration_ms=1,
            )

        if stage_type is AIPipelineStageType.PERSONA_BEHAVIOR and self.behavior_index_override is not None:
            evidence_id = sorted(input_payload.evidence.evidence_ids())[0]
            results = tuple(
                PersonaBehaviorEstimate(
                    persona_index=idx,
                    likely_completion=LikelyCompletion.MEDIUM,
                    confidence=0.5,
                    evidence_references=(evidence_id,),
                )
                for idx in self.behavior_index_override
            )
            output = PersonaBehaviorBatchOutput(
                batch_index=input_payload.batch.batch_index,
                persona_results=results,
                limitations="test",
            )
            return ProviderResult(
                output=output,
                provider_name=self.provider_name,
                model_name=self.model_name,
                is_mock=True,
                configuration_fingerprint=self.configuration_fingerprint,
                input_tokens=1,
                output_tokens=1,
                estimated_cost=0.0,
                request_duration_ms=1,
            )

        mock_result = await self._mock.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )
        return ProviderResult(
            output=mock_result.output,
            provider_name=self.provider_name,
            model_name=self.model_name,
            is_mock=True,
            configuration_fingerprint=self.configuration_fingerprint,
            input_tokens=mock_result.input_tokens,
            output_tokens=mock_result.output_tokens,
            estimated_cost=0.0,
            request_duration_ms=mock_result.request_duration_ms,
        )


# =================================================================================
# CLI KAPILARI (provider HIC OLUSTURULMAZ, hicbir cagri yapilmaz)
# =================================================================================


def test_main_without_confirm_flag_makes_zero_calls():
    assert main([]) == 2


def test_main_with_wrong_provider_makes_zero_calls(monkeypatch):
    monkeypatch.setattr(settings, "ai_report_enabled", True)
    monkeypatch.setattr(settings, "ai_report_provider", "mock")
    assert main(["--confirm-local-live"]) == 2


def test_main_with_ai_report_disabled_makes_zero_calls(monkeypatch):
    monkeypatch.setattr(settings, "ai_report_enabled", False)
    monkeypatch.setattr(settings, "ai_report_provider", "ollama")
    assert main(["--confirm-local-live"]) == 2


def test_main_with_json_schema_mode_makes_zero_calls(monkeypatch):
    monkeypatch.setattr(settings, "ai_report_enabled", True)
    monkeypatch.setattr(settings, "ai_report_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_structured_output_mode", "json_schema")
    assert main(["--confirm-local-live"]) == 2


# =================================================================================
# PIPELINE AKISI (Fake provider + gercek stage_runner/validation/aggregation)
# =================================================================================


async def test_success_makes_exactly_three_generation_calls():
    provider = _FakeProvider()
    result = await run_pipeline_smoke(provider)

    assert result.success is True
    assert result.generation_calls_made == 3
    assert provider.call_count == 3
    assert result.persona_count == 3
    assert result.population_weight_total == 100
    assert result.behavior_persona_count == 3
    assert result.persona_index_integrity_ok is True
    assert result.aggregation_total_population == 100
    assert result.synthetic_warning_present is True
    assert result.finding_count is not None


async def test_stage3_failure_stops_after_one_call():
    provider = _FakeProvider(fail_at=AIPipelineStageType.SCENARIO_INTERPRETATION)
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "scenario_interpretation"
    assert result.generation_calls_made == 1
    assert provider.call_count == 1


async def test_stage4_failure_stops_after_two_calls():
    provider = _FakeProvider(fail_at=AIPipelineStageType.PERSONA_BEHAVIOR)
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "persona_behavior"
    assert result.generation_calls_made == 2
    assert provider.call_count == 2


async def test_stage6_failure_stops_after_three_calls():
    provider = _FakeProvider(fail_at=AIPipelineStageType.UX_REPORT)
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "ux_report"
    assert result.generation_calls_made == 3
    assert provider.call_count == 3


async def test_aggregation_failure_prevents_stage6_call():
    """Stage 5'in KENDI (Stage 4'ten BAGIMSIZ) butunluk kontrolunu tetiklemek
    icin, aggregation'a Stage 4'un batch'inden FARKLI (eksik) bir persona
    kumesi verilir - bu, yalnizca bu testte kullanilan bir escape hatch'tir;
    gercek CLI (`main`) bu parametreyi ASLA gecmez."""

    personas = _build_synthetic_personas()
    mismatched_aggregation_personas = personas[:2]  # index=2 eksik

    provider = _FakeProvider()
    result = await run_pipeline_smoke(provider, aggregation_personas=mismatched_aggregation_personas)

    assert result.success is False
    assert result.failed_stage == "aggregation"
    # Stage 3 + Stage 4 cagrildi (2); Stage 6 (UX report) CAGRILMADI.
    assert result.generation_calls_made == 2
    assert provider.call_count == 2


async def test_missing_persona_index_in_behavior_output_is_rejected():
    provider = _FakeProvider(behavior_index_override=(0, 1))  # index=2 eksik
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "persona_behavior"
    assert result.generation_calls_made == 2


async def test_extra_persona_index_in_behavior_output_is_rejected():
    provider = _FakeProvider(behavior_index_override=(0, 1, 2, 3))  # index=3 fazladan
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "persona_behavior"
    assert result.generation_calls_made == 2


async def test_unknown_evidence_reference_in_scenario_is_rejected():
    provider = _FakeProvider(bad_evidence_reference=True)
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "scenario_interpretation"
    assert result.generation_calls_made == 1


async def test_synthetic_warning_missing_is_rejected():
    class _NoDisclaimerProvider(_FakeProvider):
        async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
            base_result = await super().generate_structured(
                stage_type=stage_type,
                batch_index=batch_index,
                prompt=prompt,
                input_payload=input_payload,
                output_schema=output_schema,
            )
            if stage_type is not AIPipelineStageType.UX_REPORT:
                return base_result

            stripped_output = base_result.output.model_copy(
                update={"disclaimer": "Rapor tamamlandi.", "limitations": "Yok."}
            )
            return ProviderResult(
                output=stripped_output,
                provider_name=base_result.provider_name,
                model_name=base_result.model_name,
                is_mock=True,
                configuration_fingerprint=base_result.configuration_fingerprint,
                input_tokens=base_result.input_tokens,
                output_tokens=base_result.output_tokens,
                estimated_cost=0.0,
                request_duration_ms=base_result.request_duration_ms,
            )

    provider = _NoDisclaimerProvider()
    result = await run_pipeline_smoke(provider)

    assert result.success is False
    assert result.failed_stage == "ux_report"
    assert result.failed_error_code == "synthetic_warning_missing"
    assert result.synthetic_warning_present is False
    # Cagrinin kendisi BASARILI oldu (provider hata firlatmadi) - yine de tam 3.
    assert result.generation_calls_made == 3


async def test_population_weight_total_reflects_actual_fixture_sum():
    custom_personas = (
        PersonaContext(persona_id=uuid.uuid4(), index=0, label="a", attributes={}, population_weight=60),
        PersonaContext(persona_id=uuid.uuid4(), index=1, label="b", attributes={}, population_weight=30),
    )
    provider = _FakeProvider(fail_at=AIPipelineStageType.SCENARIO_INTERPRETATION)
    result = await run_pipeline_smoke(provider, personas=custom_personas)

    assert result.population_weight_total == 90
    assert result.persona_count == 2


# =================================================================================
# GUVENLI RAPORLAMA (ham icerik/persona etiketi sizmaz)
# =================================================================================


def test_report_output_never_leaks_persona_labels_or_attributes(capsys):
    async def _go():
        provider = _FakeProvider()
        result = await run_pipeline_smoke(provider)
        _print_report(result, provider)  # type: ignore[arg-type]

    asyncio.run(_go())
    captured = capsys.readouterr().out
    for forbidden in ("persona-0", "persona-1", "persona-2", "marmara", "ic_anadolu", "akdeniz"):
        assert forbidden not in captured


def test_no_db_redis_or_arq_side_effects_in_module_source():
    source = inspect.getsource(module)
    for forbidden in ("app.db", "import redis", "import arq", "session.add(", "session.commit("):
        assert forbidden not in source
