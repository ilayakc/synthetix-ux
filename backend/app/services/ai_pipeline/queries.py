"""Salt-okunur AI pipeline sorgu/gorunum katmani (Faz 3C.1).

Bu modul, frontend'in bir `SimulationRun`e bagli AI pipeline'inin DURUMUNU ve
(varsa) tamamlanmis UX RAPORUNU guvenli, tenant-scoped ve SALT-OKUNUR bicimde
okumasi icin gereken tum is mantigini tasir. Router'a HICBIR is mantigi
gomulmez (bkz. gorev talimati madde 10):

- Tenant sinirini SORGUNUN parcasi yapan ortak `_load_scoped_run` yardimcisi
  (iki endpoint de AYNI helper'i kullanir - iki farkli auth mantigi YOKTUR).
- Kanonik (deterministik) stage siralamasi (`sort_stages_canonically`).
- Beklenen toplam stage sayisi + ilerleme yuzdesi hesabi.
- Nihai UX raporunun SIKI decode akisi.

Bu katman `HTTPException` KULLANMAZ - yalnizca kisa, sabit `code`'lu domain
hatalari (`AIPipelineQueryError` alt tipleri) firlatir; bunlari HTTP durum
kodlarina router cevirir. Response gorunumleri (Pydantic modelleri) burada
tanimlidir; router'i gereksiz buyutmemek icin (bkz. madde 2). Bu gorunumler
ASLA su alanlari tasimaz: `validated_output` (ara stage'ler), `input_hash`,
`output_hash`, `prompt_hash`, prompt metni, execution/logical idempotency key,
provider ham cevabi, evidence, Persona attribute'lari, ham exception mesaji,
API anahtari/secret, token/maliyet ic muhasebesi (bkz. madde 3).
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_pipeline import (
    AIPipelineRun,
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.personas import Persona
from app.models.simulations import SimulationRun
from app.services.ai_pipeline.persistence import (
    AIPipelinePersistenceError,
    decode_persona_batch_manifest,
    decode_ux_report,
)
from app.services.ai_pipeline.schemas import DEFAULT_PERSONA_BATCH_SIZE, UXReport

# --- Sabit, sunucu-kontrollu metinler --------------------------------------------

# Her rapor response'unda dondurulen SABIT (server-controlled) sentetik-tahmin
# uyarisi. LLM/pipeline ciktisindan ALINMAZ - degismez bir sabittir (bkz. madde
# 6). `UXReport.disclaimer` (aggregation/LLM kaynakli) alanindan AYRIDIR.
SYNTHETIC_ESTIMATE_DISCLAIMER = (
    "Bu rapor gerçek kullanıcı araştırması değildir. Yapay zekâ ve sentetik "
    "personalar tarafından üretilmiş tahmini bir değerlendirmedir."
)

# Rapor icerigi HTML DEGIL, yapilandirilmis JSON'dur (frontend'in
# `dangerouslySetInnerHTML` kullanmasina gerek birakmaz, bkz. madde 6).
CONTENT_FORMAT_STRUCTURED_JSON = "structured_json"


# --- Kanonik stage sirasi (madde 3) ----------------------------------------------

# DB enum'unun (rastgele/alfabetik) sirasina GUVENILMEZ; acik kanonik sira.
CANONICAL_STAGE_ORDER: tuple[AIPipelineStageType, ...] = (
    AIPipelineStageType.EVIDENCE_PREPARATION,
    AIPipelineStageType.PERSONA_BATCH_PREPARATION,
    AIPipelineStageType.SCENARIO_INTERPRETATION,
    AIPipelineStageType.PERSONA_BEHAVIOR,
    AIPipelineStageType.AGGREGATION,
    AIPipelineStageType.UX_REPORT,
)
_STAGE_ORDER_INDEX: dict[AIPipelineStageType, int] = {
    stage_type: order for order, stage_type in enumerate(CANONICAL_STAGE_ORDER)
}

# Beklenen SABIT (batch'lenmeyen) stage sayisi: Evidence + Persona batch prep +
# Scenario + Aggregation + UX report = 5. Bunlara N adet PERSONA_BEHAVIOR batch
# eklenir (bkz. `expected_stage_count`).
FIXED_STAGE_COUNT = 5


# --- Domain hatalari (madde 10) --------------------------------------------------


class AIPipelineQueryError(Exception):
    """Salt-okunur sorgu katmanindaki tum domain hatalarinin temel sinifi.

    `HTTPException`/API-katmani tipi DEGILDIR; `code` ileride guvenle disari
    verilebilecek kisa/sabit bir tanimlayicidir, `message` yalnizca log/
    gelistirici icindir (ham payload/PII TASIMAZ)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SimulationRunNotFoundError(AIPipelineQueryError):
    """`SimulationRun` verilen id + organizasyon icin bulunamadi (yok YA DA baska
    org'a ait - iki durum cagiran acisindan AYIRT EDILEMEZ)."""


