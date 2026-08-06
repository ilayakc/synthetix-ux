"""Saf, ag-siz, DB-siz AI pipeline orkestratoru (Faz 2B).

`execute_pipeline`, alti asamayi (Stage 1-6) SIRAYLA calistirir:
1. EVIDENCE_PREPARATION (saf)      -> run_evidence_stage
2. PERSONA_BATCH_PREPARATION (saf) -> run_batching_stage (batch_size=15 SABIT)
3. SCENARIO_INTERPRETATION (provider) -> run_scenario_stage
4. PERSONA_BEHAVIOR (provider, batch basina 1 cagri) -> run_persona_behavior_batch
5. AGGREGATION (saf)               -> run_aggregation_stage
6. UX_REPORT (provider)            -> run_ux_report_stage

Her asamanin hash/audit/idempotency mantigi `stage_runner.py`de tek tek,
bagimsiz cagrilabilir fonksiyonlarda yasar; `execute_pipeline` yalnizca
bunlari sirayla baglar. Bu modul HICBIR DB oturumu/`add()`/`commit()`, ag
cagrisi veya ortam degiskeni okumasi ICERMEZ. Herhangi bir asama hatasi
(provider hatasi VEYA calistiricinin kendi bagimsiz re-validasyonu)
`PipelineStageError` olarak YUKARI CIKAR - asla yakalanip yari/kismi sonuc
uretilmez ve ASLA otomatik olarak Mock'a dusulmez.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.services.ai_pipeline.hashing import hash_payload
from app.services.ai_pipeline.provider import AIProvider
from app.services.ai_pipeline.schemas import (
    AggregationResult,
    PageEvidence,
    PersonaBatch,
    PersonaBehaviorBatchOutput,
    PersonaContext,
    ScenarioInterpretation,
    UXReport,
)
from app.services.ai_pipeline.stage_inputs import (
    BaselineMetricSnapshot,
    ModuleSummary,
)
from app.services.ai_pipeline.stage_runner import (
    PipelineStageError,
    StageAudit,
    run_aggregation_stage,
    run_batching_stage,
    run_evidence_stage,
    run_persona_behavior_batch,
    run_scenario_stage,
    run_ux_report_stage,
)


class AIPipelineExecutionInput(BaseModel):
    """Pipeline'in SAF girdisi - hicbir ORM oturumu/`SimulationRun`/`Persona`
    ORM satiri kabul etmez, yalnizca duz degerler ve hazir `PersonaContext`
    DTO'lari. `metrics`/`page_features`/`module_results` gevsek `dict`
    tutulur cunku dogrudan `prepare_page_evidence`in allowlist'li Mapping
    parametrelerine akar (allowlisting orada yapilir)."""

    model_config = ConfigDict(extra="forbid")

    simulation_run_id: uuid.UUID
    source_type: str = Field(min_length=1, max_length=50)
    metrics: dict[str, object]
    page_features: dict[str, object] | None = None
    selected_modules: tuple[str, ...] = ()
    module_results: dict[str, object] | None = None
    target_task: str = Field(min_length=1, max_length=500)
    test_name: str = Field(min_length=1, max_length=200)
    test_description: str = Field(min_length=1, max_length=1000)
    methodology_context: str = Field(min_length=1, max_length=1000)
    personas: tuple[PersonaContext, ...] = Field(min_length=1)
    baseline_metrics: tuple[BaselineMetricSnapshot, ...] = ()
    module_summary: tuple[ModuleSummary, ...] = ()


class AIPipelineExecutionResult(BaseModel):
    """Tum pipeline'in saf ciktisi - ham prompt/provider-cevabi alani YOK."""

    model_config = ConfigDict(extra="forbid")

    evidence: PageEvidence
    batches: tuple[PersonaBatch, ...]
    scenario: ScenarioInterpretation
    behavior_outputs: tuple[PersonaBehaviorBatchOutput, ...]
    aggregation: AggregationResult
    report: UXReport
    stage_audits: tuple[StageAudit, ...]
    provider_name: str
    model_name: str
    is_mock: bool
    total_input_tokens: int = Field(ge=0)
    total_output_tokens: int = Field(ge=0)
    total_estimated_cost: float | None = Field(default=None, ge=0.0)
    pipeline_input_hash: str
    pipeline_output_hash: str


def _sum_estimated_cost(audits: list[StageAudit]) -> float | None:
    """Tum None ise None; en az biri float ise None olmayanlarin toplami."""

    contributions = [a.estimated_cost for a in audits if a.estimated_cost is not None]
    if not contributions:
        return None
    return sum(contributions)


