"""DB-destekli AI pipeline worker cekirdegi (Faz 3B.2B).

Bu modul, DB'de QUEUED durumdaki TEK bir AI pipeline stage'ini guvenli sekilde
isler: claim et -> girdileri authoritative DB kayitlarindan/onceki basarili
stage ciktilarindan hazirla -> ilgili `stage_runner.run_*` fonksiyonunu
calistir -> sonucu (CAS korumasiyla) kalicilastir -> DAG'de sonraki stage(ler)i
QUEUED olarak olustur.

KAPSAM DISI (bu faz): arq/cron entegrasyonu, sonsuz worker loop, otomatik
retry, stale RUNNING recovery, lease cleanup, chip muhasebesi, HTTP endpoint,
gercek OpenAI/Anthropic provider, paralel Stage 4 execution, migration/model
degisikligi, raw prompt/provider response saklama.

Transaction sinirlari (bkz. `app.services.simulation_worker` konvansiyonu):
- Transaction A (`claim_next_ai_stage`): stage'i `SELECT ... FOR UPDATE SKIP
  LOCKED` ile claim et, RUNNING'e gecir, `attempt_count`i artir; provider/
  runner CALISMADAN ONCE COMMIT et. Provider cagrisi sirasinda DB lock veya
  acik transaction TUTULMAZ.
- Transaction dISI: girdi hazirlama (kisa read), stage runner/provider
  execution.
- Transaction B (`persist_stage_success`/`persist_stage_failure`/iptal):
  stage'i yeniden kilitle, CAS dogrula (ayni stage id + status RUNNING +
  `attempt_count == claim'deki claimed_attempt_count` + pipeline hala
  calistirilabilir), sonucu yaz, successor(lar)i AYNI kisa transaction icinde
  atomik olustur, pipeline durumunu guncelle, COMMIT et.

Idempotency/hash TEK KAYNAK: worker, hicbir yerde ikinci bir hash/idempotency
algoritmasi YAZMAZ. QUEUED successor satirlarinin `input_hash`i ve deterministik
`idempotency_key`i, PAYLASILAN `stage_sources`/`stage_hashing` yardimcilariyla
uretilir; ayni yardimcilar stage calistirilirken de kullanilir, boylece
`stage_runner`in urettigi `audit.input_hash` QUEUED satirinkiyle BIREBIR eslesir
(persist'te dogrulanir).

Idempotency-key dogrulamasi (bkz. bilinen sinirlamalar): QUEUED satirlarin
`idempotency_key`i DAIMA deterministik/provider-bagimsiz (`deterministic_stage_
idempotency_key`) uretilir - bu, DAG dedup ve UNIQUE kisiti icin tek/kararli
anahtardir. Saf asamalarda (1/2/5) `stage_runner`in `audit.idempotency_key`i de
ayni deterministik degeri urettigi icin persist'te BIREBIR (strict) dogrulanir.
Provider asamalarinda (3/4/6) runner'in `audit.idempotency_key`i provider/model'e
BAGLIDIR (deterministik degil); bu nedenle bu asamalarda `input_hash` (strict)
dogrulanir ve QUEUED satirin deterministik idempotency_key'i degistirilmeden
korunur (audit'in provider/model/prompt metadata'si ayrica saklanir).
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.ai_pipeline import (
    AIPipelineRun,
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.personas import Persona
from app.models.simulations import SimulationRun
from app.services.ai_pipeline.hashing import hash_prompt_descriptor
from app.services.ai_pipeline.persistence import (
    build_stage2_manifest,
    decode_aggregation_result,
    decode_page_evidence,
    decode_persona_batch_manifest,
    decode_persona_behavior_batch,
    decode_scenario_interpretation,
    encode_stage_output,
    persona_context_from_row,
    rehydrate_persona_batches,
)
from app.services.ai_pipeline.prompts import get_prompt
from app.services.ai_pipeline.provider import AIProvider
from app.services.ai_pipeline.retry_policy import (
    MAX_STAGE_ATTEMPTS,
    STALE_REAP_BATCH_LIMIT,
    STALE_RUNNING_TIMEOUT_SECONDS,
)
from app.services.ai_pipeline.schemas import (
    AggregationResult,
    PageEvidence,
    PersonaBatch,
    PersonaBehaviorBatchOutput,
    PersonaContext,
    ScenarioInterpretation,
)
from app.services.ai_pipeline.stage_hashing import (
    deterministic_stage_idempotency_key,
    provider_stage_execution_idempotency_key,
)
from app.services.ai_pipeline.stage_inputs import BaselineMetricSnapshot, ModuleSummary
from app.services.ai_pipeline.stage_runner import (
    PipelineStageError,
    StageRunResult,
    run_aggregation_stage,
    run_batching_stage,
    run_evidence_stage,
    run_persona_behavior_batch,
    run_scenario_stage,
    run_ux_report_stage,
)
from app.services.ai_pipeline.stage_sources import (
    TaskContextSource,
    aggregation_stage_input_hash,
    batching_stage_input_hash,
    build_baseline_metrics,
    build_evidence_stage_source,
    build_module_summary,
    build_task_context_source,
    persona_behavior_stage_input_hash,
    scenario_stage_input_hash,
    ux_report_stage_input_hash,
)

# Provider cagirmayan saf asamalar (idempotency_key strict dogrulanir).
_PURE_STAGE_TYPES: frozenset[AIPipelineStageType] = frozenset(
    {
        AIPipelineStageType.EVIDENCE_PREPARATION,
        AIPipelineStageType.PERSONA_BATCH_PREPARATION,
        AIPipelineStageType.AGGREGATION,
    }
)

# Provider (LLM) cagiran asamalar.
_PROVIDER_STAGE_TYPES: frozenset[AIPipelineStageType] = frozenset(
    {
        AIPipelineStageType.SCENARIO_INTERPRETATION,
        AIPipelineStageType.PERSONA_BEHAVIOR,
        AIPipelineStageType.UX_REPORT,
    }
)

# Bir stage'in claim edilebilmesi icin bagli pipeline'in bu durumlarda olmasi
# gerekir (terminal/iptal DEGIL).
_RUNNABLE_PIPELINE_STATUSES: tuple[AIPipelineStatus, ...] = (
    AIPipelineStatus.QUEUED,
    AIPipelineStatus.RUNNING,
)

# Sanitize edilmis, kisa hata kodlari (raw mesaj/traceback DEGIL).
ERROR_PROVIDER_NOT_CONFIGURED = "provider_not_configured"
ERROR_STAGE_INPUT_HASH_MISMATCH = "stage_input_hash_mismatch"
ERROR_STAGE_IDEMPOTENCY_MISMATCH = "stage_idempotency_key_mismatch"
# Faz 3B.2C: execution-kimligi/pin uyusmazliklari + stale recovery kodlari.
ERROR_STAGE_EXECUTION_KEY_MISMATCH = "stage_execution_key_mismatch"
ERROR_PROVIDER_CONFIGURATION_MISMATCH = "provider_configuration_mismatch"
ERROR_STALE_EXECUTION_REQUEUED = "stale_execution_requeued"
ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED = "stale_execution_attempts_exhausted"
ERROR_STALE_EXECUTION_CANCELLED = "stale_execution_cancelled"


class AIPipelineWorkerError(Exception):
    """Worker katmanindaki kontrollu domain hatalarinin temel sinifi (saf
    Python istisnasi; `code` ileride guvenle disari verilebilecek kisa/sabit
    bir tanimlayicidir)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AIPipelineWorkerConfigurationError(AIPipelineWorkerError):
    """Provider gerektiren bir stage icin provider verilmediginde firlatilir."""


