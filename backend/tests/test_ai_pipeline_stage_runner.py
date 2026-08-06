"""Faz 3B.1: tekil stage runner fonksiyonlari + execute_pipeline delegasyonu testleri.

Her stage runner'in BAGIMSIZ cagrilabildigini, provider cagri sayilarini,
tek-batch semantigini, hata baglamini ve `execute_pipeline`in bu fonksiyonlari
GERCEKTEN cagirdigini (paralel bir reimplementasyon DEGIL) dogrular.
"""

import inspect
import uuid

import pytest

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline import executor as executor_module
from app.services.ai_pipeline import stage_runner as stage_runner_module
from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.executor import (
    AIPipelineExecutionInput,
    execute_pipeline,
)
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.provider_errors import AIProviderTransportError
from app.services.ai_pipeline.schemas import (
    AggregationResult,
    CompletionBucketStat,
    CompletionDistribution,
    FrictionHypothesis,
    PageEvidence,
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
    WeightedIssue,
)
from app.services.ai_pipeline.stage_runner import (
    PipelineStageError,
    StageAudit,
    StageRunResult,
    run_aggregation_stage,
    run_batching_stage,
    run_evidence_stage,
    run_persona_behavior_batch,
    run_scenario_stage,
    run_ux_report_stage,
)

pytestmark = pytest.mark.unit

_RUN_ID = uuid.UUID(int=1)

_METRICS: dict[str, object] = {
    "task_completion_probability": {"point_estimate": 0.7},
    "abandonment_probability": {"point_estimate": 0.2},
    "contrast_check": {"pass": True, "avg_ratio": 4.5},
}


# --- Fixtures / yardimcilar ------------------------------------------------------


def _personas(n, attrs=None):
    return tuple(
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=i,
            label=f"P{i}",
            attributes=attrs or {"accessibility_need": "visual"},
            population_weight=10,
        )
        for i in range(n)
    )


def _execution_input(n=5, personas=None, metrics=None):
    return AIPipelineExecutionInput(
        simulation_run_id=_RUN_ID,
        source_type="sim",
        metrics=metrics or dict(_METRICS),
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
        personas=personas if personas is not None else _personas(n),
    )


def _evidence() -> PageEvidence:
    return prepare_page_evidence(source_type="sim", metrics=dict(_METRICS))


