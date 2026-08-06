"""Faz 3B.2A: persistence/codec + Stage 2 manifest + rehydration testleri (saf).

Bu dosya DB oturumu KULLANMAZ - in-memory `Persona` ORM nesneleri (id acikca
atanmis) ve saf DTO'lar ile calisir. Orkestrasyon/DB entegrasyon testleri
ayri dosyadadir (`test_ai_pipeline_orchestration.py`).
"""

from __future__ import annotations

import uuid

import pytest

from app.models.ai_pipeline import AIPipelineStageType
from app.models.personas import Persona
from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.persistence import (
    ManifestRehydrationError,
    StageOutputDecodeError,
    StageSchemaMismatchError,
    build_stage2_manifest,
    decode_persona_batch_manifest,
    decode_stage_output,
    encode_stage_output,
    persona_context_from_row,
    rehydrate_persona_batches,
)
from app.services.ai_pipeline.schemas import (
    AggregationResult,
    CompletionBucketStat,
    CompletionDistribution,
    LikelyCompletion,
    PageEvidence,
    PersonaBehaviorBatchOutput,
    PersonaBehaviorEstimate,
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
    UXReport,
)
from app.services.ai_pipeline.stage_hashing import persona_batches_output_hash
from app.services.ai_pipeline.stage_runner import run_batching_stage

# --- Factory yardimcilari --------------------------------------------------------


def _persona_context(index: int, *, weight: int = 10) -> PersonaContext:
    return PersonaContext(
        persona_id=uuid.uuid4(),
        index=index,
        label=f"Persona {index}",
        attributes={"age_range": "25_34", "device_class": "mobile"},
        population_weight=weight,
    )


def _persona_row_from_context(ctx: PersonaContext) -> Persona:
    row = Persona(
        simulation_run_id=uuid.uuid4(),
        index=ctx.index,
        label=ctx.label,
        attributes=dict(ctx.attributes),
        population_weight=ctx.population_weight,
    )
    row.id = ctx.persona_id
    return row


def _sample_evidence() -> PageEvidence:
    return prepare_page_evidence(
        source_type="url",
        metrics={
            "task_completion_probability": {"point_estimate": 0.8},
            "readability_score": 72.0,
        },
    )