class ProviderConfigurationMismatchError(AIPipelineWorkerError):
    """Bir provider asamasi, pinlenen provider/model/execution key ile UYUSMAYAN
    bir provider/model ile (yani provider DEGISTIREN bir retry) calistirilmaya
    calisildiginda, provider cagrisindan ONCE firlatilir.

    Bu hata KONTROLLU ve TERMINALDIR (non-retryable): deneme sayisindan bagimsiz
    olarak stage/pipeline FAILED yapilir - asla yeniden kuyruga alinmaz (bkz.
    gorev talimati Part 3: "farkli provider/model ile retry ... RETRYABLE
    OLMAMALI")."""


def _now() -> datetime:
    return datetime.now(UTC)


def mark_stage_cancelled(stage: AIPipelineStage, *, now: datetime | None = None) -> None:
    """Bir NONTERMINAL stage'i (QUEUED/RUNNING) guvenli sekilde terminal CANCELLED
    yapar - TEK, PAYLASILAN cancel davranisi (worker reaper + mutasyon iptali ayni
    kodu kullanir; error_code kopyasi/tutarsizligi olmaz).

    Yazilan alanlar: `status=CANCELLED`, `error_code=stale_execution_cancelled`,
    `finished_at`. KORUNANLAR (dokunulmaz): `attempt_count`, `provider`,
    `model_name`, `execution_idempotency_key`/`idempotency_key`, `validated_output`,
    token/cost. Bir RUNNING stage'de normalde `validated_output` henuz yazilmamis
    olur (success persist ile ATOMIK yazilir); bu helper hicbir cikti yazmaz/silmez."""

    stage.status = AIPipelineStageStatus.CANCELLED
    stage.error_code = ERROR_STALE_EXECUTION_CANCELLED
    stage.finished_at = now or _now()


# --- process_one sonuc tipi (Part 10) --------------------------------------------


class AIStageOutcome(str, enum.Enum):
    """`process_one_ai_stage`in ayirt edilebilir sonuc durumlari.

    Faz 3B.2C'de ADDITIVE olarak `RETRY_SCHEDULED` ve
    `PROVIDER_CONFIGURATION_MISMATCH` eklendi (mevcut degerler korunur)."""

    NO_WORK = "no_work"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    STALE_RESULT_REJECTED = "stale_result_rejected"
    PROVIDER_CONFIGURATION_MISMATCH = "provider_configuration_mismatch"


@dataclass(frozen=True)
class AIStageProcessResult:
    outcome: AIStageOutcome
    pipeline_id: uuid.UUID | None = None
    stage_id: uuid.UUID | None = None
    stage_type: AIPipelineStageType | None = None
    batch_index: int | None = None
    error_code: str | None = None
    pipeline_completed: bool = False


# --- Claim sonucu (immutable, transaction disina tasinan guvenli deger) ----------


@dataclass(frozen=True)
class ClaimedAIStage:
    """Claim transaction'inda alinan, ORM'den BAGIMSIZ immutable claim degeri.

    `claimed_attempt_count`, persist sirasinda lease/CAS token'i olarak
    kullanilir.

    `claimed_manual_retry_count` (Faz 3C.3) - claim ANINDAKI pipeline manuel
    retry JENERASYONUDUR (`AIPipelineRun.manual_retry_count`). persist/pin/CAS
    yollarinda (bkz. `_relock_for_persist`) lease kimliginin bir parcasi olarak
    dogrulanir: manuel retry stage'i QUEUED'a dondurup `attempt_count`i 0'a
    sifirladiktan sonra yeni bir worker AYNI `attempt_count` degerine ulasabilir
    (ABA); jenerasyon eslesmesi, eski jenerasyona ait GEC gelen bir sonucun yeni
    execution gibi kabul edilmesini onler. Prompt/input hash'ine ASLA girmez -
    bu bir lease token'idir, icerik degil."""

    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    stage_type: AIPipelineStageType
    batch_index: int | None
    claimed_attempt_count: int
    claimed_manual_retry_count: int
    simulation_run_id: uuid.UUID
    organization_id: uuid.UUID


# --- Stage execution plani (girdi hazirlama ciktisi, saf DTO'lar) ----------------


@dataclass(frozen=True)
class StageExecutionPlan:
    cancelled: bool = False
    source_type: str = ""
    metrics: dict[str, object] = field(default_factory=dict)
    page_features: dict[str, object] | None = None
    selected_modules: tuple[str, ...] = ()
    module_results: dict[str, object] | None = None
    personas: tuple[PersonaContext, ...] = ()
    evidence: PageEvidence | None = None
    evidence_output_hash: str | None = None
    scenario: ScenarioInterpretation | None = None
    batch: PersonaBatch | None = None
    behavior_outputs: tuple[PersonaBehaviorBatchOutput, ...] = ()
    aggregation: AggregationResult | None = None
    baseline_metrics: tuple[BaselineMetricSnapshot, ...] = ()
    module_summary: tuple[ModuleSummary, ...] = ()
    task_context: TaskContextSource | None = None


# --- DB read yardimcilari --------------------------------------------------------


async def _load_personas(session: AsyncSession, simulation_run_id: uuid.UUID) -> list[Persona]:
    stmt = select(Persona).where(Persona.simulation_run_id == simulation_run_id).order_by(Persona.index)
    return list((await session.execute(stmt)).scalars().all())


async def _persona_contexts(
    session: AsyncSession, simulation_run_id: uuid.UUID
) -> tuple[PersonaContext, ...]:
    rows = await _load_personas(session, simulation_run_id)
    return tuple(persona_context_from_row(row) for row in rows)


async def _succeeded_stage(
    session: AsyncSession, pipeline_id: uuid.UUID, stage_type: AIPipelineStageType
) -> AIPipelineStage | None:
    """Batch'lenmeyen bir asamanin TEK basarili satirini dondurur (yoksa None)."""

    stmt = select(AIPipelineStage).where(
        AIPipelineStage.ai_pipeline_run_id == pipeline_id,
        AIPipelineStage.stage_type == stage_type,
        AIPipelineStage.status == AIPipelineStageStatus.SUCCEEDED,
    )
    return (await session.execute(stmt)).scalars().first()


