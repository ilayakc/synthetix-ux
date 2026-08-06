"""Faz 2B: AIProvider sozlesmesi + ProviderResult + stage-contract testleri."""

import pytest
from pydantic import ValidationError

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.provider import (
    AIProvider,
    ProviderResult,
    validate_stage_contract,
)
from app.services.ai_pipeline.provider_errors import (
    AIProviderError,
    AIProviderInvalidOutputError,
    AIProviderUnsupportedStageError,
)
from app.services.ai_pipeline.schemas import (
    PersonaBehaviorBatchOutput,
    ScenarioInterpretation,
    UXReport,
)
from app.services.ai_pipeline.stage_inputs import ScenarioInterpretationInput

pytestmark = pytest.mark.unit

_FORBIDDEN_FIELD_NAMES = {
    "system_instructions",
    "raw_response",
    "raw_prompt",
    "prompt",
    "request_body",
    "api_key",
    "headers",
    "html",
    "dom",
    "stack_trace",
}


def _evidence():
    return prepare_page_evidence(
        source_type="sim",
        metrics={"task_completion_probability": {"point_estimate": 0.7}},
    )


def _scenario_input():
    return ScenarioInterpretationInput(
        evidence=_evidence(),
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )


def test_mock_provider_identity():
    provider = MockAIProvider()
    assert provider.provider_name == "mock"
    assert provider.model_name == "deterministic-template-v1"
    assert provider.is_mock is True
    assert isinstance(provider, AIProvider)


def test_validate_stage_contract_rejects_unsupported_stage():
    with pytest.raises(AIProviderUnsupportedStageError):
        validate_stage_contract(
            stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )


def test_validate_stage_contract_rejects_wrong_input_type():
    with pytest.raises(AIProviderError) as exc_info:
        validate_stage_contract(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            input_payload=PersonaBehaviorBatchOutput.model_construct(),
            output_schema=ScenarioInterpretation,
        )
    assert exc_info.value.error_code == "invalid_input_payload"


def test_validate_stage_contract_rejects_wrong_output_schema():
    with pytest.raises(AIProviderInvalidOutputError):
        validate_stage_contract(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            input_payload=_scenario_input(),
            output_schema=UXReport,
        )


def test_provider_result_carries_no_raw_fields():
    field_names = set(ProviderResult.model_fields)
    assert not (field_names & _FORBIDDEN_FIELD_NAMES)


def test_provider_result_rejects_negative_tokens():
    scenario = ScenarioInterpretation.model_construct()
    with pytest.raises(ValidationError):
        ProviderResult(
            output=scenario,
            provider_name="mock",
            model_name="m",
            is_mock=True,
            input_tokens=-1,
            output_tokens=0,
            request_duration_ms=0,
        )


def test_provider_result_rejects_negative_cost():
    scenario = ScenarioInterpretation.model_construct()
    with pytest.raises(ValidationError):
        ProviderResult(
            output=scenario,
            provider_name="mock",
            model_name="m",
            is_mock=True,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=-0.5,
            request_duration_ms=0,
        )


async def test_mock_provider_rejects_unsupported_stage_via_generate():
    provider = MockAIProvider()
    with pytest.raises(AIProviderUnsupportedStageError):
        await provider.generate_structured(
            stage_type=AIPipelineStageType.AGGREGATION,
            batch_index=None,
            prompt=object(),  # type: ignore[arg-type]
            input_payload=_scenario_input(),
            output_schema=ScenarioInterpretation,
        )


async def test_mock_provider_rejects_wrong_output_schema_via_generate():
    provider = MockAIProvider()
    with pytest.raises(AIProviderInvalidOutputError):
        await provider.generate_structured(
            stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
            batch_index=None,
            prompt=object(),  # type: ignore[arg-type]
            input_payload=_scenario_input(),
            output_schema=UXReport,
        )