async def execute_pipeline(
    execution_input: AIPipelineExecutionInput,
    *,
    provider: AIProvider,
) -> AIPipelineExecutionResult:
    """Altı asamayi sirayla calistirir ve tek bir `AIPipelineExecutionResult`
    dondurur. Her asama, `stage_runner`daki tekil calistiriciya devredilir;
    herhangi bir asama hatasi `PipelineStageError` olarak yukari cikar."""

    run_id = execution_input.simulation_run_id
    audits: list[StageAudit] = []

    # --- Stage 1: EVIDENCE_PREPARATION (saf) ------------------------------------
    evidence_run = run_evidence_stage(
        simulation_run_id=run_id,
        source_type=execution_input.source_type,
        metrics=execution_input.metrics,
        page_features=execution_input.page_features,
        selected_modules=execution_input.selected_modules,
        module_results=execution_input.module_results,
    )
    evidence = evidence_run.output
    audits.append(evidence_run.audit)

    # --- Stage 2: PERSONA_BATCH_PREPARATION (saf) -------------------------------
    batching_run = run_batching_stage(
        simulation_run_id=run_id,
        personas=execution_input.personas,
        evidence_output_hash=evidence_run.audit.output_hash,
    )
    batches = batching_run.output
    audits.append(batching_run.audit)

    # --- Stage 3: SCENARIO_INTERPRETATION (provider) ----------------------------
    scenario_run = await run_scenario_stage(
        simulation_run_id=run_id,
        provider=provider,
        evidence=evidence,
        target_task=execution_input.target_task,
        test_name=execution_input.test_name,
        test_description=execution_input.test_description,
        methodology_context=execution_input.methodology_context,
    )
    scenario = scenario_run.output
    audits.append(scenario_run.audit)

    # --- Stage 4: PERSONA_BEHAVIOR (provider, batch basina 1 cagri) -------------
    behavior_outputs: list[PersonaBehaviorBatchOutput] = []
    for batch in sorted(batches, key=lambda b: b.batch_index):
        behavior_run = await run_persona_behavior_batch(
            simulation_run_id=run_id,
            provider=provider,
            batch=batch,
            evidence=evidence,
            scenario=scenario,
            baseline_metrics=execution_input.baseline_metrics,
        )
        behavior_outputs.append(behavior_run.output)
        audits.append(behavior_run.audit)

    # --- Stage 5: AGGREGATION (saf) ---------------------------------------------
    aggregation_run = run_aggregation_stage(
        simulation_run_id=run_id,
        personas=execution_input.personas,
        behavior_outputs=tuple(behavior_outputs),
        scenario=scenario,
    )
    aggregation = aggregation_run.output
    audits.append(aggregation_run.audit)

    # --- Stage 6: UX_REPORT (provider) ------------------------------------------
    report_run = await run_ux_report_stage(
        simulation_run_id=run_id,
        provider=provider,
        evidence=evidence,
        baseline_metrics=execution_input.baseline_metrics,
        aggregation=aggregation,
        module_summary=execution_input.module_summary,
        methodology_context=execution_input.methodology_context,
    )
    report = report_run.output
    audits.append(report_run.audit)

    # --- Toplamlar ve pipeline hash'leri ----------------------------------------
    total_input_tokens = sum(a.input_tokens for a in audits)
    total_output_tokens = sum(a.output_tokens for a in audits)
    total_estimated_cost = _sum_estimated_cost(audits)

    pipeline_input_hash = hash_payload(execution_input.model_dump(mode="json"))
    # Cikti hash'i, kanonik (index'e gore sabitlenmis) batch/aggregation
    # sonuclarindan olusur - girdi persona sirasindan BAGIMSIZDIR.
    pipeline_output_hash = hash_payload(
        {
            "evidence": evidence.model_dump(mode="json"),
            "batches": [b.model_dump(mode="json") for b in batches],
            "scenario": scenario.model_dump(mode="json"),
            "behavior_outputs": [o.model_dump(mode="json") for o in behavior_outputs],
            "aggregation": aggregation.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
        }
    )

    return AIPipelineExecutionResult(
        evidence=evidence,
        batches=batches,
        scenario=scenario,
        behavior_outputs=tuple(behavior_outputs),
        aggregation=aggregation,
        report=report,
        stage_audits=tuple(audits),
        provider_name=provider.provider_name,
        model_name=provider.model_name,
        is_mock=provider.is_mock,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_estimated_cost=total_estimated_cost,
        pipeline_input_hash=pipeline_input_hash,
        pipeline_output_hash=pipeline_output_hash,
    )


__all__ = [
    "PipelineStageError",
    "AIPipelineExecutionInput",
    "StageAudit",
    "AIPipelineExecutionResult",
    "execute_pipeline",
]
