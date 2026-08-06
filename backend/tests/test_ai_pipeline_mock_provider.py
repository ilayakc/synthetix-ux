"""Faz 2B: deterministik MockAIProvider (Stage 3/4/6) testleri."""

import inspect
import uuid

import pytest

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline import mock_provider as mock_provider_module
from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.hashing import hash_payload
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.schemas import (
    UX_REPORT_DISCLAIMER,
    AggregationResult,
    CompletionBucketStat,
    CompletionDistribution,
    FrictionHypothesis,
    LikelyCompletion,
    PersonaBehaviorBatchOutput,
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
    WeightedIssue,
)
from app.services.ai_pipeline.stage_inputs import (
    PersonaBehaviorBatchInput,
    ScenarioInterpretationInput,
    UXReportInput,
)

pytestmark = pytest.mark.unit


def _evidence(*, with_contrast=True, single=False):
    metrics: dict[str, object] = {"task_completion_probability": {"point_estimate": 0.7}}
    if not single:
        metrics["abandonment_probability"] = {"point_estimate": 0.2}
    if with_contrast:
        metrics["contrast_check"] = {"pass": True, "avg_ratio": 4.5}
    return prepare_page_evidence(source_type="sim", metrics=metrics)


def _scenario_input(evidence=None):
    return ScenarioInterpretationInput(
        evidence=evidence or _evidence(),
        target_task="Sepete ekle ve odeme yap",
        test_name="t",
        test_description="d",
        methodology_context="m",
    )


def _personas(n, attrs=None):
    return tuple(
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=i,
            label=f"P{i}",
            attributes=attrs or {},
            population_weight=10,
        )
        for i in range(n)
    )


def _scenario():
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


# --- Stage 3 ---------------------------------------------------------------------


async def test_stage3_uses_real_evidence_and_no_unknown_refs():
    provider = MockAIProvider()
    evidence = _evidence()
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=object(),  # type: ignore[arg-type]
        input_payload=_scenario_input(evidence),
        output_schema=ScenarioInterpretation,
    )
    scenario = result.output
    evidence_ids = evidence.evidence_ids()
    step_ids = [s.step_id for s in scenario.steps]
    assert len(step_ids) == len(set(step_ids))
    hyp_ids = [h.hypothesis_id for h in scenario.friction_hypotheses]
    assert len(hyp_ids) == len(set(hyp_ids))
    for step in scenario.steps:
        assert all(ref in evidence_ids for ref in step.evidence_references)
    for hyp in scenario.friction_hypotheses:
        assert all(ref in evidence_ids for ref in hyp.evidence_references)


async def test_stage3_limitations_reflect_low_evidence():
    provider = MockAIProvider()
    evidence = _evidence(single=True, with_contrast=False)
    assert len(evidence.evidence_ids()) < 2
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=object(),  # type: ignore[arg-type]
        input_payload=_scenario_input(evidence),
        output_schema=ScenarioInterpretation,
    )
    assert "sınırlı" in result.output.limitations.lower()


async def test_stage3_deterministic_output():
    provider = MockAIProvider()
    payload = _scenario_input()
    a = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=object(),  # type: ignore[arg-type]
        input_payload=payload,
        output_schema=ScenarioInterpretation,
    )
    b = await provider.generate_structured(
        stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
        batch_index=None,
        prompt=object(),  # type: ignore[arg-type]
        input_payload=payload,
        output_schema=ScenarioInterpretation,
    )
    assert hash_payload(a.output.model_dump(mode="json")) == hash_payload(b.output.model_dump(mode="json"))


# --- Stage 4 ---------------------------------------------------------------------


async def _run_stage4(personas, evidence=None, scenario=None):
    provider = MockAIProvider()
    evidence = evidence or _evidence()
    scenario = scenario or _scenario()
    batch = build_persona_batches(personas, batch_size=15)[0]
    payload = PersonaBehaviorBatchInput(batch=batch, evidence=evidence, scenario=scenario)
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
        batch_index=batch.batch_index,
        prompt=object(),  # type: ignore[arg-type]
        input_payload=payload,
        output_schema=PersonaBehaviorBatchOutput,
    )
    return batch, result.output


async def test_stage4_one_result_per_persona():
    personas = _personas(5)
    batch, output = await _run_stage4(personas)
    assert len(output.persona_results) == 5
    indices = [r.persona_index for r in output.persona_results]
    assert sorted(indices) == [0, 1, 2, 3, 4]
    assert len(indices) == len(set(indices))
    assert output.batch_index == batch.batch_index


async def test_stage4_valid_enum_and_references():
    personas = _personas(6)
    evidence = _evidence()
    scenario = _scenario()
    _, output = await _run_stage4(personas, evidence, scenario)
    evidence_ids = evidence.evidence_ids()
    hyp_ids = scenario.hypothesis_ids()
    for r in output.persona_results:
        assert isinstance(r.likely_completion, LikelyCompletion)
        assert all(ref in evidence_ids for ref in r.evidence_references)
        assert all(h in hyp_ids for h in r.affected_hypothesis_ids)


async def test_stage4_multiple_batches_validate_independently():
    for n in (3, 7, 12):
        personas = _personas(n)
        _, output = await _run_stage4(personas)
        assert len(output.persona_results) == n