async def _succeeded_behavior_stages(session: AsyncSession, pipeline_id: uuid.UUID) -> list[AIPipelineStage]:
    """Tum basarili PERSONA_BEHAVIOR satirlarini `batch_index` ASC sirada dondurur."""

    stmt = (
        select(AIPipelineStage)
        .where(
            AIPipelineStage.ai_pipeline_run_id == pipeline_id,
            AIPipelineStage.stage_type == AIPipelineStageType.PERSONA_BEHAVIOR,
            AIPipelineStage.status == AIPipelineStageStatus.SUCCEEDED,
        )
        .order_by(AIPipelineStage.batch_index)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _require_succeeded_output(
    session: AsyncSession, pipeline_id: uuid.UUID, stage_type: AIPipelineStageType
) -> object:
    stage = await _succeeded_stage(session, pipeline_id, stage_type)
    if stage is None or stage.validated_output is None:
        raise AIPipelineWorkerError(
            "missing_dependency_output",
            f"beklenen basarili {stage_type.value} ciktisi bulunamadi",
        )
    return stage.validated_output


# --- 1) Claim (Transaction A) ----------------------------------------------------


async def claim_next_ai_stage(session: AsyncSession) -> ClaimedAIStage | None:
    """Sıradaki calistirilabilir QUEUED stage'i `FOR UPDATE SKIP LOCKED` ile
    claim eder; RUNNING'e gecirir, `attempt_count`i artirir, `started_at`i
    doldurur ve pipeline QUEUED ise RUNNING'e cevirir.

    Deterministik siralama: pipeline `created_at` -> stage `created_at` ->
    stage `id`. Yalnizca STAGE satiri kilitlenir (`of=AIPipelineStage`);
    pipeline satirina bu asamada kilit alinmaz.

    NOT (tenant): worker tenant-facing bir giris noktasi DEGILDIR - DISARIDAN
    hicbir org/id parametresi almaz; yalnizca DB'de kendi claim ettigi internal
    id'lerle ilerler ve TUM organizasyonlar genelinde (yalnizca internal id ile)
    claim eder. Cagirana (`process_one_ai_stage`) da harici bir org girdisi
    verilmez.
    """

    stmt = (
        select(AIPipelineStage)
        .join(AIPipelineRun, AIPipelineStage.ai_pipeline_run_id == AIPipelineRun.id)
        .where(
            AIPipelineStage.status == AIPipelineStageStatus.QUEUED,
            AIPipelineRun.status.in_(_RUNNABLE_PIPELINE_STATUSES),
            AIPipelineRun.cancel_requested.is_(False),
        )
        .order_by(AIPipelineRun.created_at, AIPipelineStage.created_at, AIPipelineStage.id)
        .limit(1)
        .with_for_update(skip_locked=True, of=AIPipelineStage)
    )
    stage = (await session.execute(stmt)).scalars().first()
    if stage is None:
        return None

    pipeline = await session.get(AIPipelineRun, stage.ai_pipeline_run_id)
    if pipeline is None:
        return None

    stage.status = AIPipelineStageStatus.RUNNING
    stage.attempt_count += 1
    stage.started_at = _now()

    if pipeline.status == AIPipelineStatus.QUEUED:
        pipeline.status = AIPipelineStatus.RUNNING
        if pipeline.started_at is None:
            pipeline.started_at = _now()

    await session.flush()

    return ClaimedAIStage(
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        stage_type=stage.stage_type,
        batch_index=stage.batch_index,
        claimed_attempt_count=stage.attempt_count,
        # Jenerasyon lease token'i: claim aninda pipeline satiri zaten okundugu
        # icin ek sorgu gerekmez (bkz. `claimed_manual_retry_count` dokstring'i).
        claimed_manual_retry_count=pipeline.manual_retry_count,
        simulation_run_id=pipeline.simulation_run_id,
        organization_id=pipeline.organization_id,
    )


# --- 2) Girdi hazirlama + iptal kontrolu (transaction disi kisa read'ler) --------


async def _is_cancelled(session: AsyncSession, claimed: ClaimedAIStage) -> bool:
    pipeline = await session.get(AIPipelineRun, claimed.pipeline_id)
    if pipeline is None:
        return True
    if pipeline.cancel_requested or pipeline.status == AIPipelineStatus.CANCELLED:
        return True
    run = await session.get(SimulationRun, claimed.simulation_run_id)
    if run is not None and run.cancel_requested:
        return True
    return False


async def _prepare_stage_execution(session: AsyncSession, claimed: ClaimedAIStage) -> StageExecutionPlan:
    """Provider cagrisindan ONCE: iptal kontrolu + stage girdilerini
    authoritative DB kayitlarindan/onceki basarili stage ciktilarindan kurar."""

    if await _is_cancelled(session, claimed):
        return StageExecutionPlan(cancelled=True)

    run = await session.get(SimulationRun, claimed.simulation_run_id)
    if run is None:
        raise AIPipelineWorkerError("simulation_run_not_found", "bagli SimulationRun bulunamadi")

    pipeline_id = claimed.pipeline_id
    stage_type = claimed.stage_type

    if stage_type == AIPipelineStageType.EVIDENCE_PREPARATION:
        source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
        return StageExecutionPlan(
            source_type=source.source_type,
            metrics=source.metrics,
            page_features=source.page_features,
            selected_modules=source.selected_modules,
            module_results=source.module_results,
        )

    if stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION:
        evidence_stage = await _succeeded_stage(
            session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION
        )
        if evidence_stage is None or evidence_stage.output_hash is None:
            raise AIPipelineWorkerError(
                "missing_dependency_output", "Stage 1 evidence output_hash bulunamadi"
            )
        personas = await _persona_contexts(session, claimed.simulation_run_id)
        return StageExecutionPlan(
            personas=personas,
            evidence_output_hash=evidence_stage.output_hash,
        )

    if stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION:
        evidence = decode_page_evidence(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION)
        )
        return StageExecutionPlan(
            evidence=evidence,
            task_context=build_task_context_source(input_snapshot=run.input_snapshot),
        )

    if stage_type == AIPipelineStageType.PERSONA_BEHAVIOR:
        evidence = decode_page_evidence(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION)
        )
        scenario = decode_scenario_interpretation(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.SCENARIO_INTERPRETATION)
        )
        manifest = decode_persona_batch_manifest(
            await _require_succeeded_output(
                session, pipeline_id, AIPipelineStageType.PERSONA_BATCH_PREPARATION
            )
        )
        persona_rows = await _load_personas(session, claimed.simulation_run_id)
        batches = rehydrate_persona_batches(manifest, persona_rows)
        target_batch = next((b for b in batches if b.batch_index == claimed.batch_index), None)
        if target_batch is None:
            raise AIPipelineWorkerError(
                "missing_dependency_output",
                f"batch_index {claimed.batch_index} icin PersonaBatch bulunamadi",
            )
        return StageExecutionPlan(
            evidence=evidence,
            scenario=scenario,
            batch=target_batch,
            baseline_metrics=build_baseline_metrics(result=run.result),
        )

    if stage_type == AIPipelineStageType.AGGREGATION:
        scenario = decode_scenario_interpretation(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.SCENARIO_INTERPRETATION)
        )
        behavior_stages = await _succeeded_behavior_stages(session, pipeline_id)
        behavior_outputs = tuple(
            decode_persona_behavior_batch(stage.validated_output) for stage in behavior_stages
        )
        personas = await _persona_contexts(session, claimed.simulation_run_id)
        return StageExecutionPlan(
            personas=personas,
            behavior_outputs=behavior_outputs,
            scenario=scenario,
        )

    if stage_type == AIPipelineStageType.UX_REPORT:
        evidence = decode_page_evidence(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION)
        )
        aggregation = decode_aggregation_result(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.AGGREGATION)
        )
        return StageExecutionPlan(
            evidence=evidence,
            aggregation=aggregation,
            baseline_metrics=build_baseline_metrics(result=run.result),
            module_summary=build_module_summary(input_snapshot=run.input_snapshot),
            task_context=build_task_context_source(input_snapshot=run.input_snapshot),
        )

    raise AIPipelineWorkerError("unknown_stage_type", f"bilinmeyen stage_type: {stage_type!r}")


def _require_provider(provider: AIProvider | None, stage_type: AIPipelineStageType) -> AIProvider:
    if provider is None:
        raise AIPipelineWorkerConfigurationError(
            ERROR_PROVIDER_NOT_CONFIGURED,
            f"{stage_type.value} bir provider gerektirir ama provider verilmedi",
        )
    return provider


# --- 2.5) Provider/model pinning (kisa transaction, provider cagrisindan ONCE) ---


