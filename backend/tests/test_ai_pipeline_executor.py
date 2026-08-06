"""Faz 2B: pure pipeline executor (cagri sayisi/sira/determinizm/hata) testleri."""

import inspect
import random
import uuid

import pytest

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline import executor as executor_module
from app.services.ai_pipeline.executor import (
    AIPipelineExecutionInput,
    AIPipelineExecutionResult,
    PipelineStageError,
    StageAudit,
    execute_pipeline,
)
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.provider import ProviderResult
from app.services.ai_pipeline.provider_errors import AIProviderTransportError
from app.services.ai_pipeline.schemas import (
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
)

pytestmark = pytest.mark.unit


def _personas(n):
    return tuple(
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=i,
            label=f"P{i}",
            attributes={"accessibility_need": "visual"},
            population_weight=10,
        )
        for i in range(n)
    )


def _execution_input(n=5, personas=None, metrics=None):
    return AIPipelineExecutionInput(
        simulation_run_id=uuid.UUID(int=1),
        source_type="sim",
        metrics=metrics
        or {
            "task_completion_probability": {"point_estimate": 0.7},
            "abandonment_probability": {"point_estimate": 0.2},
            "contrast_check": {"pass": True, "avg_ratio": 4.5},
        },
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
        personas=personas if personas is not None else _personas(n),
    )


class CountingMockProvider:
    """`MockAIProvider`i saran, cagri sayisi/sirasini kaydeden test wrapper'i."""

    def __init__(self, *, model_name=None):
        self._inner = MockAIProvider()
        self.provider_name = self._inner.provider_name
        self.model_name = model_name or self._inner.model_name
        self.is_mock = self._inner.is_mock
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
        # model_name override'i audit'e yansisin.
        return result.model_copy(update={"model_name": self.model_name})


class FaultyProvider:
    """Belirli bir stage/batch'te hata firlatan test provider'i."""

    provider_name = "faulty"
    model_name = "faulty-v1"
    is_mock = False

    def __init__(self, *, fail_stage, fail_batch_index=None):
        self._inner = MockAIProvider()
        self._fail_stage = fail_stage
        self._fail_batch_index = fail_batch_index
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        if stage_type is self._fail_stage and (
            self._fail_batch_index is None or batch_index == self._fail_batch_index
        ):
            raise AIProviderTransportError("test tasima hatasi")
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


# --- Cagri sayisi / sira ---------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "expected_calls", "expected_batches"),
    [(1, 3, 1), (15, 3, 1), (16, 4, 2), (100, 9, 7)],
)
async def test_call_count_and_order(n, expected_calls, expected_batches):
    provider = CountingMockProvider()
    result = await execute_pipeline(_execution_input(n), provider=provider)

    assert len(provider.calls) == expected_calls
    stage_types = {c[0] for c in provider.calls}
    assert stage_types == {
        AIPipelineStageType.SCENARIO_INTERPRETATION,
        AIPipelineStageType.PERSONA_BEHAVIOR,
        AIPipelineStageType.UX_REPORT,
    }
    # Sira: scenario -> tum persona_behavior (batch_index ASC) -> ux_report.
    assert provider.calls[0] == (AIPipelineStageType.SCENARIO_INTERPRETATION, None)
    assert provider.calls[-1] == (AIPipelineStageType.UX_REPORT, None)
    behavior_calls = [c for c in provider.calls if c[0] is AIPipelineStageType.PERSONA_BEHAVIOR]
    assert [c[1] for c in behavior_calls] == list(range(expected_batches))
    assert len(result.batches) == expected_batches


async def test_all_stage_audits_present_and_idempotency_unique():
    result = await execute_pipeline(_execution_input(16), provider=MockAIProvider())
    stage_seq = [(a.stage_type, a.batch_index) for a in result.stage_audits]
    assert stage_seq == [
        (AIPipelineStageType.EVIDENCE_PREPARATION, None),
        (AIPipelineStageType.PERSONA_BATCH_PREPARATION, None),
        (AIPipelineStageType.SCENARIO_INTERPRETATION, None),
        (AIPipelineStageType.PERSONA_BEHAVIOR, 0),
        (AIPipelineStageType.PERSONA_BEHAVIOR, 1),
        (AIPipelineStageType.AGGREGATION, None),
        (AIPipelineStageType.UX_REPORT, None),
    ]
    keys = [a.idempotency_key for a in result.stage_audits]
    assert len(keys) == len(set(keys))
    assert all(a.error_code is None and a.status == "succeeded" for a in result.stage_audits)


# --- Determinizm / sira bagimsizligi ---------------------------------------------


async def test_same_input_same_output_hash():
    ei = _execution_input(16)
    a = await execute_pipeline(ei, provider=MockAIProvider())
    b = await execute_pipeline(ei, provider=MockAIProvider())
    assert a.pipeline_output_hash == b.pipeline_output_hash
    assert a.pipeline_input_hash == b.pipeline_input_hash


async def test_persona_input_order_does_not_change_output_hash():
    personas = _personas(20)
    base = await execute_pipeline(_execution_input(personas=personas), provider=MockAIProvider())

    shuffled = list(personas)
    random.Random(7).shuffle(shuffled)
    other = await execute_pipeline(_execution_input(personas=tuple(shuffled)), provider=MockAIProvider())
    assert base.pipeline_output_hash == other.pipeline_output_hash


