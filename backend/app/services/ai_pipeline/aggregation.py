"""Stage 5 - Deterministik aggregation (saf, DB'siz, ag cagrisiz, LLM'siz).

Bu asama HICBIR LLM/provider cagrisi YAPMAZ - yalnizca Stage 4'un (persona
behavior estimation) zaten uretilmis/dogrulanmis ciktilarini,
`population_weight` ile agirliklandirarak deterministik bir sekilde
ozetler. Ayni girdi HER ZAMAN byte-for-byte ayni `AggregationResult`i
uretir (bkz. testler).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.services.ai_pipeline.errors import (
    ERROR_INVALID_HYPOTHESIS_REFERENCE,
    ERROR_PERSONA_RESULT_MISMATCH,
    ERROR_PERSONAS_REQUIRED,
    AIPipelineError,
)
from app.services.ai_pipeline.hashing import hash_payload
from app.services.ai_pipeline.schemas import (
    AGGREGATION_DISCLAIMER,
    AGGREGATION_VERSION,
    AggregationResult,
    CompletionBucketStat,
    CompletionDistribution,
    LikelyCompletion,
    PersonaBehaviorEstimate,
    PersonaContext,
    ScenarioInterpretation,
    WeightedIssue,
)

_ROUND_DIGITS = 4


def _round(value: float) -> float:
    """Merkezi, tum modulde tek bir yuvarlama kurali (bkz. gorev talimati
    madde 9 "Float sonuclar icin merkezi ve test edilmis yuvarlama kurali")."""

    return round(value, _ROUND_DIGITS)


def aggregate_persona_behavior(
    *,
    personas: Sequence[PersonaContext],
    behavior_results: Sequence[PersonaBehaviorEstimate],
    scenario: ScenarioInterpretation,
) -> AggregationResult:
    """`behavior_results`i `population_weight` ile agirliklandirip
    deterministik bir `AggregationResult` uretir.

    Girdi sirasi sonucu ETKILEMEZ (dahili olarak `persona.index`e gore
    islenir). Butunluk (her persona tam bir sonuca sahip, fazla/yinelenen
    sonuc yok, hypothesis referanslari gecerli) bu fonksiyonun ICINDE,
    Stage 4'un per-batch dogrulamasindan BAGIMSIZ olarak yeniden kontrol
    edilir (bkz. gorev talimati madde 9 "once butunluk dogrula").
    """

    if not personas:
        raise AIPipelineError(ERROR_PERSONAS_REQUIRED, "en az bir persona gereklidir")

    persona_indices = [p.index for p in personas]
    if len(persona_indices) != len(set(persona_indices)):
        raise AIPipelineError(ERROR_PERSONA_RESULT_MISMATCH, "yinelenen persona index degeri bulundu")

    result_indices = [r.persona_index for r in behavior_results]
    if len(result_indices) != len(set(result_indices)):
        raise AIPipelineError(ERROR_PERSONA_RESULT_MISMATCH, "yinelenen persona_index sonucu bulundu")

    expected = set(persona_indices)
    actual = set(result_indices)
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise AIPipelineError(
            ERROR_PERSONA_RESULT_MISMATCH,
            f"persona sonuc kumesi eksik/fazla (eksik={missing}, fazla={extra})",
        )

    hypothesis_ids = scenario.hypothesis_ids()
    hypothesis_by_id = {h.hypothesis_id: h for h in scenario.friction_hypotheses}
    for result in behavior_results:
        unknown = [hyp for hyp in result.affected_hypothesis_ids if hyp not in hypothesis_ids]
        if unknown:
            raise AIPipelineError(
                ERROR_INVALID_HYPOTHESIS_REFERENCE,
                f"persona {result.persona_index}: bilinmeyen hypothesis referansi {unknown}",
            )

    weight_by_index = {p.index: p.population_weight for p in personas}
    total_population = sum(weight_by_index.values())

    result_by_index = {r.persona_index: r for r in behavior_results}

    # --- Tamamlama dagilimi -----------------------------------------------------
    bucket_indices: dict[LikelyCompletion, list[int]] = {
        LikelyCompletion.HIGH: [],
        LikelyCompletion.MEDIUM: [],
        LikelyCompletion.LOW: [],
    }
    for index, result in result_by_index.items():
        bucket_indices[result.likely_completion].append(index)

    def _bucket_stat(level: LikelyCompletion) -> CompletionBucketStat:
        users = sum(weight_by_index[i] for i in bucket_indices[level])
        share = _round(users / total_population)
        return CompletionBucketStat(users=users, share=share)

    completion_distribution = CompletionDistribution(
        high=_bucket_stat(LikelyCompletion.HIGH),
        medium=_bucket_stat(LikelyCompletion.MEDIUM),
        low=_bucket_stat(LikelyCompletion.LOW),
    )

    # --- Ortak sorunlar (yalnizca en az bir persona tarafindan referans
    # verilen hypothesis'ler) --------------------------------------------------
    common_issues: list[WeightedIssue] = []
    for hypothesis_id, hypothesis in hypothesis_by_id.items():
        affected_indices = sorted(
            index
            for index, result in result_by_index.items()
            if hypothesis_id in result.affected_hypothesis_ids
        )
        if not affected_indices:
            continue

        affected_users = sum(weight_by_index[i] for i in affected_indices)
        affected_share = _round(affected_users / total_population)
        weighted_confidence_sum = sum(
            weight_by_index[i] * result_by_index[i].confidence for i in affected_indices
        )
        weighted_confidence = _round(weighted_confidence_sum / affected_users)

        common_issues.append(
            WeightedIssue(
                hypothesis_id=hypothesis_id,
                description=hypothesis.description,
                affected_users=affected_users,
                affected_share=affected_share,
                affected_persona_indices=tuple(affected_indices),
                weighted_confidence=weighted_confidence,
            )
        )

    common_issues.sort(key=lambda issue: (-issue.affected_users, issue.hypothesis_id))

    # --- Genel guven --------------------------------------------------------------
    overall_confidence_sum = sum(
        weight_by_index[index] * result.confidence for index, result in result_by_index.items()
    )
    overall_confidence = _round(overall_confidence_sum / total_population)

    hashable_payload = {
        "aggregation_version": AGGREGATION_VERSION,
        "total_population": total_population,
        "completion_distribution": completion_distribution.model_dump(mode="json"),
        "common_issues": [issue.model_dump(mode="json") for issue in common_issues],
        "overall_confidence": overall_confidence,
        "disclaimer": AGGREGATION_DISCLAIMER,
    }
    aggregation_hash = hash_payload(hashable_payload)

    return AggregationResult(
        aggregation_version=AGGREGATION_VERSION,
        total_population=total_population,
        completion_distribution=completion_distribution,
        common_issues=tuple(common_issues),
        overall_confidence=overall_confidence,
        disclaimer=AGGREGATION_DISCLAIMER,
        aggregation_hash=aggregation_hash,
    )


__all__ = ["aggregate_persona_behavior"]