class _PinResult(enum.Enum):
    """`_pin_provider_execution`in ayirt edilebilir sonuclari (provider cagrisi
    yapilmadan ONCE):

    - `OK`: provider/model/execution key ILK kez pinlendi ya da mevcut pin ile
      (retry) UYUSTU -> provider cagrilabilir.
    - `STALE`: CAS basarisiz (stage status/attempt/pipeline uyusmuyor; late/stale
      claim) -> provider CAGRILMAZ, sonuc `STALE_RESULT_REJECTED` olur.
    - `MISMATCH`: satir zaten FARKLI bir provider/model/execution key ile pinli
      (provider DEGISTIREN retry) -> provider CAGRILMADAN terminal (non-retryable)
      FAILED (`provider_configuration_mismatch`)."""

    OK = "ok"
    STALE = "stale"
    MISMATCH = "mismatch"


def _compute_provider_execution_key(claimed: ClaimedAIStage, *, provider: AIProvider, input_hash: str) -> str:
    """Bir provider asamasinin execution idempotency key'ini, `stage_runner`in
    basarili sonucta uretecegi `audit.idempotency_key` ile BIREBIR ayni
    PAYLASILAN yardimciyla (tek hash kaynagi) hesaplar.

    `input_hash`, QUEUED satirin DB'deki (successor olusturulurken PAYLASILAN
    `*_stage_input_hash` yardimcisiyla yazilmis) `input_hash`idir; prompt
    metadata'si merkezi `prompts.get_prompt`tan gelir; provider/model provider
    sozlesmesinin `provider_name`/`model_name` alanlarindan (reflection/string
    parsing YOK)."""

    prompt = get_prompt(claimed.stage_type)
    return provider_stage_execution_idempotency_key(
        simulation_run_id=claimed.simulation_run_id,
        stage_type=claimed.stage_type,
        batch_index=claimed.batch_index,
        prompt_version=prompt.prompt_version,
        prompt_hash=hash_prompt_descriptor(prompt),
        provider=provider.provider_name,
        model_name=provider.model_name,
        input_hash=input_hash,
        provider_configuration_fingerprint=provider.configuration_fingerprint,
    )


async def _pin_provider_execution(
    session_maker: async_sessionmaker[AsyncSession],
    claimed: ClaimedAIStage,
    *,
    provider: AIProvider,
) -> _PinResult:
    """Provider asamasini ILK kez calistirmadan HEMEN ONCE, kisa/ayri bir
    transaction icinde provider adi + model_name + execution_idempotency_key'i
    stage satirina PINLER ve COMMIT eder (provider cagrisindan ONCE).

    Donus (bkz. `_PinResult`): `OK` -> pin edildi (ilk) ya da mevcut pin ile
    uyustu (retry), provider cagrilabilir; `STALE` -> CAS basarisiz (attempt/
    status/pipeline uyusmuyor; late/stale claim), provider CAGRILMAZ; `MISMATCH`
    -> satir FARKLI bir provider/model/execution key ile pinli (provider
    DEGISTIREN retry), provider CAGRILMADAN ONCE kontrollu/terminal.

    Retry sirasinda (satir zaten pinliyse) AYNI provider/model/execution key
    beklenir; farkli provider/model ile retry istenirse (provider DEGISTIREN
    retry) provider CAGRILMADAN ONCE `MISMATCH` doner - cagiran bunu terminal
    (non-retryable) FAILED'a cevirir (bkz. `ProviderConfigurationMismatchError`
    dokstring'i)."""

    async with session_maker() as session:
        locked = await _relock_for_persist(session, claimed, require_runnable_pipeline=True)
        if locked is None:
            await session.rollback()
            return _PinResult.STALE
        stage, _pipeline = locked

        execution_key = _compute_provider_execution_key(
            claimed, provider=provider, input_hash=stage.input_hash
        )

        if stage.execution_idempotency_key is None:
            # Ilk execution: provider/model/execution key/config fingerprint PINLENIR.
            stage.provider = provider.provider_name
            stage.model_name = provider.model_name
            stage.execution_idempotency_key = execution_key
            stage.provider_configuration_hash = provider.configuration_fingerprint
            await session.commit()
            return _PinResult.OK

        # Retry: pinlenen kimlik DEGISMEMELI. Provider DEGISTIREN retry reddedilir.
        if (
            stage.provider != provider.provider_name
            or stage.model_name != provider.model_name
            or stage.execution_idempotency_key != execution_key
            or stage.provider_configuration_hash != provider.configuration_fingerprint
        ):
            await session.rollback()
            return _PinResult.MISMATCH
        await session.commit()
        return _PinResult.OK


# --- 3) Execution (transaction disi) ---------------------------------------------


async def execute_claimed_ai_stage(
    claimed: ClaimedAIStage,
    plan: StageExecutionPlan,
    *,
    provider: AIProvider | None,
) -> StageRunResult:
    """Claim edilen stage'i, hazirlanmis plan ile ilgili `run_*` fonksiyonuna
    devrederek calistirir. Provider gerektiren asamalarda provider ACIK olarak
    zorunludur (asla Mock'a dusulmez)."""

    stage_type = claimed.stage_type
    run_id = claimed.simulation_run_id

    if stage_type == AIPipelineStageType.EVIDENCE_PREPARATION:
        return run_evidence_stage(
            simulation_run_id=run_id,
            source_type=plan.source_type,
            metrics=plan.metrics,
            page_features=plan.page_features,
            selected_modules=plan.selected_modules,
            module_results=plan.module_results,
        )

    if stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION:
        assert plan.evidence_output_hash is not None
        return run_batching_stage(
            simulation_run_id=run_id,
            personas=plan.personas,
            evidence_output_hash=plan.evidence_output_hash,
        )

    if stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION:
        assert plan.evidence is not None and plan.task_context is not None
        real_provider = _require_provider(provider, stage_type)
        return await run_scenario_stage(
            simulation_run_id=run_id,
            provider=real_provider,
            evidence=plan.evidence,
            target_task=plan.task_context.target_task,
            test_name=plan.task_context.test_name,
            test_description=plan.task_context.test_description,
            methodology_context=plan.task_context.methodology_context,
        )

    if stage_type == AIPipelineStageType.PERSONA_BEHAVIOR:
        assert plan.evidence is not None and plan.scenario is not None and plan.batch is not None
        real_provider = _require_provider(provider, stage_type)
        return await run_persona_behavior_batch(
            simulation_run_id=run_id,
            provider=real_provider,
            batch=plan.batch,
            evidence=plan.evidence,
            scenario=plan.scenario,
            baseline_metrics=plan.baseline_metrics,
        )

    if stage_type == AIPipelineStageType.AGGREGATION:
        assert plan.scenario is not None
        return run_aggregation_stage(
            simulation_run_id=run_id,
            personas=plan.personas,
            behavior_outputs=plan.behavior_outputs,
            scenario=plan.scenario,
        )

    if stage_type == AIPipelineStageType.UX_REPORT:
        assert plan.evidence is not None and plan.aggregation is not None and plan.task_context is not None
        real_provider = _require_provider(provider, stage_type)
        return await run_ux_report_stage(
            simulation_run_id=run_id,
            provider=real_provider,
            evidence=plan.evidence,
            baseline_metrics=plan.baseline_metrics,
            aggregation=plan.aggregation,
            module_summary=plan.module_summary,
            methodology_context=plan.task_context.methodology_context,
        )

    raise AIPipelineWorkerError("unknown_stage_type", f"bilinmeyen stage_type: {stage_type!r}")