def _scenario() -> ScenarioInterpretation:
    return ScenarioInterpretation(
        steps=(
            TaskStep(
                step_id="step:1",
                instruction="a",
                success_criterion="a",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        success_criteria=("a",),
        friction_hypotheses=(
            FrictionHypothesis(
                hypothesis_id="hyp:1",
                description="desc",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="ok",
    )


def _aggregation(issues=()) -> AggregationResult:
    dist = CompletionDistribution(
        high=CompletionBucketStat(users=50, share=0.5),
        medium=CompletionBucketStat(users=30, share=0.3),
        low=CompletionBucketStat(users=20, share=0.2),
    )
    return AggregationResult(
        total_population=100,
        completion_distribution=dist,
        common_issues=issues,
        overall_confidence=0.5,
        disclaimer="d" * 10,
        aggregation_hash="0" * 64,
    )


class CountingProvider:
    """`MockAIProvider`i saran, cagri (stage/batch) kaydeden test wrapper'i."""

    def __init__(self):
        self._inner = MockAIProvider()
        self.provider_name = self._inner.provider_name
        self.model_name = self._inner.model_name
        self.is_mock = self._inner.is_mock
        self.calls: list[tuple[AIPipelineStageType, int | None]] = []

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
        self.calls.append((stage_type, batch_index))
        return await self._inner.generate_structured(
            stage_type=stage_type,
            batch_index=batch_index,
            prompt=prompt,
            input_payload=input_payload,
            output_schema=output_schema,
        )


class FaultyProvider:
    """Belirli bir stage/batch'te AIProviderTransportError firlatan provider."""

    provider_name = "faulty"
    model_name = "faulty-v1"
    is_mock = False

    def __init__(self, *, fail_stage, fail_batch_index=None):
        self._inner = MockAIProvider()
        self._fail_stage = fail_stage
        self._fail_batch_index = fail_batch_index

    async def generate_structured(self, *, stage_type, batch_index, prompt, input_payload, output_schema):
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


# --- 1. Her runner bagimsiz cagrilabilir + StageRunResult sekli ------------------


def test_run_evidence_stage_standalone():
    result = run_evidence_stage(
        simulation_run_id=_RUN_ID,
        source_type="sim",
        metrics=dict(_METRICS),
        page_features=None,
        selected_modules=(),
        module_results=None,
    )
    assert isinstance(result, StageRunResult)
    assert isinstance(result.output, PageEvidence)
    assert isinstance(result.audit, StageAudit)
    assert result.audit.stage_type is AIPipelineStageType.EVIDENCE_PREPARATION
    assert result.audit.provider == "deterministic"


def test_run_batching_stage_standalone():
    result = run_batching_stage(
        simulation_run_id=_RUN_ID,
        personas=_personas(16),
        evidence_output_hash="a" * 64,
    )
    assert len(result.output) == 2
    assert [b.batch_index for b in result.output] == [0, 1]
    assert result.audit.stage_type is AIPipelineStageType.PERSONA_BATCH_PREPARATION


async def test_run_scenario_stage_standalone():
    provider = CountingProvider()
    result = await run_scenario_stage(
        simulation_run_id=_RUN_ID,
        provider=provider,
        evidence=_evidence(),
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )
    assert isinstance(result.output, ScenarioInterpretation)
    assert result.audit.stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION
    assert result.audit.prompt_key == "scenario_interpretation"


async def test_run_persona_behavior_batch_standalone():
    provider = CountingProvider()
    batch = build_persona_batches(_personas(5), batch_size=15)[0]
    result = await run_persona_behavior_batch(
        simulation_run_id=_RUN_ID,
        provider=provider,
        batch=batch,
        evidence=_evidence(),
        scenario=_scenario(),
        baseline_metrics=(),
    )
    assert result.output.batch_index == batch.batch_index
    assert len(result.output.persona_results) == 5


async def test_run_aggregation_stage_standalone():
    provider = CountingProvider()
    # Once Stage 4'u calistirip gercek behavior ciktisi uretelim (bagimsizca).
    personas = _personas(5)
    batch = build_persona_batches(personas, batch_size=15)[0]
    behavior = await run_persona_behavior_batch(
        simulation_run_id=_RUN_ID,
        provider=provider,
        batch=batch,
        evidence=_evidence(),
        scenario=_scenario(),
        baseline_metrics=(),
    )
    result = run_aggregation_stage(
        simulation_run_id=_RUN_ID,
        personas=personas,
        behavior_outputs=(behavior.output,),
        scenario=_scenario(),
    )
    assert isinstance(result.output, AggregationResult)
    assert result.audit.stage_type is AIPipelineStageType.AGGREGATION


async def test_run_ux_report_stage_standalone():
    provider = CountingProvider()
    result = await run_ux_report_stage(
        simulation_run_id=_RUN_ID,
        provider=provider,
        evidence=_evidence(),
        baseline_metrics=(),
        aggregation=_aggregation(),
        module_summary=(),
        methodology_context="m",
    )
    from app.services.ai_pipeline.schemas import UXReport

    assert isinstance(result.output, UXReport)
    assert result.audit.stage_type is AIPipelineStageType.UX_REPORT


# --- 2-7. Provider cagri sayilari (asama basina) ---------------------------------


def test_evidence_stage_signature_takes_no_provider():
    # Stage 1 provider parametresi KABUL ETMEZ -> sifir provider cagrisi.
    sig = inspect.signature(run_evidence_stage)
    assert "provider" not in sig.parameters


def test_batching_stage_signature_takes_no_provider():
    sig = inspect.signature(run_batching_stage)
    assert "provider" not in sig.parameters


def test_aggregation_stage_signature_takes_no_provider():
    sig = inspect.signature(run_aggregation_stage)
    assert "provider" not in sig.parameters


async def test_scenario_stage_makes_exactly_one_provider_call():
    provider = CountingProvider()
    await run_scenario_stage(
        simulation_run_id=_RUN_ID,
        provider=provider,
        evidence=_evidence(),
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )
    assert len(provider.calls) == 1
    assert provider.calls[0] == (AIPipelineStageType.SCENARIO_INTERPRETATION, None)


async def test_persona_behavior_batch_processes_one_batch_one_call():
    provider = CountingProvider()
    batches = build_persona_batches(_personas(30), batch_size=15)
    assert len(batches) == 2
    await run_persona_behavior_batch(
        simulation_run_id=_RUN_ID,
        provider=provider,
        batch=batches[1],
        evidence=_evidence(),
        scenario=_scenario(),
        baseline_metrics=(),
    )
    # Tek batch -> tek cagri, dogru batch_index ile.
    assert len(provider.calls) == 1
    assert provider.calls[0] == (AIPipelineStageType.PERSONA_BEHAVIOR, 1)


async def test_ux_report_stage_makes_exactly_one_provider_call():
    provider = CountingProvider()
    await run_ux_report_stage(
        simulation_run_id=_RUN_ID,
        provider=provider,
        evidence=_evidence(),
        baseline_metrics=(),
        aggregation=_aggregation(),
        module_summary=(),
        methodology_context="m",
    )
    assert len(provider.calls) == 1
    assert provider.calls[0] == (AIPipelineStageType.UX_REPORT, None)


# --- 8-9. execute_pipeline sira + cagri sayisi garantileri ------------------------


async def test_execute_pipeline_stage_ordering():
    provider = CountingProvider()
    result = await execute_pipeline(_execution_input(16), provider=provider)
    # Provider cagri sirasi: scenario -> behavior(0,1 ASC) -> ux_report.
    assert provider.calls == [
        (AIPipelineStageType.SCENARIO_INTERPRETATION, None),
        (AIPipelineStageType.PERSONA_BEHAVIOR, 0),
        (AIPipelineStageType.PERSONA_BEHAVIOR, 1),
        (AIPipelineStageType.UX_REPORT, None),
    ]
    # Audit sirasi: evidence/batching once, sonra scenario/behavior/agg/report.
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


@pytest.mark.parametrize(
    ("n", "expected_calls", "expected_batches"),
    [(1, 3, 1), (15, 3, 1), (16, 4, 2), (100, 9, 7)],
)
async def test_execute_pipeline_provider_call_counts(n, expected_calls, expected_batches):
    provider = CountingProvider()
    result = await execute_pipeline(_execution_input(n), provider=provider)
    assert len(provider.calls) == expected_calls
    assert len(result.batches) == expected_batches
    behavior_calls = [c for c in provider.calls if c[0] is AIPipelineStageType.PERSONA_BEHAVIOR]
    assert [c[1] for c in behavior_calls] == list(range(expected_batches))


# --- 10-11. Determinizm / hash-idempotency stabilitesi ---------------------------


async def test_execute_pipeline_twice_identical_result_and_audits():
    ei = _execution_input(16)
    a = await execute_pipeline(ei, provider=MockAIProvider())
    b = await execute_pipeline(ei, provider=MockAIProvider())
    assert a.pipeline_output_hash == b.pipeline_output_hash
    assert a.pipeline_input_hash == b.pipeline_input_hash
    # Audit kayitlari (idempotency_key/input_hash/output_hash dahil) birebir esit.
    a_audits = [x.model_dump(mode="json") for x in a.stage_audits]
    b_audits = [x.model_dump(mode="json") for x in b.stage_audits]
    for xa, xb in zip(a_audits, b_audits, strict=True):
        for key in ("input_hash", "output_hash", "idempotency_key", "prompt_hash"):
            assert xa[key] == xb[key]


async def test_runner_audit_hashes_match_inline_pipeline_values():
    # Bir runner'i dogrudan cagirip uretilen hash/idempotency_key'in,
    # execute_pipeline icindeki ayni asamanin audit'i ile ayni oldugunu dogrula.
    provider_a = CountingProvider()
    pipeline = await execute_pipeline(_execution_input(5), provider=provider_a)
    scenario_audit = next(
        a for a in pipeline.stage_audits if a.stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION
    )
    # Ayni girdiyle bagimsiz runner cagrisi.
    evidence = _evidence()
    standalone = await run_scenario_stage(
        simulation_run_id=_RUN_ID,
        provider=MockAIProvider(),
        evidence=evidence,
        target_task="Sepete ekle",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )
    assert standalone.audit.prompt_hash == scenario_audit.prompt_hash
    assert standalone.audit.input_hash == scenario_audit.input_hash
    assert standalone.audit.output_hash == scenario_audit.output_hash
    assert standalone.audit.idempotency_key == scenario_audit.idempotency_key


# --- 12-13. Hata baglami / propagasyon -------------------------------------------


async def test_persona_behavior_batch_failure_preserves_batch_index():
    provider = FaultyProvider(fail_stage=AIPipelineStageType.PERSONA_BEHAVIOR, fail_batch_index=0)
    batch = build_persona_batches(_personas(5), batch_size=15)[0]
    with pytest.raises(PipelineStageError) as exc_info:
        await run_persona_behavior_batch(
            simulation_run_id=_RUN_ID,
            provider=provider,
            batch=batch,
            evidence=_evidence(),
            scenario=_scenario(),
            baseline_metrics=(),
        )
    assert exc_info.value.stage_type is AIPipelineStageType.PERSONA_BEHAVIOR
    assert exc_info.value.batch_index == 0
    assert exc_info.value.error_code == "provider_transport_error"


async def test_scenario_stage_provider_error_propagates_with_context():
    provider = FaultyProvider(fail_stage=AIPipelineStageType.SCENARIO_INTERPRETATION)
    with pytest.raises(PipelineStageError) as exc_info:
        await run_scenario_stage(
            simulation_run_id=_RUN_ID,
            provider=provider,
            evidence=_evidence(),
            target_task="Sepete ekle",
            test_name="t",
            test_description="d",
            methodology_context="m",
        )
    assert exc_info.value.stage_type is AIPipelineStageType.SCENARIO_INTERPRETATION
    assert exc_info.value.batch_index is None
    assert exc_info.value.error_code == "provider_transport_error"


def test_evidence_stage_validation_error_propagates():
    # Bos/gecersiz metrics -> ERROR_INVALID_EVIDENCE, PipelineStageError sarilir.
    with pytest.raises(PipelineStageError) as exc_info:
        run_evidence_stage(
            simulation_run_id=_RUN_ID,
            source_type="sim",
            metrics={},
            page_features=None,
            selected_modules=(),
            module_results=None,
        )
    assert exc_info.value.stage_type is AIPipelineStageType.EVIDENCE_PREPARATION
    assert exc_info.value.batch_index is None


# --- 14. Stage 6 bos common_issues -> bos findings (regresyon) --------------------


async def test_ux_report_stage_empty_issues_yields_empty_findings():
    result = await run_ux_report_stage(
        simulation_run_id=_RUN_ID,
        provider=MockAIProvider(),
        evidence=_evidence(),
        baseline_metrics=(),
        aggregation=_aggregation(issues=()),
        module_summary=(),
        methodology_context="m",
    )
    assert result.output.findings == ()


async def test_ux_report_stage_nonempty_issues_yields_findings():
    issues = (
        WeightedIssue(
            hypothesis_id="hyp:1",
            description="Odeme adimi karisik",
            affected_users=60,
            affected_share=0.6,
            affected_persona_indices=(0, 1),
            weighted_confidence=0.4,
        ),
    )
    result = await run_ux_report_stage(
        simulation_run_id=_RUN_ID,
        provider=MockAIProvider(),
        evidence=_evidence(),
        baseline_metrics=(),
        aggregation=_aggregation(issues=issues),
        module_summary=(),
        methodology_context="m",
    )
    assert len(result.output.findings) == 1


# --- 15. Kaynak-metin: Mock asla otomatik secilmez -------------------------------


def test_stage_runner_and_executor_source_have_no_auto_mock():
    for module in (stage_runner_module, executor_module):
        source = inspect.getsource(module)
        assert "MockAIProvider(" not in source
        for forbidden in ("httpx", "requests", "import socket", "environ"):
            assert forbidden not in source


# --- 16. execute_pipeline GERCEKTEN stage runner fonksiyonlarini cagirir ----------


async def test_execute_pipeline_delegates_to_stage_runners(monkeypatch):
    calls: dict[str, int] = {
        name: 0
        for name in (
            "run_evidence_stage",
            "run_batching_stage",
            "run_scenario_stage",
            "run_persona_behavior_batch",
            "run_aggregation_stage",
            "run_ux_report_stage",
        )
    }

    orig = {name: getattr(executor_module, name) for name in calls}

    def _wrap_sync(name):
        def _inner(*args, **kwargs):
            calls[name] += 1
            return orig[name](*args, **kwargs)

        return _inner

    def _wrap_async(name):
        async def _inner(*args, **kwargs):
            calls[name] += 1
            return await orig[name](*args, **kwargs)

        return _inner

    monkeypatch.setattr(executor_module, "run_evidence_stage", _wrap_sync("run_evidence_stage"))
    monkeypatch.setattr(executor_module, "run_batching_stage", _wrap_sync("run_batching_stage"))
    monkeypatch.setattr(executor_module, "run_aggregation_stage", _wrap_sync("run_aggregation_stage"))
    monkeypatch.setattr(executor_module, "run_scenario_stage", _wrap_async("run_scenario_stage"))
    monkeypatch.setattr(
        executor_module, "run_persona_behavior_batch", _wrap_async("run_persona_behavior_batch")
    )
    monkeypatch.setattr(executor_module, "run_ux_report_stage", _wrap_async("run_ux_report_stage"))

    # 16 persona -> 2 batch -> run_persona_behavior_batch tam 2 kez cagrilmali.
    await execute_pipeline(_execution_input(16), provider=MockAIProvider())

    assert calls["run_evidence_stage"] == 1
    assert calls["run_batching_stage"] == 1
    assert calls["run_scenario_stage"] == 1
    assert calls["run_persona_behavior_batch"] == 2
    assert calls["run_aggregation_stage"] == 1
    assert calls["run_ux_report_stage"] == 1