def _sample_scenario() -> ScenarioInterpretation:
    return ScenarioInterpretation(
        steps=(
            TaskStep(
                step_id="step_1",
                instruction="Formu doldur",
                success_criterion="Gonderildi",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        success_criteria=("Kullanici gorevi tamamlar",),
        limitations="Sentetik tahmin.",
    )


def _sample_behavior() -> PersonaBehaviorBatchOutput:
    return PersonaBehaviorBatchOutput(
        batch_index=0,
        persona_results=(
            PersonaBehaviorEstimate(
                persona_index=0,
                likely_completion=LikelyCompletion.HIGH,
                confidence=0.7,
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="Sentetik tahmin.",
    )


def _sample_aggregation() -> AggregationResult:
    return AggregationResult(
        total_population=100,
        completion_distribution=CompletionDistribution(
            high=CompletionBucketStat(users=60, share=0.6),
            medium=CompletionBucketStat(users=30, share=0.3),
            low=CompletionBucketStat(users=10, share=0.1),
        ),
        overall_confidence=0.7,
        disclaimer="Sentetik ozet, kalibre edilmemis.",
        aggregation_hash="a" * 64,
    )


def _sample_ux_report() -> UXReport:
    return UXReport(
        summary="Genel olarak kullanilabilir.",
        limitations="Sentetik tahmin.",
        disclaimer="Kalibre edilmemis sentetik rapor.",
    )


# --- Codec: roundtrip ------------------------------------------------------------


@pytest.mark.parametrize(
    "stage_type, model",
    [
        (AIPipelineStageType.EVIDENCE_PREPARATION, _sample_evidence()),
        (AIPipelineStageType.SCENARIO_INTERPRETATION, _sample_scenario()),
        (AIPipelineStageType.PERSONA_BEHAVIOR, _sample_behavior()),
        (AIPipelineStageType.AGGREGATION, _sample_aggregation()),
        (AIPipelineStageType.UX_REPORT, _sample_ux_report()),
    ],
)
def test_encode_decode_roundtrip(stage_type, model):
    encoded = encode_stage_output(stage_type, model)
    assert isinstance(encoded, dict)
    decoded = decode_stage_output(stage_type, encoded)
    assert decoded == model
    assert type(decoded) is type(model)


def test_decode_coerces_json_arrays_back_to_tuples():
    """Pydantic `tuple[...]` alanlari encode'da JSON dizisi olur; decode'da
    yeniden tuple'a coerce edilmelidir (varsayim degil, gercek dogrulama)."""

    evidence = _sample_evidence()
    encoded = encode_stage_output(AIPipelineStageType.EVIDENCE_PREPARATION, evidence)
    assert isinstance(encoded["items"], list)  # JSON tarafinda dizi
    decoded = decode_stage_output(AIPipelineStageType.EVIDENCE_PREPARATION, encoded)
    assert isinstance(decoded, PageEvidence)
    assert isinstance(decoded.items, tuple)  # model tarafinda yeniden tuple


# --- Codec: sema/stage_type uyumu ------------------------------------------------


def test_encode_rejects_wrong_model_for_stage_type():
    with pytest.raises(StageSchemaMismatchError):
        encode_stage_output(AIPipelineStageType.UX_REPORT, _sample_scenario())


def test_decode_wrong_schema_for_stage_type_rejected():
    """PERSONA_BEHAVIOR satirinin JSON'i UXReport olarak cozulemez."""

    behavior_json = encode_stage_output(AIPipelineStageType.PERSONA_BEHAVIOR, _sample_behavior())
    with pytest.raises(StageOutputDecodeError):
        decode_stage_output(AIPipelineStageType.UX_REPORT, behavior_json)


def test_decode_corrupted_payload_raises_controlled_error():
    with pytest.raises(StageOutputDecodeError):
        decode_stage_output(AIPipelineStageType.AGGREGATION, {"bogus": True})


def test_decode_non_dict_payload_rejected():
    with pytest.raises(StageOutputDecodeError):
        decode_stage_output(AIPipelineStageType.UX_REPORT, ["not", "a", "dict"])


def test_decode_rejects_unknown_extra_field():
    encoded = encode_stage_output(AIPipelineStageType.UX_REPORT, _sample_ux_report())
    encoded["raw_provider_response"] = "SIZDIRILAMAZ"
    with pytest.raises(StageOutputDecodeError):
        decode_stage_output(AIPipelineStageType.UX_REPORT, encoded)


def test_no_raw_prompt_or_provider_fields_in_any_stage_schema():
    """Hicbir persist-cikti semasi ham prompt/provider-cevabi/PII alani tasimaz."""

    banned_substrings = ("prompt", "raw", "provider_response", "html", "dom", "authorization", "api_key")
    from app.services.ai_pipeline.persistence import _STAGE_OUTPUT_SCHEMA

    for schema in _STAGE_OUTPUT_SCHEMA.values():
        for field_name in schema.model_fields:
            lowered = field_name.lower()
            assert not any(
                bad in lowered for bad in banned_substrings
            ), f"{schema.__name__}.{field_name} yasakli alan ismi tasiyor"


# --- Stage 2 manifest ------------------------------------------------------------


def test_manifest_excludes_attributes_and_label():
    contexts = tuple(_persona_context(i) for i in range(3))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)

    dumped = manifest.model_dump(mode="json")
    text = str(dumped)
    assert "attributes" not in text
    assert "age_range" not in text
    # PersonaManifestEntry yalnizca id/index/population_weight tasir.
    for batch in manifest.batches:
        for entry in batch.personas:
            assert set(entry.model_dump().keys()) == {"persona_id", "index", "population_weight"}


def test_manifest_is_deterministic():
    contexts = tuple(_persona_context(i) for i in range(5))
    batches = build_persona_batches(contexts)
    m1 = build_stage2_manifest(batches)
    m2 = build_stage2_manifest(batches)
    assert m1 == m2
    assert m1.model_dump(mode="json") == m2.model_dump(mode="json")


def test_manifest_roundtrip_via_codec():
    contexts = tuple(_persona_context(i) for i in range(3))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    encoded = encode_stage_output(AIPipelineStageType.PERSONA_BATCH_PREPARATION, manifest)
    decoded = decode_persona_batch_manifest(encoded)
    assert decoded == manifest


# --- Rehydration -----------------------------------------------------------------


def test_rehydration_reconstructs_batches_with_attributes():
    contexts = tuple(_persona_context(i) for i in range(20))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    rows = [_persona_row_from_context(c) for c in contexts]

    rehydrated = rehydrate_persona_batches(manifest, rows)
    assert rehydrated == batches
    # attributes DB satirindan geri gelmis olmali (manifeste yazilmamisti).
    for batch in rehydrated:
        for persona in batch.personas:
            assert persona.attributes == {"age_range": "25_34", "device_class": "mobile"}


def test_rehydrated_output_hash_matches_original_stage_runner_hash():
    """KRITIK: manifest -> rehydrate edilmis tam PersonaBatch tuple'inin hash'i,
    Stage 2'nin `stage_runner` tarafindan kaydedilen output_hash'i ile AYNI."""

    contexts = tuple(_persona_context(i) for i in range(30))
    run_id = uuid.uuid4()
    stage_result = run_batching_stage(
        simulation_run_id=run_id, personas=contexts, evidence_output_hash="e" * 64
    )
    original_output_hash = stage_result.audit.output_hash

    manifest = build_stage2_manifest(stage_result.output)
    assert manifest.output_hash == original_output_hash

    rows = [_persona_row_from_context(c) for c in contexts]
    rehydrated = rehydrate_persona_batches(manifest, rows)
    assert persona_batches_output_hash(rehydrated) == original_output_hash


def test_rehydration_missing_persona_rejected():
    contexts = tuple(_persona_context(i) for i in range(4))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    rows = [_persona_row_from_context(c) for c in contexts[:-1]]  # biri eksik
    with pytest.raises(ManifestRehydrationError):
        rehydrate_persona_batches(manifest, rows)


def test_rehydration_extra_persona_rejected():
    contexts = tuple(_persona_context(i) for i in range(4))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    rows = [_persona_row_from_context(c) for c in contexts]
    rows.append(_persona_row_from_context(_persona_context(99)))  # fazladan
    with pytest.raises(ManifestRehydrationError):
        rehydrate_persona_batches(manifest, rows)


def test_rehydration_duplicate_persona_row_rejected():
    contexts = tuple(_persona_context(i) for i in range(3))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    rows = [_persona_row_from_context(c) for c in contexts]
    rows.append(rows[0])  # ayni id iki kez
    with pytest.raises(ManifestRehydrationError):
        rehydrate_persona_batches(manifest, rows)


def test_rehydration_changed_population_weight_rejected():
    contexts = tuple(_persona_context(i) for i in range(4))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    rows = [_persona_row_from_context(c) for c in contexts]
    rows[0].population_weight += 5  # DB'de agirlik degismis
    with pytest.raises(ManifestRehydrationError):
        rehydrate_persona_batches(manifest, rows)


def test_rehydration_batch_hash_mismatch_rejected():
    contexts = tuple(_persona_context(i) for i in range(4))
    batches = build_persona_batches(contexts)
    manifest = build_stage2_manifest(batches)
    # Manifestteki bir batch_hash'i boz.
    tampered = manifest.model_copy(deep=True)
    object.__setattr__(tampered.batches[0], "batch_hash", "f" * 64)
    rows = [_persona_row_from_context(c) for c in contexts]
    with pytest.raises(ManifestRehydrationError):
        rehydrate_persona_batches(tampered, rows)


def test_persona_context_from_row_reads_attributes_fresh():
    ctx = _persona_context(0)
    row = _persona_row_from_context(ctx)
    restored = persona_context_from_row(row)
    assert restored == ctx