def _encode_validated_output(stage_type: AIPipelineStageType, run_result: StageRunResult) -> dict:
    """Runner ciktisini persist edilecek `validated_output`a cevirir. Stage 2
    icin MINIMAL manifest (attributes'siz) yazilir; digerlerinde codec kullanilir."""

    if stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION:
        manifest = build_stage2_manifest(run_result.output)
        return encode_stage_output(stage_type, manifest)
    return encode_stage_output(stage_type, run_result.output)


# --- CAS re-lock yardimcisi ------------------------------------------------------


async def _relock_for_persist(
    session: AsyncSession, claimed: ClaimedAIStage, *, require_runnable_pipeline: bool
) -> tuple[AIPipelineStage, AIPipelineRun] | None:
    """Persist oncesi stage + pipeline'i yeniden kilitler ve CAS'i dogrular.

    CAS: ayni stage id, mevcut status RUNNING, `attempt_count == claimed_
    attempt_count`, pipeline hala ayni VE pipeline manuel retry jenerasyonu
    claim anindakiyle AYNI (`manual_retry_count == claimed_manual_retry_count`).
    `require_runnable_pipeline` True ise pipeline ayrica calistirilabilir
    (terminal/iptal DEGIL) olmalidir.

    Jenerasyon CAS'i (Faz 3C.3): manuel retry `attempt_count`i 0'a sifirladigi
    icin yalnizca `attempt_count` yetmez (ABA); jenerasyon uyusmazsa eski
    execution'in GEC sonucu reddedilir (STALE) - cikti/token/successor yazilmaz.
    Bu kontrol `require_runnable_pipeline`den BAGIMSIZ olarak HER yolda (basari/
    retry/iptal/pin) uygulanir cunku pipeline satiri her durumda burada okunur.

    Kilitleme SIRASI daima STAGE -> PIPELINE (deadlock'tan kacinmak icin sabit)."""

    stage = (
        await session.execute(
            select(AIPipelineStage).where(AIPipelineStage.id == claimed.stage_id).with_for_update()
        )
    ).scalar_one_or_none()
    if stage is None:
        return None
    if (
        stage.status != AIPipelineStageStatus.RUNNING
        or stage.attempt_count != claimed.claimed_attempt_count
        or stage.ai_pipeline_run_id != claimed.pipeline_id
    ):
        return None

    pipeline = (
        await session.execute(
            select(AIPipelineRun).where(AIPipelineRun.id == claimed.pipeline_id).with_for_update()
        )
    ).scalar_one_or_none()
    if pipeline is None:
        return None
    # Jenerasyon CAS (Faz 3C.3): manuel retry lease token'i. HER yolda dogrulanir
    # (require_runnable_pipeline'den bagimsiz) - eski jenerasyon sonucu STALE.
    if pipeline.manual_retry_count != claimed.claimed_manual_retry_count:
        return None
    if require_runnable_pipeline and (
        pipeline.status not in _RUNNABLE_PIPELINE_STATUSES or pipeline.cancel_requested
    ):
        return None

    return stage, pipeline


# --- 4) Success persistence + DAG successor (Transaction B) ----------------------


async def persist_stage_success(
    session_maker: async_sessionmaker[AsyncSession],
    claimed: ClaimedAIStage,
    run_result: StageRunResult,
    *,
    provider: AIProvider | None,
) -> AIStageProcessResult:
    """Basarili stage'i kisa, ayri bir transaction icinde CAS korumasiyla yazar
    ve successor(lar)i AYNI transaction icinde atomik olusturur."""

    audit = run_result.audit
    stage_type = claimed.stage_type

    async with session_maker() as session:
        locked = await _relock_for_persist(session, claimed, require_runnable_pipeline=True)
        if locked is None:
            await session.rollback()
            return AIStageProcessResult(
                outcome=AIStageOutcome.STALE_RESULT_REJECTED,
                pipeline_id=claimed.pipeline_id,
                stage_id=claimed.stage_id,
                stage_type=stage_type,
                batch_index=claimed.batch_index,
            )
        stage, pipeline = locked

        # Hash/idempotency dogrulamasi (PAYLASILAN kaynaktan). input_hash TUM
        # asamalarda strict eslesmeli. Saf asamalarda idempotency_key de strict
        # eslesir; provider asamalarinda runner key'i provider-bagimli oldugu
        # icin idempotency_key karsilastirilmaz (bkz. modul dokstring'i).
        if audit.input_hash != stage.input_hash:
            return await _write_failure(session, stage, pipeline, error_code=ERROR_STAGE_INPUT_HASH_MISMATCH)
        if stage_type in _PURE_STAGE_TYPES and audit.idempotency_key != stage.idempotency_key:
            return await _write_failure(session, stage, pipeline, error_code=ERROR_STAGE_IDEMPOTENCY_MISMATCH)
        # Provider asamalarinda: ONCEDEN pinlenen execution kimligi (provider/
        # model/execution_idempotency_key) ile audit BIREBIR eslesmeli (Faz
        # 3B.2C - "pinned input/execution identity" CAS kontrolu). Uyusmazlik
        # integrity failure sayilir: cikti/successor YAZILMAZ, stage FAILED.
        if (
            stage_type in _PROVIDER_STAGE_TYPES
            and stage.execution_idempotency_key is not None
            and (
                stage.execution_idempotency_key != audit.idempotency_key
                or stage.provider != audit.provider
                or stage.model_name != audit.model_name
                or stage.provider_configuration_hash != audit.provider_configuration_hash
            )
        ):
            return await _write_failure(
                session, stage, pipeline, error_code=ERROR_STAGE_EXECUTION_KEY_MISMATCH
            )

        validated_output = _encode_validated_output(stage_type, run_result)

        stage.status = AIPipelineStageStatus.SUCCEEDED
        stage.validated_output = validated_output
        stage.output_hash = audit.output_hash
        stage.prompt_hash = audit.prompt_hash
        stage.prompt_version = audit.prompt_version
        stage.provider = audit.provider
        stage.model_name = audit.model_name
        stage.provider_configuration_hash = audit.provider_configuration_hash
        # Logical `idempotency_key`den AYRI: gercek execution key'i persist edilir
        # (saf asamalarda logical key ile ayni deterministik deger; provider
        # asamalarinda pinlenen key ile ayni). Bkz. kolon dokstring'i.
        stage.execution_idempotency_key = audit.idempotency_key
        stage.input_tokens = audit.input_tokens
        stage.output_tokens = audit.output_tokens
        stage.estimated_cost = audit.estimated_cost
        stage.error_code = None
        stage.finished_at = _now()

        pipeline.total_input_tokens += audit.input_tokens
        pipeline.total_output_tokens += audit.output_tokens
        if audit.estimated_cost is not None:
            pipeline.estimated_cost = (pipeline.estimated_cost or 0.0) + audit.estimated_cost

        await session.flush()

        pipeline_completed = await enqueue_ready_successors(
            session, claimed=claimed, pipeline=pipeline, run_result=run_result
        )

        await session.commit()
        return AIStageProcessResult(
            outcome=AIStageOutcome.SUCCEEDED,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            stage_type=stage_type,
            batch_index=claimed.batch_index,
            pipeline_completed=pipeline_completed,
        )


