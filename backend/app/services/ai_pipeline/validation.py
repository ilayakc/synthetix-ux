"""Asamalar-arasi (cross-stage) dogrulama yardimcilari (saf, DB'siz, ag cagrisiz).

Bu modul, bir asamanin (ornegin Stage 4 - persona behavior) ciktisinin bir
ONCEKI asamanin (Stage 2 - persona batch, Stage 3 - scenario, Stage 1 -
evidence) somut icerigiyle TUTARLI oldugunu dogrular - LLM'in evidence'ta
olmayan bir elementten veya tanimlanmamis bir hypothesis/step'ten soz
etmesini ENGELLER (bkz. gorev talimati madde 8/10).

`assert_no_banned_ai_claims` (bkz. app.services.ai_explanation) DOGRUDAN
yeniden kullanilir - burada ikinci bir yasakli-ifade listesi ICAT EDILMEZ
ve `ai_explanation`in davranisi hicbir sekilde degistirilmez.
"""

from __future__ import annotations

from app.services.ai_explanation import AIExplanationValidationError, assert_no_banned_ai_claims
from app.services.ai_pipeline.errors import (
    ERROR_BANNED_CLAIM,
    ERROR_INVALID_AGGREGATION,
    ERROR_INVALID_EVIDENCE_REFERENCE,
    ERROR_INVALID_HYPOTHESIS_REFERENCE,
    ERROR_PERSONA_RESULT_MISMATCH,
    AIPipelineError,
)
from app.services.ai_pipeline.schemas import (
    PageEvidence,
    PersonaBatch,
    PersonaBehaviorBatchOutput,
    ScenarioInterpretation,
    UXReport,
)


def _assert_no_banned_claims(text: str) -> None:
    try:
        assert_no_banned_ai_claims(text)
    except AIExplanationValidationError as exc:
        raise AIPipelineError(ERROR_BANNED_CLAIM, str(exc)) from exc


def validate_scenario_interpretation(
    *,
    scenario: ScenarioInterpretation,
    evidence: PageEvidence,
) -> None:
    """Stage 3 ciktisinin (TaskStep/FrictionHypothesis evidence_references)
    kaynak `evidence` ile TUTARLI oldugunu dogrular; ayrica banned-claim
    taramasi yapar. Herhangi bir ihlalde `AIPipelineError` firlatir."""

    evidence_ids = evidence.evidence_ids()

    for step in scenario.steps:
        unknown = [ref for ref in step.evidence_references if ref not in evidence_ids]
        if unknown:
            raise AIPipelineError(
                ERROR_INVALID_EVIDENCE_REFERENCE,
                f"'{step.step_id}': bilinmeyen evidence referansi {unknown}",
            )
        _assert_no_banned_claims(step.instruction)
        _assert_no_banned_claims(step.success_criterion)

    for hypothesis in scenario.friction_hypotheses:
        unknown = [ref for ref in hypothesis.evidence_references if ref not in evidence_ids]
        if unknown:
            raise AIPipelineError(
                ERROR_INVALID_EVIDENCE_REFERENCE,
                f"'{hypothesis.hypothesis_id}': bilinmeyen evidence referansi {unknown}",
            )
        _assert_no_banned_claims(hypothesis.description)

    for criterion in scenario.success_criteria:
        _assert_no_banned_claims(criterion)

    _assert_no_banned_claims(scenario.limitations)


def validate_persona_behavior_batch(
    *,
    batch: PersonaBatch,
    output: PersonaBehaviorBatchOutput,
    evidence: PageEvidence,
    scenario: ScenarioInterpretation,
) -> None:
    """Bir `PersonaBehaviorBatchOutput`in, kaynagi olan `batch`/`evidence`/
    `scenario` ile TUTARLI oldugunu dogrular; herhangi bir ihlalde
    `AIPipelineError` firlatir (asla sessizce duzeltmez/yok saymaz)."""

    if output.batch_index != batch.batch_index:
        raise AIPipelineError(
            ERROR_PERSONA_RESULT_MISMATCH,
            f"batch_index uyusmuyor (batch={batch.batch_index}, output={output.batch_index})",
        )

    expected_indices = {p.index for p in batch.personas}
    actual_indices = {r.persona_index for r in output.persona_results}
    if expected_indices != actual_indices:
        missing = sorted(expected_indices - actual_indices)
        extra = sorted(actual_indices - expected_indices)
        raise AIPipelineError(
            ERROR_PERSONA_RESULT_MISMATCH,
            f"persona sonuc kumesi batch ile eslesmiyor (eksik={missing}, fazla={extra})",
        )

    evidence_ids = evidence.evidence_ids()
    hypothesis_ids = scenario.hypothesis_ids()

    for result in output.persona_results:
        unknown_evidence = [ref for ref in result.evidence_references if ref not in evidence_ids]
        if unknown_evidence:
            raise AIPipelineError(
                ERROR_INVALID_EVIDENCE_REFERENCE,
                f"persona {result.persona_index}: bilinmeyen evidence referansi {unknown_evidence}",
            )

        unknown_hypotheses = [
            hyp_id for hyp_id in result.affected_hypothesis_ids if hyp_id not in hypothesis_ids
        ]
        if unknown_hypotheses:
            raise AIPipelineError(
                ERROR_INVALID_HYPOTHESIS_REFERENCE,
                f"persona {result.persona_index}: bilinmeyen hypothesis referansi {unknown_hypotheses}",
            )

        for concern in result.accessibility_concerns:
            _assert_no_banned_claims(concern)

    _assert_no_banned_claims(output.limitations)


def validate_ux_report(
    *,
    report: UXReport,
    evidence: PageEvidence,
    total_population: int,
) -> None:
    """Bir `UXReport`in, kaynak `evidence` ve toplam nufusla TUTARLI
    oldugunu dogrular."""

    evidence_ids = evidence.evidence_ids()

    for finding in report.findings:
        if finding.estimated_affected_users > total_population:
            raise AIPipelineError(
                ERROR_INVALID_AGGREGATION,
                f"'{finding.finding_id}': estimated_affected_users ({finding.estimated_affected_users}) "
                f"total_population'i ({total_population}) asiyor",
            )

        unknown_evidence = [ref for ref in finding.evidence_references if ref not in evidence_ids]
        if unknown_evidence:
            raise AIPipelineError(
                ERROR_INVALID_EVIDENCE_REFERENCE,
                f"'{finding.finding_id}': bilinmeyen evidence referansi {unknown_evidence}",
            )

        _assert_no_banned_claims(finding.finding)
        _assert_no_banned_claims(finding.recommendation)

    _assert_no_banned_claims(report.summary)
    _assert_no_banned_claims(report.limitations)


__all__ = [
    "validate_scenario_interpretation",
    "validate_persona_behavior_batch",
    "validate_ux_report",
]