async def test_model_name_change_affects_only_provider_stage_audits():
    ei = _execution_input(16)
    base = await execute_pipeline(ei, provider=CountingMockProvider())
    changed = await execute_pipeline(ei, provider=CountingMockProvider(model_name="other-model-v9"))

    pure_stages = {
        AIPipelineStageType.EVIDENCE_PREPARATION,
        AIPipelineStageType.PERSONA_BATCH_PREPARATION,
        AIPipelineStageType.AGGREGATION,
    }
    base_by = {(a.stage_type, a.batch_index): a for a in base.stage_audits}
    changed_by = {(a.stage_type, a.batch_index): a for a in changed.stage_audits}

    for key, base_audit in base_by.items():
        changed_audit = changed_by[key]
        if base_audit.stage_type in pure_stages:
            assert base_audit.idempotency_key == changed_audit.idempotency_key
            assert base_audit.model_name == changed_audit.model_name
        else:
            assert base_audit.model_name != changed_audit.model_name
            assert base_audit.idempotency_key != changed_audit.idempotency_key


async def test_metric_change_affects_pipeline_hashes():
    personas = _personas(16)
    a = await execute_pipeline(_execution_input(personas=personas), provider=MockAIProvider())
    b = await execute_pipeline(
        _execution_input(
            personas=personas,
            metrics={
                "task_completion_probability": {"point_estimate": 0.99},
                "abandonment_probability": {"point_estimate": 0.2},
                "contrast_check": {"pass": True, "avg_ratio": 4.5},
            },
        ),
        provider=MockAIProvider(),
    )
    assert a.pipeline_input_hash != b.pipeline_input_hash
    assert a.pipeline_output_hash != b.pipeline_output_hash


async def test_token_and_cost_sums_match_stage_audits():
    result = await execute_pipeline(_execution_input(16), provider=MockAIProvider())
    assert result.total_input_tokens == sum(a.input_tokens for a in result.stage_audits)
    assert result.total_output_tokens == sum(a.output_tokens for a in result.stage_audits)
    contributions = [a.estimated_cost for a in result.stage_audits if a.estimated_cost is not None]
    assert result.total_estimated_cost == (sum(contributions) if contributions else None)


def test_result_has_no_raw_prompt_or_response_fields():
    forbidden = {"system_instructions", "raw_response", "raw_prompt", "request_body", "prompt"}
    assert not (set(AIPipelineExecutionResult.model_fields) & forbidden)
    assert not (set(StageAudit.model_fields) & forbidden)


def test_executor_source_has_no_auto_mock_fallback():
    source = inspect.getsource(executor_module)
    assert "MockAIProvider(" not in source
    for forbidden in ("httpx", "requests", "import socket", "environ"):
        assert forbidden not in source


# --- Hata davranisi --------------------------------------------------------------


async def test_scenario_failure_stops_pipeline():
    provider = FaultyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION)
    with pytest.raises(PipelineStageError) as exc_info:
        await execute_pipeline(_execution_input(16), provider=provider)
    assert exc_info.value.stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION
    assert exc_info.value.error_code == "provider_transport_error"
    # persona_behavior / ux_report cagrilmadi.
    later = {AIPipelineStageType.PERSONA_BEHAVIOR, AIPipelineStageType.UX_REPORT}
    assert not any(c[0] in later for c in provider.calls)


async def test_specific_batch_failure_stops_later_stages():
    provider = FaultyProvider(fail_stage=AIPipelineStageType.PERSONA_BEHAVIOR, fail_batch_index=1)
    with pytest.raises(PipelineStageError) as exc_info:
        await execute_pipeline(_execution_input(16), provider=provider)
    assert exc_info.value.stage_type is AIPipelineStageType.PERSONA_BEHAVIOR
    assert exc_info.value.batch_index == 1
    # batch 2+ ve ux_report cagrilmadi.
    behavior_batches = [c[1] for c in provider.calls if c[0] is AIPipelineStageType.PERSONA_BEHAVIOR]
    assert max(behavior_batches) == 1
    assert not any(c[0] is AIPipelineStageType.UX_REPORT for c in provider.calls)


async def test_ux_report_failure_raises_no_partial_result():
    provider = FaultyProvider(fail_stage=AIPipelineStageType.UX_REPORT)
    with pytest.raises(PipelineStageError) as exc_info:
        await execute_pipeline(_execution_input(16), provider=provider)
    assert exc_info.value.stage_type is AIPipelineStageType.UX_REPORT


class BrokenScenarioProvider:
    """Kendi ic dogrulamasini ATLAYIP, sema-gecerli ama evidence-referansi
    GECERSIZ bir ScenarioInterpretation donduren test double'i. Executor'in
    kendi bagimsiz re-validasyonunun bunu yakalamasi beklenir."""

    provider_name = "broken"
    model_name = "broken-v1"
    is_mock = False

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        broken = ScenarioInterpretation(
            steps=(
                TaskStep(
                    step_id="step:1",
                    instruction="x",
                    success_criterion="y",
                    evidence_references=("metric:does_not_exist",),
                ),
            ),
            success_criteria=("z",),
            friction_hypotheses=(),
            limitations="ok",
        )
        return ProviderResult(
            output=broken,
            provider_name=self.provider_name,
            model_name=self.model_name,
            is_mock=False,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
            request_duration_ms=0,
            provider_request_id=None,
        )


async def test_executor_revalidates_broken_provider_output():
    provider = BrokenScenarioProvider()
    with pytest.raises(PipelineStageError) as exc_info:
        await execute_pipeline(_execution_input(5), provider=provider)
    assert exc_info.value.stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION
    assert exc_info.value.error_code == "invalid_evidence_reference"