async def _write_failure(
    session: AsyncSession,
    stage: AIPipelineStage,
    pipeline: AIPipelineRun,
    *,
    error_code: str,
) -> AIStageProcessResult:
    """Kilitli stage/pipeline uzerinde (successor OLUSTURMADAN) basarisizligi
    yazar ve pipeline'i FAILED yapar."""

    stage.status = AIPipelineStageStatus.FAILED
    stage.error_code = error_code
    stage.finished_at = _now()
    pipeline.status = AIPipelineStatus.FAILED
    pipeline.finished_at = _now()
    await session.commit()
    return AIStageProcessResult(
        outcome=AIStageOutcome.FAILED,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        stage_type=stage.stage_type,
        batch_index=stage.batch_index,
        error_code=error_code,
    )


async def persist_stage_failure(
    session_maker: async_sessionmaker[AsyncSession],
    claimed: ClaimedAIStage,
    *,
    error_code: str,
) -> AIStageProcessResult:
    """Runner/provider/validation/config hatasini CAS korumasiyla yazar; stage
    FAILED, pipeline FAILED; successor OLUSTURULMAZ; SimulationRun/Report
    DEGISTIRILMEZ."""

    async with session_maker() as session:
        locked = await _relock_for_persist(session, claimed, require_runnable_pipeline=False)
        if locked is None:
            await session.rollback()
            return AIStageProcessResult(
                outcome=AIStageOutcome.STALE_RESULT_REJECTED,
                pipeline_id=claimed.pipeline_id,
                stage_id=claimed.stage_id,
                stage_type=claimed.stage_type,
                batch_index=claimed.batch_index,
            )
        stage, pipeline = locked
        return await _write_failure(session, stage, pipeline, error_code=error_code)


async def persist_stage_retry(
    session_maker: async_sessionmaker[AsyncSession],
    claimed: ClaimedAIStage,
    *,
    error_code: str,
) -> AIStageProcessResult:
    """Retryable bir hatayi (ve `attempt_count < MAX_STAGE_ATTEMPTS`) CAS
    korumasiyla RUNNING->QUEUED yaparak yeniden kuyruga alir.

    KORUNANLAR (spec Part 5): pipeline RUNNING kalir; successor OLUSTURULMAZ;
    `attempt_count` DEGISTIRILMEZ/sifirlanmaz (sonraki claim'de artacak); logical
    `idempotency_key`, `execution_idempotency_key` ve provider/model pinleri
    DEGISMEZ (dokunulmaz); `finished_at` terminal basari gibi AYARLANMAZ.
    Yalnizca `status` QUEUED'a doner ve sanitize `error_code` saklanir.

    CAS basarisizsa (attempt/status/pipeline uyusmuyor) late/stale sonuc gibi
    reddedilir (`STALE_RESULT_REJECTED`) - o durumda satir baska bir jenerasyon
    tarafindan zaten ilerletilmis demektir."""

    async with session_maker() as session:
        locked = await _relock_for_persist(session, claimed, require_runnable_pipeline=True)
        if locked is None:
            await session.rollback()
            return AIStageProcessResult(
                outcome=AIStageOutcome.STALE_RESULT_REJECTED,
                pipeline_id=claimed.pipeline_id,
                stage_id=claimed.stage_id,
                stage_type=claimed.stage_type,
                batch_index=claimed.batch_index,
            )
        stage, _pipeline = locked
        stage.status = AIPipelineStageStatus.QUEUED
        stage.error_code = error_code
        # attempt_count, keys, pins, finished_at, started_at: DOKUNULMAZ.
        await session.commit()
        return AIStageProcessResult(
            outcome=AIStageOutcome.RETRY_SCHEDULED,
            pipeline_id=claimed.pipeline_id,
            stage_id=claimed.stage_id,
            stage_type=claimed.stage_type,
            batch_index=claimed.batch_index,
            error_code=error_code,
        )


async def _persist_cancellation(
    session_maker: async_sessionmaker[AsyncSession], claimed: ClaimedAIStage
) -> AIStageProcessResult:
    """Iptal edilmis bir stage'i CAS korumasiyla CANCELLED yapar ve pipeline'i
    CANCELLED yapar; provider CAGRILMAZ, successor OLUSTURULMAZ."""

    async with session_maker() as session:
        locked = await _relock_for_persist(session, claimed, require_runnable_pipeline=False)
        if locked is None:
            await session.rollback()
            return AIStageProcessResult(
                outcome=AIStageOutcome.STALE_RESULT_REJECTED,
                pipeline_id=claimed.pipeline_id,
                stage_id=claimed.stage_id,
                stage_type=claimed.stage_type,
                batch_index=claimed.batch_index,
            )
        stage, pipeline = locked
        stage.status = AIPipelineStageStatus.CANCELLED
        stage.finished_at = _now()
        pipeline.status = AIPipelineStatus.CANCELLED
        pipeline.finished_at = _now()
        await session.commit()
        return AIStageProcessResult(
            outcome=AIStageOutcome.CANCELLED,
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            stage_type=stage.stage_type,
            batch_index=stage.batch_index,
        )


# --- 5) DAG successor / fan-out / fan-in (Transaction B icinde) ------------------


def _make_queued_stage(
    *,
    simulation_run_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    stage_type: AIPipelineStageType,
    input_hash: str,
    batch_index: int | None = None,
) -> AIPipelineStage:
    """Deterministik, provider-bagimsiz `idempotency_key` ile bir QUEUED
    successor satiri kurar (henuz ADD/FLUSH edilmez)."""

    idempotency_key = deterministic_stage_idempotency_key(
        simulation_run_id=simulation_run_id,
        stage_type=stage_type,
        input_hash=input_hash,
        batch_index=batch_index,
    )
    return AIPipelineStage(
        ai_pipeline_run_id=pipeline_id,
        stage_type=stage_type,
        batch_index=batch_index,
        status=AIPipelineStageStatus.QUEUED,
        attempt_count=0,
        input_hash=input_hash,
        idempotency_key=idempotency_key,
    )


async def _add_successor(session: AsyncSession, stage: AIPipelineStage) -> None:
    """Successor'u SAVEPOINT icinde ekler; `idempotency_key` UNIQUE ihlali
    (es-zamanli/duplicate olusturma) dis transaction'i bozmadan yutulur -
    aynen `orchestration.initialize_ai_pipeline`in create-race deseni gibi."""

    try:
        async with session.begin_nested():
            session.add(stage)
            await session.flush()
    except IntegrityError:
        # Zaten olusturulmus (UNIQUE idempotency_key) - guvenle atlanir.
        pass