class PipelineNotFoundError(AIPipelineQueryError):
    """Tenant'i dogrulanmis `SimulationRun` icin bir `AIPipelineRun` yok."""


class ReportNotReadyError(AIPipelineQueryError):
    """Pipeline hala QUEUED/RUNNING - nihai rapor henuz hazir degil."""


class ReportNotAvailableError(AIPipelineQueryError):
    """Pipeline terminal ama BASARISIZ (FAILED/PARTIAL/CANCELLED) - nihai basarili
    rapor asla uretilmedi; ara stage ciktilari kullaniciya SUNULMAZ."""


class PipelineIntegrityError(AIPipelineQueryError):
    """Pipeline SUCCEEDED gorunuyor ama nihai cikti eksik/bozuk (UX_REPORT stage
    yok, `validated_output` bos ya da sema dogrulamasi basarisiz). Ham payload
    SIZDIRILMADAN kontrollu bir sunucu-butunlugu hatasi olarak yukselir."""


ERROR_SIMULATION_RUN_NOT_FOUND = "simulation_run_not_found"
ERROR_PIPELINE_NOT_FOUND = "ai_pipeline_not_found"
ERROR_REPORT_NOT_READY = "ai_report_not_ready"
ERROR_REPORT_NOT_AVAILABLE = "ai_report_not_available"
ERROR_REPORT_INTEGRITY = "ai_report_integrity_error"


# --- Response gorunumleri (strict; madde 2/3/6) ----------------------------------


class AIPipelineStageSummary(BaseModel):
    """Tek bir stage'in GUVENLI ozeti. `extra="forbid"`: hicbir hassas alan
    sonradan sessizce eklenemez."""

    model_config = ConfigDict(extra="forbid")

    stage_type: str
    status: str
    batch_index: int | None
    attempt_count: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AIPipelineStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: uuid.UUID
    simulation_run_id: uuid.UUID
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    cancel_requested: bool
    expected_stage_count: int
    completed_stage_count: int
    succeeded_stage_count: int
    running_stage_count: int
    queued_stage_count: int
    failed_stage_count: int
    progress_percent: int
    report_available: bool
    stages: tuple[AIPipelineStageSummary, ...]


class AIReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: uuid.UUID
    simulation_run_id: uuid.UUID
    generated_at: datetime | None
    content_format: str
    synthetic_disclaimer: str
    report: UXReport


# --- Kanonik siralama (madde 3) --------------------------------------------------


def _stage_sort_key(stage: AIPipelineStage) -> tuple[int, int, datetime, str]:
    """Deterministik siralama anahtari: kanonik stage sirasi -> `batch_index`
    (yalnizca PERSONA_BEHAVIOR icin anlamli; batch'lenmeyen stage'lerde None ->
    -1) -> son tie-break `created_at` -> `id`."""

    return (
        _STAGE_ORDER_INDEX.get(stage.stage_type, len(CANONICAL_STAGE_ORDER)),
        stage.batch_index if stage.batch_index is not None else -1,
        stage.created_at,
        str(stage.id),
    )


def sort_stages_canonically(stages: list[AIPipelineStage]) -> list[AIPipelineStage]:
    """Stage'leri kanonik/deterministik sirada dondurur (DB enum sirasina
    GUVENMEDEN)."""

    return sorted(stages, key=_stage_sort_key)


# --- Beklenen stage sayisi + ilerleme (madde 4) ----------------------------------


def _succeeded_stage2(stages: list[AIPipelineStage]) -> AIPipelineStage | None:
    return next(
        (
            stage
            for stage in stages
            if stage.stage_type == AIPipelineStageType.PERSONA_BATCH_PREPARATION
            and stage.status == AIPipelineStageStatus.SUCCEEDED
        ),
        None,
    )