async def test_stage4_deterministic_and_call_order_independent():
    personas = _personas(5)
    _, out_a = await _run_stage4(personas)
    _, out_b = await _run_stage4(personas)
    assert hash_payload(out_a.model_dump(mode="json")) == hash_payload(out_b.model_dump(mode="json"))


async def test_stage4_different_input_different_output():
    _, out_a = await _run_stage4(_personas(5))
    _, out_b = await _run_stage4(_personas(6))
    assert hash_payload(out_a.model_dump(mode="json")) != hash_payload(out_b.model_dump(mode="json"))


async def test_stage4_accessibility_concern_only_with_need_and_contrast():
    # Erisilebilirlik ihtiyaci beyan eden + kontrast evidence olan -> endise olabilir.
    personas = _personas(5, attrs={"accessibility_need": "visual"})
    _, output = await _run_stage4(personas, evidence=_evidence(with_contrast=True))
    assert any(r.accessibility_concerns for r in output.persona_results)

    # Kontrast evidence yoksa -> hic endise yok.
    _, output2 = await _run_stage4(personas, evidence=_evidence(with_contrast=False))
    assert all(not r.accessibility_concerns for r in output2.persona_results)

    # Ihtiyac "none" ise -> hic endise yok.
    personas_none = _personas(5, attrs={"accessibility_need": "none"})
    _, output3 = await _run_stage4(personas_none, evidence=_evidence(with_contrast=True))
    assert all(not r.accessibility_concerns for r in output3.persona_results)


# --- Stage 6 ---------------------------------------------------------------------


def _aggregation(issues=()):
    dist = CompletionDistribution(
        high=CompletionBucketStat(users=50, share=0.5),
        medium=CompletionBucketStat(users=30, share=0.3),
        low=CompletionBucketStat(users=20, share=0.2),
    )
    payload = {
        "aggregation_version": "aggregation-v1",
        "total_population": 100,
        "completion_distribution": dist.model_dump(mode="json"),
        "common_issues": [i.model_dump(mode="json") for i in issues],
        "overall_confidence": 0.5,
        "disclaimer": "d",
    }
    return AggregationResult(
        total_population=100,
        completion_distribution=dist,
        common_issues=issues,
        overall_confidence=0.5,
        disclaimer="d" * 10,
        aggregation_hash=hash_payload(payload),
    )


async def _run_stage6(aggregation, evidence=None):
    provider = MockAIProvider()
    evidence = evidence or _evidence()
    from app.services.ai_pipeline.schemas import UXReport

    payload = UXReportInput(
        evidence=evidence,
        aggregation=aggregation,
        methodology_context="m",
    )
    result = await provider.generate_structured(
        stage_type=AIPipelineStageType.UX_REPORT,
        batch_index=None,
        prompt=object(),  # type: ignore[arg-type]
        input_payload=payload,
        output_schema=UXReport,
    )
    return result.output


async def test_stage6_findings_trace_to_aggregation():
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
    agg = _aggregation(issues)
    report = await _run_stage6(agg)
    evidence_ids = _evidence().evidence_ids()
    for f in report.findings:
        assert f.estimated_affected_users <= agg.total_population
        assert f.estimated_affected_users == 60
        assert f.source_stage == "aggregation"
        assert all(ref in evidence_ids for ref in f.evidence_references)
    assert report.disclaimer == UX_REPORT_DISCLAIMER


async def test_stage6_empty_issues_produces_empty_findings():
    # Urun kurali: sorun yoksa UYDURMA bir bulgu uretme; findings bos kalmali.
    agg = _aggregation(())
    report = await _run_stage6(agg)
    assert report.findings == ()
    # Uydurulmus bir finding_id / estimated_affected_users OLMAMALI.
    source = inspect.getsource(mock_provider_module)
    assert "finding:no-issues-found" not in source
    # summary + disclaimer hala mevcut ve bos degil.
    assert report.summary.strip()
    assert report.disclaimer == UX_REPORT_DISCLAIMER
    assert report.limitations.strip()
    # Durus: "sorun yok" ifadesi asla "sayfada sorun yoktur" garantisine donusmemeli.
    assert "anlamına gelmez" in report.limitations


async def test_stage6_empty_findings_passes_validation():
    from app.services.ai_pipeline import validation as ai_validation

    agg = _aggregation(())
    report = await _run_stage6(agg)
    # validate_ux_report bos findings'i hatasiz kabul etmeli.
    ai_validation.validate_ux_report(
        report=report,
        evidence=_evidence(),
        total_population=agg.total_population,
    )


async def test_stage6_deterministic():
    issues = (
        WeightedIssue(
            hypothesis_id="hyp:1",
            description="x",
            affected_users=60,
            affected_share=0.6,
            affected_persona_indices=(0,),
            weighted_confidence=0.4,
        ),
    )
    agg = _aggregation(issues)
    a = await _run_stage6(agg)
    b = await _run_stage6(agg)
    assert hash_payload(a.model_dump(mode="json")) == hash_payload(b.model_dump(mode="json"))


# --- Purity / determinism source checks ------------------------------------------


def test_mock_provider_source_has_no_network_or_env_or_random():
    source = inspect.getsource(mock_provider_module)
    for forbidden in ("httpx", "requests", "import socket", "environ", "settings", "random", "secrets"):
        assert forbidden not in source, f"mock_provider kaynaginda yasak token: {forbidden}"