async def enqueue_ready_successors(
    session: AsyncSession,
    *,
    claimed: ClaimedAIStage,
    pipeline: AIPipelineRun,
    run_result: StageRunResult,
) -> bool:
    """Basari sonrasi DAG successor(lar)ini AYNI transaction icinde olusturur.
    UX_REPORT basarisinda pipeline'i SUCCEEDED yapar ve `True` dondurur (aksi
    halde `False`).

    DAG:
      EVIDENCE_PREPARATION      -> 1 PERSONA_BATCH_PREPARATION
      PERSONA_BATCH_PREPARATION -> 1 SCENARIO_INTERPRETATION
      SCENARIO_INTERPRETATION   -> her batch icin 1 PERSONA_BEHAVIOR (0..N-1)
      PERSONA_BEHAVIOR          -> (fan-in) hepsi SUCCEEDED ise 1 AGGREGATION
      AGGREGATION               -> 1 UX_REPORT
      UX_REPORT                 -> pipeline SUCCEEDED
    """

    stage_type = claimed.stage_type
    pipeline_id = pipeline.id
    run_id = claimed.simulation_run_id

    run = await session.get(SimulationRun, run_id)
    input_snapshot = run.input_snapshot if run is not None else {}
    result = run.result if run is not None else {}

    if stage_type == AIPipelineStageType.EVIDENCE_PREPARATION:
        input_hash = batching_stage_input_hash(evidence_output_hash=run_result.audit.output_hash)
        await _add_successor(
            session,
            _make_queued_stage(
                simulation_run_id=run_id,
                pipeline_id=pipeline_id,
                stage_type=AIPipelineStageType.PERSONA_BATCH_PREPARATION,
                input_hash=input_hash,
            ),
        )
        return False

    if stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION:
        evidence = decode_page_evidence(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION)
        )
        task_context = build_task_context_source(input_snapshot=input_snapshot)
        input_hash = scenario_stage_input_hash(evidence=evidence, task_context=task_context)
        await _add_successor(
            session,
            _make_queued_stage(
                simulation_run_id=run_id,
                pipeline_id=pipeline_id,
                stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
                input_hash=input_hash,
            ),
        )
        return False

    if stage_type == AIPipelineStageType.SCENARIO_INTERPRETATION:
        scenario = run_result.output
        assert isinstance(scenario, ScenarioInterpretation)
        evidence = decode_page_evidence(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION)
        )
        manifest = decode_persona_batch_manifest(
            await _require_succeeded_output(
                session, pipeline_id, AIPipelineStageType.PERSONA_BATCH_PREPARATION
            )
        )
        persona_rows = await _load_personas(session, run_id)
        batches = rehydrate_persona_batches(manifest, persona_rows)
        baseline_metrics = build_baseline_metrics(result=result)
        for batch in sorted(batches, key=lambda b: b.batch_index):
            input_hash = persona_behavior_stage_input_hash(
                batch=batch,
                evidence=evidence,
                scenario=scenario,
                baseline_metrics=baseline_metrics,
            )
            await _add_successor(
                session,
                _make_queued_stage(
                    simulation_run_id=run_id,
                    pipeline_id=pipeline_id,
                    stage_type=AIPipelineStageType.PERSONA_BEHAVIOR,
                    input_hash=input_hash,
                    batch_index=batch.batch_index,
                ),
            )
        return False

    if stage_type == AIPipelineStageType.PERSONA_BEHAVIOR:
        manifest = decode_persona_batch_manifest(
            await _require_succeeded_output(
                session, pipeline_id, AIPipelineStageType.PERSONA_BATCH_PREPARATION
            )
        )
        behavior_stages = await _succeeded_behavior_stages(session, pipeline_id)
        # Fan-in: tum behavior batch'leri SUCCEEDED olmadan AGGREGATION olusmaz.
        if len(behavior_stages) < manifest.batch_count:
            return False
        behavior_outputs = tuple(decode_persona_behavior_batch(s.validated_output) for s in behavior_stages)
        scenario = decode_scenario_interpretation(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.SCENARIO_INTERPRETATION)
        )
        input_hash = aggregation_stage_input_hash(behavior_outputs=behavior_outputs, scenario=scenario)
        await _add_successor(
            session,
            _make_queued_stage(
                simulation_run_id=run_id,
                pipeline_id=pipeline_id,
                stage_type=AIPipelineStageType.AGGREGATION,
                input_hash=input_hash,
            ),
        )
        return False

    if stage_type == AIPipelineStageType.AGGREGATION:
        aggregation = run_result.output
        assert isinstance(aggregation, AggregationResult)
        evidence = decode_page_evidence(
            await _require_succeeded_output(session, pipeline_id, AIPipelineStageType.EVIDENCE_PREPARATION)
        )
        task_context = build_task_context_source(input_snapshot=input_snapshot)
        input_hash = ux_report_stage_input_hash(
            evidence=evidence,
            baseline_metrics=build_baseline_metrics(result=result),
            aggregation=aggregation,
            module_summary=build_module_summary(input_snapshot=input_snapshot),
            task_context=task_context,
        )
        await _add_successor(
            session,
            _make_queued_stage(
                simulation_run_id=run_id,
                pipeline_id=pipeline_id,
                stage_type=AIPipelineStageType.UX_REPORT,
                input_hash=input_hash,
            ),
        )
        return False

    if stage_type == AIPipelineStageType.UX_REPORT:
        pipeline.status = AIPipelineStatus.SUCCEEDED
        pipeline.finished_at = _now()
        await session.flush()
        return True

    raise AIPipelineWorkerError("unknown_stage_type", f"bilinmeyen stage_type: {stage_type!r}")


# --- 6) process_one (Part 10) ----------------------------------------------------


async def process_one_ai_stage(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    provider: AIProvider | None = None,
) -> AIStageProcessResult:
    """En fazla BIR stage claim eder, calistirir ve sonucu persist eder.

    Sonsuz loop/sleep/cron ICERMEZ. Is yoksa `NO_WORK` dondurur. Provider'i
    ACIK olarak alir (Mock'a otomatik dusulmez); provider gerektiren stage'de
    provider verilmemisse kontrollu bir configuration hatasi FAILED olarak
    yazilir.
    """

    # Transaction A: claim -> RUNNING/attempt++ -> commit.
    async with session_maker() as session:
        claimed = await claim_next_ai_stage(session)
        await session.commit()

    if claimed is None:
        return AIStageProcessResult(outcome=AIStageOutcome.NO_WORK)

    # Kisa read: girdi hazirlama + iptal kontrolu (provider cagrisindan ONCE).
    async with session_maker() as session:
        plan = await _prepare_stage_execution(session, claimed)

    if plan.cancelled:
        return await _persist_cancellation(session_maker, claimed)

    # Provider asamalari (3/4/6): provider zorunlu + provider cagrisindan ONCE
    # kisa bir transaction'da provider/model/execution key PINLENIR (retry'de
    # dogrulanir). Saf asamalarda (1/2/5) pinning YAPILMAZ.
    if claimed.stage_type in _PROVIDER_STAGE_TYPES:
        if provider is None:
            return await persist_stage_failure(
                session_maker, claimed, error_code=ERROR_PROVIDER_NOT_CONFIGURED
            )
        pin = await _pin_provider_execution(session_maker, claimed, provider=provider)
        if pin is _PinResult.STALE:
            return AIStageProcessResult(
                outcome=AIStageOutcome.STALE_RESULT_REJECTED,
                pipeline_id=claimed.pipeline_id,
                stage_id=claimed.stage_id,
                stage_type=claimed.stage_type,
                batch_index=claimed.batch_index,
            )
        if pin is _PinResult.MISMATCH:
            # provider DEGISTIREN retry: provider CAGRILMADAN terminal FAILED.
            failure = await persist_stage_failure(
                session_maker, claimed, error_code=ERROR_PROVIDER_CONFIGURATION_MISMATCH
            )
            return _with_outcome(failure, AIStageOutcome.PROVIDER_CONFIGURATION_MISMATCH)

    # Execution: transaction/lock DISINDA (provider cagrisi burada olabilir).
    try:
        run_result = await execute_claimed_ai_stage(claimed, plan, provider=provider)
    except PipelineStageError as exc:
        # Retryable hata VE deneme hakki varsa: RUNNING->QUEUED (pipeline RUNNING
        # kalir, successor yok). Aksi halde (non-retryable VEYA deneme bitti)
        # terminal FAILED. Karar TYPED `exc.retryable`den verilir (string DEGIL).
        if exc.retryable and claimed.claimed_attempt_count < MAX_STAGE_ATTEMPTS:
            return await persist_stage_retry(session_maker, claimed, error_code=exc.error_code)
        return await persist_stage_failure(session_maker, claimed, error_code=exc.error_code)
    except AIPipelineWorkerConfigurationError as exc:
        return await persist_stage_failure(session_maker, claimed, error_code=exc.code)

    # Transaction B: yeniden kilitle/CAS -> persist -> successor -> commit.
    return await persist_stage_success(session_maker, claimed, run_result, provider=provider)