async def _behavior_batch_count(
    session: AsyncSession, run_id: uuid.UUID, stages: list[AIPipelineStage]
) -> int:
    """Beklenen PERSONA_BEHAVIOR batch sayisini oncelik sirasiyla belirler:

    1. Stage 2 (PERSONA_BATCH_PREPARATION) SUCCEEDED ise: SIKI decode edilmis
       manifest'in `batch_count`'u (authoritative kaynak).
    2. Stage 2 henuz tamamlanmadiysa: bu run'a ait KALICI Persona satiri sayisi
       + mevcut batch-size SABITI (schemas.DEFAULT_PERSONA_BATCH_SIZE) ile
       `ceil` (batching.build_persona_batches ile AYNI formul).
    3. Veri butunlugu bozuksa (manifest decode edilemiyor / persona yok):
       sahte deger yerine kontrollu `PipelineIntegrityError`.
    """

    stage2 = _succeeded_stage2(stages)
    if stage2 is not None:
        if stage2.validated_output is None:
            raise PipelineIntegrityError(ERROR_REPORT_INTEGRITY, "Stage 2 SUCCEEDED ama validated_output bos")
        try:
            manifest = decode_persona_batch_manifest(stage2.validated_output)
        except AIPipelinePersistenceError as exc:
            raise PipelineIntegrityError(
                ERROR_REPORT_INTEGRITY, "Stage 2 manifest'i cozulemedi (butunluk hatasi)"
            ) from exc
        return manifest.batch_count

    persona_count = (
        await session.execute(
            select(func.count()).select_from(Persona).where(Persona.simulation_run_id == run_id)
        )
    ).scalar_one()
    if not persona_count:
        raise PipelineIntegrityError(
            ERROR_REPORT_INTEGRITY,
            "pipeline mevcut ama bu run icin kalici Persona satiri yok (butunluk hatasi)",
        )
    return math.ceil(persona_count / DEFAULT_PERSONA_BATCH_SIZE)


def expected_stage_count(behavior_batch_count: int) -> int:
    """Beklenen toplam stage sayisi = 5 sabit stage + N davranis batch'i."""

    return FIXED_STAGE_COUNT + behavior_batch_count


