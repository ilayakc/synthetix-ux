"""Faz 2A: Stage 3/4 semalari + asamalar-arasi (cross-stage) dogrulama testleri."""

import uuid

import pytest
from pydantic import ValidationError

from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.errors import (
    ERROR_BANNED_CLAIM,
    ERROR_INVALID_AGGREGATION,
    ERROR_INVALID_EVIDENCE_REFERENCE,
    ERROR_INVALID_HYPOTHESIS_REFERENCE,
    ERROR_PERSONA_RESULT_MISMATCH,
    AIPipelineError,
)
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.schemas import (
    FrictionHypothesis,
    LikelyCompletion,
    PersonaBehaviorBatchOutput,
    PersonaBehaviorEstimate,
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
    UXFinding,
    UXReport,
)
from app.services.ai_pipeline.validation import validate_persona_behavior_batch, validate_ux_report

pytestmark = pytest.mark.unit


def _metrics() -> dict:
    return {"task_completion_probability": {"point_estimate": 0.6}}


def _evidence():
    return prepare_page_evidence(source_type="url", metrics=_metrics())


def _scenario(**overrides) -> ScenarioInterpretation:
    base = dict(
        steps=(
            TaskStep(
                step_id="s1",
                instruction="Anasayfadan urun bul",
                success_criterion="Urun sayfasina ulasti",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        success_criteria=("Urun sayfasina ulasti",),
        friction_hypotheses=(
            FrictionHypothesis(
                hypothesis_id="h1",
                description="Navigasyon derin",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="Sentetik senaryo varsayimi.",
    )
    base.update(overrides)
    return ScenarioInterpretation(**base)


def _personas(count: int) -> list[PersonaContext]:
    return [
        PersonaContext(persona_id=uuid.uuid4(), index=i, label=f"P{i}", attributes={}, population_weight=10)
        for i in range(count)
    ]


# --- Sema kurallari (duplicate/limit/extra field) --------------------------------


def test_duplicate_step_id_is_rejected():
    with pytest.raises(ValidationError):
        _scenario(
            steps=(
                TaskStep(
                    step_id="s1",
                    instruction="a",
                    success_criterion="a",
                    evidence_references=("metric:task_completion_probability",),
                ),
                TaskStep(
                    step_id="s1",
                    instruction="b",
                    success_criterion="b",
                    evidence_references=("metric:task_completion_probability",),
                ),
            )
        )


def test_duplicate_hypothesis_id_is_rejected():
    with pytest.raises(ValidationError):
        _scenario(
            friction_hypotheses=(
                FrictionHypothesis(
                    hypothesis_id="h1",
                    description="a",
                    evidence_references=("metric:task_completion_probability",),
                ),
                FrictionHypothesis(
                    hypothesis_id="h1",
                    description="b",
                    evidence_references=("metric:task_completion_probability",),
                ),
            )
        )


def test_duplicate_persona_index_in_batch_output_is_rejected():
    with pytest.raises(ValidationError):
        PersonaBehaviorBatchOutput(
            batch_index=0,
            persona_results=(
                PersonaBehaviorEstimate(
                    persona_index=0,
                    likely_completion=LikelyCompletion.HIGH,
                    confidence=0.5,
                    evidence_references=("metric:task_completion_probability",),
                ),
                PersonaBehaviorEstimate(
                    persona_index=0,
                    likely_completion=LikelyCompletion.LOW,
                    confidence=0.5,
                    evidence_references=("metric:task_completion_probability",),
                ),
            ),
            limitations="ok",
        )


def test_extra_field_is_rejected_on_scenario_interpretation():
    with pytest.raises(ValidationError):
        ScenarioInterpretation.model_validate(
            {
                "steps": [
                    {
                        "step_id": "s1",
                        "instruction": "a",
                        "success_criterion": "a",
                        "evidence_references": ["metric:task_completion_probability"],
                    }
                ],
                "success_criteria": ["a"],
                "limitations": "ok",
                "unexpected_field": "should not be allowed",
            }
        )


def test_extra_field_is_rejected_on_persona_behavior_estimate():
    with pytest.raises(ValidationError):
        PersonaBehaviorEstimate.model_validate(
            {
                "persona_index": 0,
                "likely_completion": "high",
                "confidence": 0.5,
                "evidence_references": ["metric:task_completion_probability"],
                "raw_provider_response": "should not be allowed",
            }
        )


def test_more_than_15_steps_is_rejected():
    steps = tuple(
        TaskStep(
            step_id=f"s{i}",
            instruction="a",
            success_criterion="a",
            evidence_references=("metric:task_completion_probability",),
        )
        for i in range(16)
    )
    with pytest.raises(ValidationError):
        _scenario(steps=steps)


def test_confidence_out_of_range_is_rejected():
    with pytest.raises(ValidationError):
        PersonaBehaviorEstimate(
            persona_index=0,
            likely_completion=LikelyCompletion.HIGH,
            confidence=1.5,
            evidence_references=("metric:task_completion_probability",),
        )


# --- validate_persona_behavior_batch (cross-stage) --------------------------------


def test_valid_batch_output_passes_cross_validation():
    personas = _personas(2)
    batch = build_persona_batches(personas, batch_size=2)[0]
    evidence = _evidence()
    scenario = _scenario()
    output = PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=tuple(
            PersonaBehaviorEstimate(
                persona_index=p.index,
                likely_completion=LikelyCompletion.HIGH,
                affected_hypothesis_ids=("h1",),
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            )
            for p in personas
        ),
        limitations="ok",
    )
    validate_persona_behavior_batch(batch=batch, output=output, evidence=evidence, scenario=scenario)


def test_missing_persona_result_is_rejected():
    personas = _personas(2)
    batch = build_persona_batches(personas, batch_size=2)[0]
    output = PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=(
            PersonaBehaviorEstimate(
                persona_index=0,
                likely_completion=LikelyCompletion.HIGH,
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="ok",
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_persona_behavior_batch(
            batch=batch, output=output, evidence=_evidence(), scenario=_scenario()
        )
    assert exc_info.value.code == ERROR_PERSONA_RESULT_MISMATCH


def test_extra_persona_result_is_rejected():
    personas = _personas(1)
    batch = build_persona_batches(personas, batch_size=1)[0]
    output = PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=(
            PersonaBehaviorEstimate(
                persona_index=0,
                likely_completion=LikelyCompletion.HIGH,
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            ),
            PersonaBehaviorEstimate(
                persona_index=99,
                likely_completion=LikelyCompletion.HIGH,
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="ok",
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_persona_behavior_batch(
            batch=batch, output=output, evidence=_evidence(), scenario=_scenario()
        )
    assert exc_info.value.code == ERROR_PERSONA_RESULT_MISMATCH


def test_unknown_evidence_reference_is_rejected():
    personas = _personas(1)
    batch = build_persona_batches(personas, batch_size=1)[0]
    output = PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=(
            PersonaBehaviorEstimate(
                persona_index=0,
                likely_completion=LikelyCompletion.HIGH,
                confidence=0.7,
                evidence_references=("metric:does_not_exist",),
            ),
        ),
        limitations="ok",
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_persona_behavior_batch(
            batch=batch, output=output, evidence=_evidence(), scenario=_scenario()
        )
    assert exc_info.value.code == ERROR_INVALID_EVIDENCE_REFERENCE


def test_unknown_hypothesis_reference_is_rejected():
    personas = _personas(1)
    batch = build_persona_batches(personas, batch_size=1)[0]
    output = PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=(
            PersonaBehaviorEstimate(
                persona_index=0,
                likely_completion=LikelyCompletion.HIGH,
                affected_hypothesis_ids=("does_not_exist",),
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="ok",
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_persona_behavior_batch(
            batch=batch, output=output, evidence=_evidence(), scenario=_scenario()
        )
    assert exc_info.value.code == ERROR_INVALID_HYPOTHESIS_REFERENCE


def test_banned_claim_in_accessibility_concern_is_rejected():
    personas = _personas(1)
    batch = build_persona_batches(personas, batch_size=1)[0]
    output = PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=(
            PersonaBehaviorEstimate(
                persona_index=0,
                likely_completion=LikelyCompletion.HIGH,
                accessibility_concerns=("Bu kanitlandi.",),
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="ok",
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_persona_behavior_batch(
            batch=batch, output=output, evidence=_evidence(), scenario=_scenario()
        )
    assert exc_info.value.code == ERROR_BANNED_CLAIM


# --- validate_ux_report (cross-stage) ---------------------------------------------


def _ux_report(**overrides) -> UXReport:
    base = dict(
        summary="Genel ozet",
        findings=(
            UXFinding(
                finding_id="f1",
                priority="high",
                finding="Navigasyon derin",
                source_stage="aggregation",
                evidence_references=("metric:task_completion_probability",),
                estimated_affected_users=5,
                recommendation="Adim sayisini azalt",
                confidence=0.6,
            ),
        ),
        limitations="Sentetik tahmin",
        disclaimer="Bu gercek kullanici olcumu degildir.",
    )
    base.update(overrides)
    return UXReport(**base)


def test_valid_ux_report_passes_cross_validation():
    validate_ux_report(report=_ux_report(), evidence=_evidence(), total_population=10)


def test_ux_report_allows_empty_findings():
    # Sema gevsetmesi (min_length=1 kaldirildi): 0 bulgulu rapor gecerlidir.
    report = _ux_report(findings=())
    assert report.findings == ()


def test_ux_report_findings_can_be_omitted():
    # findings hic verilmezse default_factory=tuple ile bos tuple olur.
    report = UXReport(
        summary="Genel ozet",
        limitations="Sentetik tahmin",
        disclaimer="Bu gercek kullanici olcumu degildir.",
    )
    assert report.findings == ()


def test_empty_findings_ux_report_passes_cross_validation():
    # validate_ux_report bos findings'i hatasiz kabul eder (dongu 0 kez doner).
    validate_ux_report(report=_ux_report(findings=()), evidence=_evidence(), total_population=10)


def test_duplicate_finding_id_is_rejected():
    with pytest.raises(ValidationError):
        _ux_report(
            findings=(
                UXFinding(
                    finding_id="f1",
                    priority="high",
                    finding="a",
                    source_stage="aggregation",
                    evidence_references=("metric:task_completion_probability",),
                    estimated_affected_users=1,
                    recommendation="a",
                    confidence=0.5,
                ),
                UXFinding(
                    finding_id="f1",
                    priority="low",
                    finding="b",
                    source_stage="aggregation",
                    evidence_references=("metric:task_completion_probability",),
                    estimated_affected_users=1,
                    recommendation="b",
                    confidence=0.5,
                ),
            )
        )


def test_estimated_affected_users_exceeding_total_population_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        validate_ux_report(report=_ux_report(), evidence=_evidence(), total_population=1)
    assert exc_info.value.code == ERROR_INVALID_AGGREGATION


def test_ux_report_unknown_evidence_reference_is_rejected():
    report = _ux_report(
        findings=(
            UXFinding(
                finding_id="f1",
                priority="high",
                finding="a",
                source_stage="aggregation",
                evidence_references=("metric:does_not_exist",),
                estimated_affected_users=1,
                recommendation="a",
                confidence=0.5,
            ),
        )
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_ux_report(report=report, evidence=_evidence(), total_population=10)
    assert exc_info.value.code == ERROR_INVALID_EVIDENCE_REFERENCE


def test_ux_report_banned_claim_is_rejected():
    report = _ux_report(
        findings=(
            UXFinding(
                finding_id="f1",
                priority="high",
                finding="Bu gercek kullanici gordu.",
                source_stage="aggregation",
                evidence_references=("metric:task_completion_probability",),
                estimated_affected_users=1,
                recommendation="a",
                confidence=0.5,
            ),
        )
    )
    with pytest.raises(AIPipelineError) as exc_info:
        validate_ux_report(report=report, evidence=_evidence(), total_population=10)
    assert exc_info.value.code == ERROR_BANNED_CLAIM


def test_ux_report_requires_disclaimer():
    with pytest.raises(ValidationError):
        UXReport.model_validate(
            {
                "summary": "ozet",
                "findings": [
                    {
                        "finding_id": "f1",
                        "priority": "high",
                        "finding": "a",
                        "source_stage": "aggregation",
                        "evidence_references": ["metric:task_completion_probability"],
                        "estimated_affected_users": 1,
                        "recommendation": "a",
                        "confidence": 0.5,
                    }
                ],
                "limitations": "ok",
                "disclaimer": "",
            }
        )