def _with_outcome(result: AIStageProcessResult, outcome: AIStageOutcome) -> AIStageProcessResult:
    """`AIStageProcessResult`un yalnizca `outcome`unu degistiren immutable kopya
    (ornegin terminal FAILED yazan bir persist'in disari verilen sonuc turunu
    daha spesifik bir degere yukseltmek icin)."""

    return AIStageProcessResult(
        outcome=outcome,
        pipeline_id=result.pipeline_id,
        stage_id=result.stage_id,
        stage_type=result.stage_type,
        batch_index=result.batch_index,
        error_code=result.error_code,
        pipeline_completed=result.pipeline_completed,
    )


# --- 7) Stale RUNNING recovery (Faz 3B.2C) ---------------------------------------


@dataclass(frozen=True)
class StaleRecoveryResult:
    """`reap_stale_ai_stages`in tek reap cagrisi ozeti."""

    scanned: int = 0
    requeued: int = 0
    failed: int = 0
    cancelled: int = 0


# Reaper'in dokunabilecegi pipeline durumlari: terminal-success/fail HARIC (bir
# stale RUNNING stage yalnizca calistirilabilir ya da iptal edilmis bir pipeline
# altinda anlamlidir).
_REAPABLE_PIPELINE_STATUSES: tuple[AIPipelineStatus, ...] = (
    AIPipelineStatus.QUEUED,
    AIPipelineStatus.RUNNING,
    AIPipelineStatus.PARTIAL,
    AIPipelineStatus.CANCELLED,
)


async def _select_stale_ai_stages(
    session: AsyncSession, *, cutoff: datetime, limit: int
) -> list[AIPipelineStage]:
    """`updated_at <= cutoff` olan RUNNING stage'leri (calistirilabilir/iptal
    pipeline altinda) `FOR UPDATE SKIP LOCKED` ile, deterministik sirada ve
    sinirli batch olarak kilitler (bkz. `claim_next_ai_stage` konvansiyonu)."""

    stmt = (
        select(AIPipelineStage)
        .join(AIPipelineRun, AIPipelineStage.ai_pipeline_run_id == AIPipelineRun.id)
        .where(
            AIPipelineStage.status == AIPipelineStageStatus.RUNNING,
            AIPipelineStage.updated_at <= cutoff,
            AIPipelineRun.status.in_(_REAPABLE_PIPELINE_STATUSES),
        )
        .order_by(AIPipelineRun.created_at, AIPipelineStage.created_at, AIPipelineStage.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=AIPipelineStage)
    )
    return list((await session.execute(stmt)).scalars().all())


async def reap_stale_ai_stages(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    stale_after_seconds: int = STALE_RUNNING_TIMEOUT_SECONDS,
    limit: int = STALE_REAP_BATCH_LIMIT,
) -> StaleRecoveryResult:
    """Worker cokmesi/kopmasi nedeniyle RUNNING'de takili kalan stage'leri kurtarir.

    Sinir davranisi: `updated_at <= now - stale_after_seconds` (yani yas
    `>= stale_after_seconds` ise stale). `SELECT ... FOR UPDATE SKIP LOCKED`,
    deterministik sira, sinirli batch; tum batch tek kisa transaction'da islenir.

    Her stale stage icin (spec Part 6):
    - Iptal edilmis pipeline/run: stage CANCELLED, pipeline CANCELLED, yeniden
      kuyruga ALINMAZ (provider cagrisi yok).
    - `attempt_count < MAX_STAGE_ATTEMPTS`: RUNNING->QUEUED; pipeline RUNNING
      kalir; logical/execution idempotency key VE provider/model pinleri
      KORUNUR (dokunulmaz); `error_code = stale_execution_requeued`;
      `attempt_count` ARTIRILMAZ (artis yalnizca claim'de). Eski worker sonucu
      sonra gelirse CAS tarafindan reddedilir.
    - `attempt_count >= MAX_STAGE_ATTEMPTS`: stage FAILED, pipeline FAILED,
      `error_code = stale_execution_attempts_exhausted`, successor OLUSTURULMAZ.
    """

    now = now or _now()
    cutoff = now - timedelta(seconds=stale_after_seconds)
    requeued = 0
    failed = 0
    cancelled = 0

    async with session_maker() as session:
        stale_stages = await _select_stale_ai_stages(session, cutoff=cutoff, limit=limit)
        for stage in stale_stages:
            pipeline = await session.get(AIPipelineRun, stage.ai_pipeline_run_id)
            if pipeline is None:
                continue
            run = await session.get(SimulationRun, pipeline.simulation_run_id)
            is_cancelled = (
                pipeline.cancel_requested
                or pipeline.status == AIPipelineStatus.CANCELLED
                or (run is not None and run.cancel_requested)
            )

            if is_cancelled:
                mark_stage_cancelled(stage, now=now)
                pipeline.status = AIPipelineStatus.CANCELLED
                if pipeline.finished_at is None:
                    pipeline.finished_at = now
                cancelled += 1
            elif stage.attempt_count < MAX_STAGE_ATTEMPTS:
                # RUNNING->QUEUED; keys/pins/attempt_count KORUNUR, finished_at YOK.
                stage.status = AIPipelineStageStatus.QUEUED
                stage.error_code = ERROR_STALE_EXECUTION_REQUEUED
                requeued += 1
            else:
                stage.status = AIPipelineStageStatus.FAILED
                stage.error_code = ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED
                stage.finished_at = now
                pipeline.status = AIPipelineStatus.FAILED
                pipeline.finished_at = now
                failed += 1

        await session.commit()

    return StaleRecoveryResult(
        scanned=len(stale_stages), requeued=requeued, failed=failed, cancelled=cancelled
    )


__all__ = [
    "AIStageOutcome",
    "AIStageProcessResult",
    "ClaimedAIStage",
    "StageExecutionPlan",
    "StaleRecoveryResult",
    "AIPipelineWorkerError",
    "AIPipelineWorkerConfigurationError",
    "ProviderConfigurationMismatchError",
    "ERROR_PROVIDER_NOT_CONFIGURED",
    "ERROR_STAGE_INPUT_HASH_MISMATCH",
    "ERROR_STAGE_IDEMPOTENCY_MISMATCH",
    "ERROR_STAGE_EXECUTION_KEY_MISMATCH",
    "ERROR_PROVIDER_CONFIGURATION_MISMATCH",
    "ERROR_STALE_EXECUTION_REQUEUED",
    "ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED",
    "ERROR_STALE_EXECUTION_CANCELLED",
    "MAX_STAGE_ATTEMPTS",
    "STALE_RUNNING_TIMEOUT_SECONDS",
    "mark_stage_cancelled",
    "claim_next_ai_stage",
    "execute_claimed_ai_stage",
    "persist_stage_success",
    "persist_stage_failure",
    "persist_stage_retry",
    "enqueue_ready_successors",
    "process_one_ai_stage",
    "reap_stale_ai_stages",
]
