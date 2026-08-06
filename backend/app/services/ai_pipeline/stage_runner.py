"""Tekil, yeniden-kullanilabilir asama calistiricilari (stage runner) (Faz 3B.1).

`execute_pipeline`, alti asamayi (Stage 1-6) bellek-ici tek bir cagriyla
calistirir; bu modul ise her asamanin hash/audit/idempotency mantigini
BAGIMSIZ, saf fonksiyonlar halinde disari cikarir. Boylece ileride (bu faz
DEGIL) DB-destekli bir worker, ayni fonksiyonlari asama-asama cagirip her
adim arasinda persist edebilir.

Bu fonksiyonlar HICBIR DB oturumu/`add()`/`commit()`, kuyruk/arq, ortam
degiskeni okumasi veya global/varsayilan provider olusturmasi ICERMEZ. Bir
asama provider'a ihtiyac duyuyorsa (Stage 3/4/6), provider ACIK bir parametre
olarak gecirilir - asla otomatik olarak Mock'a dusulmez. Saf asamalar (Stage
1/2/5) provider'a hicbir zaman ULASMAZ.

Herhangi bir asama hatasi (provider hatasi VEYA calistiricinin kendi bagimsiz
re-validasyonu) `PipelineStageError` olarak YUKARI CIKAR.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline import aggregation as aggregation_module
from app.services.ai_pipeline import batching as batching_module
from app.services.ai_pipeline import evidence as evidence_module
from app.services.ai_pipeline import prompts as prompts_module
from app.services.ai_pipeline import validation as validation_module
from app.services.ai_pipeline.errors import AIPipelineError
from app.services.ai_pipeline.hashing import (
    DETERMINISTIC_MODEL,
    DETERMINISTIC_PROVIDER,
    hash_payload,
    hash_prompt_descriptor,
)
from app.services.ai_pipeline.provider import AIProvider, ProviderResult
from app.services.ai_pipeline.provider_errors import AIProviderError
from app.services.ai_pipeline.retry_policy import is_retryable_error
from app.services.ai_pipeline.schemas import (
    DEFAULT_PERSONA_BATCH_SIZE,
    AggregationResult,
    PageEvidence,
    PersonaBatch,
    PersonaBehaviorBatchOutput,
    PersonaContext,
    ScenarioInterpretation,
    UXReport,
)
from app.services.ai_pipeline.stage_hashing import (
    deterministic_stage_idempotency_key,
    evidence_stage_input_hash,
    persona_batches_output_hash,
    provider_stage_execution_idempotency_key,
)
from app.services.ai_pipeline.stage_inputs import (
    BaselineMetricSnapshot,
    ModuleSummary,
    PersonaBehaviorBatchInput,
    ScenarioInterpretationInput,
    UXReportInput,
)

T = TypeVar("T")
ResultT = TypeVar("ResultT", bound=BaseModel)

# Saf (LLM'siz) asamalarin idempotency key'inde kullanilacak sabit,
# reprodusible prompt etiketleri, artik `stage_hashing` icinde PAYLASILAN
# sabitlerdir (deger degismedi) - buraya ayni isimle re-import edilir ki bu
# modulun geri kalani (ve testleri) etkilenmesin.


class PipelineStageError(Exception):
    """Bir pipeline asamasinin hata-guvenli disari cikan tek sarmalayicisi.

    Gelecekteki persistence katmani, hangi asamada (`stage_type`/`batch_index`)
    ve hangi kodla (`error_code`) hata olustugunu bu nesneden okuyabilir.
    Orijinal hata `__cause__` (from exc) ile zincirlenir.
    """

    def __init__(
        self,
        *,
        stage_type: AIPipelineStageType,
        batch_index: int | None,
        error_code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        self.stage_type = stage_type
        self.batch_index = batch_index
        self.error_code = error_code
        # `retryable` (Faz 3B.2C): sarilan asil hatanin TYPED siniflandirmasi
        # (bkz. `retry_policy.is_retryable_error`) - persistence/worker katmani
        # (RUNNING->QUEUED mi yoksa terminal FAILED mi) kararini BU alandan
        # verir, hata mesajini (string) ASLA incelemez.
        self.retryable = retryable
        super().__init__(message)


class StageAudit(BaseModel):
    """Tek bir asamanin audit-facing metadata'si - ham prompt/istek/cevap YOK.

    `system_instructions` gibi hicbir hassas prompt icerigi burada ASLA
    bulunmaz; yalnizca `prompt_key`/`prompt_version`/`prompt_hash` acilir.
    """

    model_config = ConfigDict(extra="forbid")

    stage_type: AIPipelineStageType
    batch_index: int | None
    status: str
    prompt_key: str | None
    prompt_version: str | None
    prompt_hash: str | None
    input_hash: str
    output_hash: str
    idempotency_key: str
    provider: str
    model_name: str
    is_mock: bool
    # Yalnizca provider (LLM) asamalarinda (3/4/6) dolu - saf asamalarda (1/2/5)
    # `None`dur (bkz. AIPipelineStage.provider_configuration_hash kolonu).
    provider_configuration_hash: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    duration_ms: int = Field(ge=0)
    error_code: str | None = None


@dataclass(frozen=True)
class StageRunResult(Generic[T]):
    """Bir asama calistiricisinin ciktisi: dogrulanmis `output` + `audit`.

    `audit.output_hash`/`audit.idempotency_key` gibi turetilmis degerler,
    bir sonraki asamanin girdisinde (ornegin Stage 2'nin evidence hash'i)
    yeniden okunabilir - boylece cagiran taraf hash'i tekrar hesaplamaz.
    """

    output: T
    audit: StageAudit


def _error_code(exc: Exception) -> str:
    code = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    return code if isinstance(code, str) and code else "unknown_error"


def _deterministic_idempotency_key(
    *,
    simulation_run_id: uuid.UUID,
    stage_type: AIPipelineStageType,
    input_hash: str,
) -> str:
    # Ince sarmalayici: hesaplama artik `stage_hashing`de PAYLASILAN saf
    # yardimcidadir (deger degismedi) - hem bu calistirici hem de
    # `orchestration.initialize_ai_pipeline` ayni fonksiyonu kullanir.
    return deterministic_stage_idempotency_key(
        simulation_run_id=simulation_run_id, stage_type=stage_type, input_hash=input_hash
    )


def _pure_stage_audit(
    *,
    stage_type: AIPipelineStageType,
    simulation_run_id: uuid.UUID,
    input_hash: str,
    output_hash: str,
    duration_ms: int,
) -> StageAudit:
    """Saf (LLM'siz) asamalar (Stage 1/2/5) icin `StageAudit` uretir - sabit
    deterministik provider/model etiketleri, sifir token/maliyet."""

    return StageAudit(
        stage_type=stage_type,
        batch_index=None,
        status="succeeded",
        prompt_key=None,
        prompt_version=None,
        prompt_hash=None,
        input_hash=input_hash,
        output_hash=output_hash,
        idempotency_key=_deterministic_idempotency_key(
            simulation_run_id=simulation_run_id, stage_type=stage_type, input_hash=input_hash
        ),
        provider=DETERMINISTIC_PROVIDER,
        model_name=DETERMINISTIC_MODEL,
        is_mock=False,
        input_tokens=0,
        output_tokens=0,
        estimated_cost=0.0,
        duration_ms=duration_ms,
    )


def _provider_stage_audit(
    *,
    stage_type: AIPipelineStageType,
    batch_index: int | None,
    simulation_run_id: uuid.UUID,
    prompt: object,
    input_hash: str,
    output: ResultT,
    result: ProviderResult[ResultT],
) -> StageAudit:
    """Provider tabanli (Stage 3/4/6) asamalar icin `StageAudit` uretir.

    `prompt` bir `PromptDescriptor`tir (dongusel import'tan kacinmak icin
    gevsek tiplenmistir; alanlar asagida acikca okunur)."""

    prompt_hash = hash_prompt_descriptor(prompt)  # type: ignore[arg-type]
    output_hash = hash_payload(output.model_dump(mode="json"))
    # Execution idempotency key'i PAYLASILAN yardimciyla hesaplanir - worker'in
    # provider cagrisindan ONCE pinledigi degerle BIREBIR ayni algoritma
    # (Faz 3B.2C: tek hash kaynagi). Deger DEGISMEDI (golden korunur).
    idempotency_key = provider_stage_execution_idempotency_key(
        simulation_run_id=simulation_run_id,
        stage_type=stage_type,
        batch_index=batch_index,
        prompt_version=prompt.prompt_version,  # type: ignore[attr-defined]
        prompt_hash=prompt_hash,
        provider=result.provider_name,
        model_name=result.model_name,
        input_hash=input_hash,
        provider_configuration_fingerprint=result.configuration_fingerprint,
    )
    return StageAudit(
        stage_type=stage_type,
        batch_index=batch_index,
        status="succeeded",
        prompt_key=prompt.prompt_key,  # type: ignore[attr-defined]
        prompt_version=prompt.prompt_version,  # type: ignore[attr-defined]
        prompt_hash=prompt_hash,
        input_hash=input_hash,
        output_hash=output_hash,
        idempotency_key=idempotency_key,
        provider=result.provider_name,
        model_name=result.model_name,
        is_mock=result.is_mock,
        provider_configuration_hash=result.configuration_fingerprint,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost=result.estimated_cost,
        duration_ms=result.request_duration_ms,
    )


# --- Stage 1: EVIDENCE_PREPARATION (saf) -----------------------------------------


def run_evidence_stage(
    *,
    simulation_run_id: uuid.UUID,
    source_type: str,
    metrics: dict[str, object],
    page_features: dict[str, object] | None,
    selected_modules: tuple[str, ...],
    module_results: dict[str, object] | None,
) -> StageRunResult[PageEvidence]:
    """Stage 1 (saf): `prepare_page_evidence` ile evidence uretir; provider'a
    hicbir zaman ULASMAZ."""

    stage = AIPipelineStageType.EVIDENCE_PREPARATION
    start = time.perf_counter()
    try:
        evidence = evidence_module.prepare_page_evidence(
            source_type=source_type,
            metrics=metrics,
            page_features=page_features,
            selected_modules=selected_modules,
            module_results=module_results,  # type: ignore[arg-type]
        )
    except (AIPipelineError, AIProviderError) as exc:
        raise PipelineStageError(
            stage_type=stage,
            batch_index=None,
            error_code=_error_code(exc),
            message=str(exc),
            retryable=is_retryable_error(exc),
        ) from exc
    duration_ms = int(round((time.perf_counter() - start) * 1000))
    input_hash = evidence_stage_input_hash(
        source_type=source_type,
        metrics=metrics,
        page_features=page_features,
        selected_modules=selected_modules,
        module_results=module_results,
    )
    output_hash = hash_payload(evidence.model_dump(mode="json"))
    audit = _pure_stage_audit(
        stage_type=stage,
        simulation_run_id=simulation_run_id,
        input_hash=input_hash,
        output_hash=output_hash,
        duration_ms=duration_ms,
    )
    return StageRunResult(output=evidence, audit=audit)


# --- Stage 2: PERSONA_BATCH_PREPARATION (saf) ------------------------------------


def run_batching_stage(
    *,
    simulation_run_id: uuid.UUID,
    personas: tuple[PersonaContext, ...],
    evidence_output_hash: str,
) -> StageRunResult[tuple[PersonaBatch, ...]]:
    """Stage 2 (saf): personalari SABIT `DEFAULT_PERSONA_BATCH_SIZE` ile
    batch'lere ayirir. `batch_size` disari ACILMAZ; provider'a ULASMAZ."""

    stage = AIPipelineStageType.PERSONA_BATCH_PREPARATION
    start = time.perf_counter()
    try:
        batches = batching_module.build_persona_batches(personas, batch_size=DEFAULT_PERSONA_BATCH_SIZE)
    except (AIPipelineError, AIProviderError) as exc:
        raise PipelineStageError(
            stage_type=stage,
            batch_index=None,
            error_code=_error_code(exc),
            message=str(exc),
            retryable=is_retryable_error(exc),
        ) from exc
    duration_ms = int(round((time.perf_counter() - start) * 1000))
    input_hash = hash_payload({"batch_size": DEFAULT_PERSONA_BATCH_SIZE, "evidence": evidence_output_hash})
    output_hash = persona_batches_output_hash(batches)
    audit = _pure_stage_audit(
        stage_type=stage,
        simulation_run_id=simulation_run_id,
        input_hash=input_hash,
        output_hash=output_hash,
        duration_ms=duration_ms,
    )
    return StageRunResult(output=batches, audit=audit)


# --- Stage 3: SCENARIO_INTERPRETATION (provider) ---------------------------------


async def run_scenario_stage(
    *,
    simulation_run_id: uuid.UUID,
    provider: AIProvider,
    evidence: PageEvidence,
    target_task: str,
    test_name: str,
    test_description: str,
    methodology_context: str,
) -> StageRunResult[ScenarioInterpretation]:
    """Stage 3 (provider): senaryo yorumu uretir ve calistirici, provider'a
    (gercek/mock) KORU KORUNE guvenmez - orkestrasyon sinirinda bagimsiz olarak
    yeniden dogrular."""

    stage = AIPipelineStageType.SCENARIO_INTERPRETATION
    scenario_input = ScenarioInterpretationInput(
        evidence=evidence,
        target_task=target_task,
        test_name=test_name,
        test_description=test_description,
        methodology_context=methodology_context,
    )
    scenario_prompt = prompts_module.get_prompt(stage)
    input_hash = hash_payload(scenario_input.model_dump(mode="json"))
    try:
        scenario_result = await provider.generate_structured(
            stage_type=stage,
            batch_index=None,
            prompt=scenario_prompt,
            input_payload=scenario_input,
            output_schema=ScenarioInterpretation,
        )
        validation_module.validate_scenario_interpretation(scenario=scenario_result.output, evidence=evidence)
    except (AIPipelineError, AIProviderError) as exc:
        raise PipelineStageError(
            stage_type=stage,
            batch_index=None,
            error_code=_error_code(exc),
            message=str(exc),
            retryable=is_retryable_error(exc),
        ) from exc
    scenario = scenario_result.output
    audit = _provider_stage_audit(
        stage_type=stage,
        batch_index=None,
        simulation_run_id=simulation_run_id,
        prompt=scenario_prompt,
        input_hash=input_hash,
        output=scenario,
        result=scenario_result,
    )
    return StageRunResult(output=scenario, audit=audit)


# --- Stage 4: PERSONA_BEHAVIOR (provider, batch basina 1 cagri) ------------------


async def run_persona_behavior_batch(
    *,
    simulation_run_id: uuid.UUID,
    provider: AIProvider,
    batch: PersonaBatch,
    evidence: PageEvidence,
    scenario: ScenarioInterpretation,
    baseline_metrics: tuple[BaselineMetricSnapshot, ...],
) -> StageRunResult[PersonaBehaviorBatchOutput]:
    """Stage 4 (provider): TAM olarak TEK bir `PersonaBatch` isler ve tek bir
    provider cagrisi yapar - dahili batch dongusu YOKTUR (dongu `execute_pipeline`
    tarafindan yurutulur). Hata `batch.batch_index` baglami ile YUKARI CIKAR."""

    stage = AIPipelineStageType.PERSONA_BEHAVIOR
    behavior_prompt = prompts_module.get_prompt(stage)
    behavior_input = PersonaBehaviorBatchInput(
        batch=batch,
        evidence=evidence,
        scenario=scenario,
        baseline_metrics=baseline_metrics,
    )
    input_hash = hash_payload(behavior_input.model_dump(mode="json"))
    try:
        behavior_result = await provider.generate_structured(
            stage_type=stage,
            batch_index=batch.batch_index,
            prompt=behavior_prompt,
            input_payload=behavior_input,
            output_schema=PersonaBehaviorBatchOutput,
        )
        validation_module.validate_persona_behavior_batch(
            batch=batch,
            output=behavior_result.output,
            evidence=evidence,
            scenario=scenario,
        )
    except (AIPipelineError, AIProviderError) as exc:
        raise PipelineStageError(
            stage_type=stage,
            batch_index=batch.batch_index,
            error_code=_error_code(exc),
            message=str(exc),
            retryable=is_retryable_error(exc),
        ) from exc
    output = behavior_result.output
    audit = _provider_stage_audit(
        stage_type=stage,
        batch_index=batch.batch_index,
        simulation_run_id=simulation_run_id,
        prompt=behavior_prompt,
        input_hash=input_hash,
        output=output,
        result=behavior_result,
    )
    return StageRunResult(output=output, audit=audit)


# --- Stage 5: AGGREGATION (saf) --------------------------------------------------


def run_aggregation_stage(
    *,
    simulation_run_id: uuid.UUID,
    personas: tuple[PersonaContext, ...],
    behavior_outputs: tuple[PersonaBehaviorBatchOutput, ...],
    scenario: ScenarioInterpretation,
) -> StageRunResult[AggregationResult]:
    """Stage 5 (saf): tum batch ciktilarindaki persona sonuclarini duzlestirip
    `aggregate_persona_behavior` ile agregasyon uretir; provider'a ULASMAZ."""

    stage = AIPipelineStageType.AGGREGATION
    all_behavior_results = tuple(r for output in behavior_outputs for r in output.persona_results)
    start = time.perf_counter()
    try:
        aggregation = aggregation_module.aggregate_persona_behavior(
            personas=personas,
            behavior_results=all_behavior_results,
            scenario=scenario,
        )
    except (AIPipelineError, AIProviderError) as exc:
        raise PipelineStageError(
            stage_type=stage,
            batch_index=None,
            error_code=_error_code(exc),
            message=str(exc),
            retryable=is_retryable_error(exc),
        ) from exc
    duration_ms = int(round((time.perf_counter() - start) * 1000))
    input_hash = hash_payload(
        {
            "behavior_outputs": [o.model_dump(mode="json") for o in behavior_outputs],
            "scenario": scenario.model_dump(mode="json"),
        }
    )
    output_hash = hash_payload(aggregation.model_dump(mode="json"))
    audit = _pure_stage_audit(
        stage_type=stage,
        simulation_run_id=simulation_run_id,
        input_hash=input_hash,
        output_hash=output_hash,
        duration_ms=duration_ms,
    )
    return StageRunResult(output=aggregation, audit=audit)


# --- Stage 6: UX_REPORT (provider) -----------------------------------------------


async def run_ux_report_stage(
    *,
    simulation_run_id: uuid.UUID,
    provider: AIProvider,
    evidence: PageEvidence,
    baseline_metrics: tuple[BaselineMetricSnapshot, ...],
    aggregation: AggregationResult,
    module_summary: tuple[ModuleSummary, ...],
    methodology_context: str,
) -> StageRunResult[UXReport]:
    """Stage 6 (provider): agregasyon + evidence'tan UX raporu uretir ve
    calistirici ciktisini bagimsiz olarak yeniden dogrular."""

    stage = AIPipelineStageType.UX_REPORT
    report_input = UXReportInput(
        evidence=evidence,
        baseline_metrics=baseline_metrics,
        aggregation=aggregation,
        module_summary=module_summary,
        methodology_context=methodology_context,
    )
    report_prompt = prompts_module.get_prompt(stage)
    input_hash = hash_payload(report_input.model_dump(mode="json"))
    try:
        report_result = await provider.generate_structured(
            stage_type=stage,
            batch_index=None,
            prompt=report_prompt,
            input_payload=report_input,
            output_schema=UXReport,
        )
        validation_module.validate_ux_report(
            report=report_result.output,
            evidence=evidence,
            total_population=aggregation.total_population,
        )
    except (AIPipelineError, AIProviderError) as exc:
        raise PipelineStageError(
            stage_type=stage,
            batch_index=None,
            error_code=_error_code(exc),
            message=str(exc),
            retryable=is_retryable_error(exc),
        ) from exc
    report = report_result.output
    audit = _provider_stage_audit(
        stage_type=stage,
        batch_index=None,
        simulation_run_id=simulation_run_id,
        prompt=report_prompt,
        input_hash=input_hash,
        output=report,
        result=report_result,
    )
    return StageRunResult(output=report, audit=audit)


__all__ = [
    "PipelineStageError",
    "StageAudit",
    "StageRunResult",
    "run_evidence_stage",
    "run_batching_stage",
    "run_scenario_stage",
    "run_persona_behavior_batch",
    "run_aggregation_stage",
    "run_ux_report_stage",
]