def compute_progress_percent(
    *, succeeded_stage_count: int, expected_total: int, pipeline_status: AIPipelineStatus
) -> int:
    """Ilerleme yuzdesi - YALNIZCA SUCCEEDED stage sayisina gore; 0-100 arasi.

    - Terminal SUCCEEDED pipeline: TAM 100 (kalan yuvarlama farki olmadan).
    - FAILED/CANCELLED/PARTIAL: sirf terminal oldugu icin 100 GOSTERILMEZ;
      yalnizca gercek succeeded orani.
    - Yuvarlama: deterministik round-half-up (tam sayi aritmetigi; float yok).
    """

    if pipeline_status == AIPipelineStatus.SUCCEEDED:
        return 100
    if expected_total <= 0:
        return 0
    # Round-half-up, yalnizca tam sayi aritmetigi ile (deterministik).
    percent = (succeeded_stage_count * 100 + expected_total // 2) // expected_total
    return max(0, min(100, percent))


# --- Ortak tenant-scoped yukleme (madde 5/10) ------------------------------------


async def _load_scoped_run(
    session: AsyncSession, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID
) -> SimulationRun:
    """Tenant sinirini SORGUNUN parcasi yapan ORTAK yardimci (iki endpoint de
    bunu kullanir). Run yoksa YA DA baska bir org'a aitse AYNI
    `SimulationRunNotFoundError` firlar - pipeline/rapor var/yok bilgisi
    cross-tenant SIZMAZ."""

    stmt = select(SimulationRun).where(
        SimulationRun.id == simulation_run_id,
        SimulationRun.organization_id == organization_id,
    )
    run = (await session.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise SimulationRunNotFoundError(
            ERROR_SIMULATION_RUN_NOT_FOUND, "verilen id/organizasyon icin SimulationRun bulunamadi"
        )
    return run


async def _load_pipeline_with_stages(
    session: AsyncSession, simulation_run_id: uuid.UUID
) -> AIPipelineRun | None:
    """Pipeline'i ve TUM stage'lerini tek `selectinload` ile yukler (lazy-load'a
    GUVENMEDEN; N+1 olusturmadan)."""

    stmt = (
        select(AIPipelineRun)
        .where(AIPipelineRun.simulation_run_id == simulation_run_id)
        .options(selectinload(AIPipelineRun.stages))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# --- 1) Durum sorgusu ------------------------------------------------------------


async def get_ai_pipeline_status(
    session: AsyncSession, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID
) -> AIPipelineStatusResponse:
    """Tenant-scoped pipeline durumu + kanonik stage ozetleri + ilerleme.

    Hata sozlesmesi: run yok/yanlis tenant -> `SimulationRunNotFoundError`;
    pipeline yok -> `PipelineNotFoundError`; veri butunlugu bozuk ->
    `PipelineIntegrityError`."""

    run = await _load_scoped_run(
        session, organization_id=organization_id, simulation_run_id=simulation_run_id
    )
    pipeline = await _load_pipeline_with_stages(session, run.id)
    if pipeline is None:
        raise PipelineNotFoundError(
            ERROR_PIPELINE_NOT_FOUND, "bu SimulationRun icin AI pipeline baslatilmamis"
        )

    stages = list(pipeline.stages)
    ordered = sort_stages_canonically(stages)

    succeeded = sum(1 for s in stages if s.status == AIPipelineStageStatus.SUCCEEDED)
    running = sum(1 for s in stages if s.status == AIPipelineStageStatus.RUNNING)
    queued = sum(1 for s in stages if s.status == AIPipelineStageStatus.QUEUED)
    failed = sum(1 for s in stages if s.status == AIPipelineStageStatus.FAILED)

    behavior_batches = await _behavior_batch_count(session, run.id, stages)
    expected_total = expected_stage_count(behavior_batches)
    progress = compute_progress_percent(
        succeeded_stage_count=succeeded,
        expected_total=expected_total,
        pipeline_status=pipeline.status,
    )

    # `report_available`, pipeline SUCCEEDED olsa bile nihai UX raporunun
    # GERCEKTEN mevcut/gecerli oldugunu ayni paylasilan `_get_validated_final_report`
    # yardimcisiyla dogrular - boylece bu alan, rapor endpoint'inin sonradan
    # 500 donecegi bir durumu sessizce `false` gostermez; bozukluk varsa
    # `PipelineIntegrityError` YUKARI FIRLATILIR (router bunu 500'e cevirir).
    report_available = False
    if pipeline.status == AIPipelineStatus.SUCCEEDED:
        _get_validated_final_report(pipeline)
        report_available = True

    return AIPipelineStatusResponse(
        pipeline_id=pipeline.id,
        simulation_run_id=run.id,
        status=pipeline.status.value,
        created_at=pipeline.created_at,
        started_at=pipeline.started_at,
        finished_at=pipeline.finished_at,
        cancel_requested=pipeline.cancel_requested,
        expected_stage_count=expected_total,
        completed_stage_count=succeeded,
        succeeded_stage_count=succeeded,
        running_stage_count=running,
        queued_stage_count=queued,
        failed_stage_count=failed,
        progress_percent=progress,
        report_available=report_available,
        stages=tuple(
            AIPipelineStageSummary(
                stage_type=s.stage_type.value,
                status=s.status.value,
                batch_index=s.batch_index,
                attempt_count=s.attempt_count,
                error_code=s.error_code,
                created_at=s.created_at,
                started_at=s.started_at,
                finished_at=s.finished_at,
            )
            for s in ordered
        ),
    )


# --- 2) Rapor sorgusu ------------------------------------------------------------


def _succeeded_ux_report_stage(stages: list[AIPipelineStage]) -> AIPipelineStage | None:
    return next(
        (
            stage
            for stage in stages
            if stage.stage_type == AIPipelineStageType.UX_REPORT
            and stage.status == AIPipelineStageStatus.SUCCEEDED
        ),
        None,
    )


def _get_validated_final_report(pipeline: AIPipelineRun) -> UXReport:
    """SUCCEEDED bir pipeline'in nihai UX raporunu bulup SIKI dogrular - hem
    durum hem de rapor endpoint'inin PAYLASTIGI tek, salt-okunur yardimci.

    Nihai raporun "mevcut ve gecerli" sayilmasi icin GEREKEN tum kosullar burada
    TEK yerde tanimlanir (mantik iki endpoint'te KOPYALANMAZ):
      - UX_REPORT stage MEVCUT ve SUCCEEDED olmali,
      - `validated_output` bos OLMAMALI,
      - `persistence.decode_ux_report` ile SIKI decode edilebilmeli.

    Cagiran YALNIZCA `pipeline.status == SUCCEEDED` iken cagirmalidir (QUEUED/
    RUNNING/FAILED/PARTIAL/CANCELLED yollari nihai ciktiyi decode ETMEZ).
    Herhangi bir kosul saglanmazsa, ham `validated_output` ya da decode hata
    metni SIZDIRILMADAN kontrollu bir `PipelineIntegrityError` yukselir."""

    ux_stage = _succeeded_ux_report_stage(list(pipeline.stages))
    if ux_stage is None or ux_stage.validated_output is None:
        raise PipelineIntegrityError(
            ERROR_REPORT_INTEGRITY,
            "pipeline SUCCEEDED ama basarili UX_REPORT ciktisi bulunamadi",
        )
    try:
        return decode_ux_report(ux_stage.validated_output)
    except AIPipelinePersistenceError as exc:
        # Ham payload SIZDIRILMADAN kontrollu butunluk hatasi.
        raise PipelineIntegrityError(
            ERROR_REPORT_INTEGRITY, "nihai UX raporu sema dogrulamasindan gecemedi"
        ) from exc


async def get_ai_pipeline_report(
    session: AsyncSession, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID
) -> AIReportResponse:
    """Nihai UX raporunu YALNIZCA su durumda dondurur: pipeline SUCCEEDED VE
    UX_REPORT stage SUCCEEDED VE `validated_output` SIKI `UXReport` olarak decode
    edilebiliyor.

    Hata sozlesmesi (router HTTP'ye cevirir):
      - run yok/yanlis tenant -> `SimulationRunNotFoundError`
      - pipeline yok           -> `PipelineNotFoundError`
      - pipeline QUEUED/RUNNING -> `ReportNotReadyError`
      - pipeline FAILED/PARTIAL/CANCELLED -> `ReportNotAvailableError`
      - SUCCEEDED ama nihai cikti eksik/bozuk -> `PipelineIntegrityError`
    Tenant dogrulamasi durum endpoint'iyle AYNI ortak `_load_scoped_run`
    uzerinden yapilir (iki farkli auth mantigi YOKTUR)."""

    run = await _load_scoped_run(
        session, organization_id=organization_id, simulation_run_id=simulation_run_id
    )
    pipeline = await _load_pipeline_with_stages(session, run.id)
    if pipeline is None:
        raise PipelineNotFoundError(
            ERROR_PIPELINE_NOT_FOUND, "bu SimulationRun icin AI pipeline baslatilmamis"
        )

    if pipeline.status in (AIPipelineStatus.QUEUED, AIPipelineStatus.RUNNING):
        raise ReportNotReadyError(
            ERROR_REPORT_NOT_READY, "AI raporu henuz hazir degil (pipeline devam ediyor)"
        )
    if pipeline.status != AIPipelineStatus.SUCCEEDED:
        # FAILED / PARTIAL / CANCELLED: nihai basarili rapor uretilmedi.
        raise ReportNotAvailableError(
            ERROR_REPORT_NOT_AVAILABLE,
            f"AI pipeline basariyla tamamlanmadi (durum: {pipeline.status.value})",
        )

    report = _get_validated_final_report(pipeline)

    return AIReportResponse(
        pipeline_id=pipeline.id,
        simulation_run_id=run.id,
        generated_at=pipeline.finished_at,
        content_format=CONTENT_FORMAT_STRUCTURED_JSON,
        synthetic_disclaimer=SYNTHETIC_ESTIMATE_DISCLAIMER,
        report=report,
    )


__all__ = [
    "SYNTHETIC_ESTIMATE_DISCLAIMER",
    "CONTENT_FORMAT_STRUCTURED_JSON",
    "CANONICAL_STAGE_ORDER",
    "FIXED_STAGE_COUNT",
    "AIPipelineQueryError",
    "SimulationRunNotFoundError",
    "PipelineNotFoundError",
    "ReportNotReadyError",
    "ReportNotAvailableError",
    "PipelineIntegrityError",
    "ERROR_SIMULATION_RUN_NOT_FOUND",
    "ERROR_PIPELINE_NOT_FOUND",
    "ERROR_REPORT_NOT_READY",
    "ERROR_REPORT_NOT_AVAILABLE",
    "ERROR_REPORT_INTEGRITY",
    "AIPipelineStageSummary",
    "AIPipelineStatusResponse",
    "AIReportResponse",
    "sort_stages_canonically",
    "expected_stage_count",
    "compute_progress_percent",
    "get_ai_pipeline_status",
    "get_ai_pipeline_report",
]
