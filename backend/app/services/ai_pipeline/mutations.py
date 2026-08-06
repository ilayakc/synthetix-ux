"""Tenant-guvenli AI pipeline launch-grup MUTASYON servisleri (Faz 3C.3).

Bu modul, salt-okunur `queries.py`nin AKSINE, bir launch grubunun AI
pipeline'lari uzerinde iki YAZMA islemi saglar:

- `retry_ai_pipeline_group`: TERMINAL basarisiz/iptal edilmis bir AI pipeline
  grubunu, launch grubu basina YENI bir 50 Chip rezervasyonu alarak MANUEL
  yeniden ucretlendirir ve yalnizca basarisiz/iptal olan kismi yeniden kuyruga
  alir (basarili sibling pipeline/stage sonuclari KORUNUR).
- `cancel_ai_pipeline_group`: Aktif (nonterminal) AI pipeline grubunu iptal
  eder ve (RESERVED ise) paylasilan AI rezervasyonunu serbest birakir.

Tasarim ilkeleri (bkz. gorev talimati Faz 3C.3):
- HICBIR `HTTPException` KULLANILMAZ - yalnizca kisa, sabit `code`'lu domain
  hatalari (`AIPipelineMutationError` alt tipleri) firlatilir; router bunlari
  HTTP durum kodlarina cevirir (queries.py/orchestration.py deseniyle tutarli).
- Transaction sahipligi: bu servis `session.flush()` yapar ama `commit()`
  ETMEZ - commit'i CAGIRAN (router) ustlenir (bkz. app.services.ai_pipeline.
  orchestration konvansiyonu). Boylece rezervasyon + pipeline/stage reset +
  sibling SimulationRun referans guncellemeleri AYNI dis transaction'da atomiktir.
- Grup uzerinde, `lifecycle.py`nin AYNI advisory-lock deseni (`_lock_launch_
  group`) FARKLI, kendine ait salt degerleriyle (`_RETRY_LOCK_SALT`/
  `_CANCEL_LOCK_SALT`) kullanilir - init/settlement/baseline kilitleriyle
  KARISMAZ. Boylece es-zamanli iki retry (ya da iki cancel) istegi tek bir
  rezervasyon uretir, `manual_retry_count`i iki kez artirmaz, stage'leri iki
  kez resetlemez.
- Chip finansal otoritesi: settlement cycle (lifecycle.py) OTOMATIK tek finansal
  otorite olmaya devam eder. Retry, settlement mantigini KOPYALAMAZ - yalnizca
  eski rezervasyonun RELEASED oldugunu DOGRULAR ve YENI bir rezervasyon acar
  (sonraki settlement cycle onu normal sekilde consume/release eder). Cancel,
  yalnizca AKTIF bir rezervasyonu (RESERVED) dogrudan release edebilir (acik
  istisna). Worker/reaper icine HICBIR Chip kodu eklenmez.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_pipeline import (
    AIPipelineRun,
    AIPipelineStageStatus,
    AIPipelineStatus,
)
from app.models.billing import ChipReservation, ChipReservationStatus
from app.models.simulations import SimulationRun
from app.services import chip_ledger
from app.services.ai_pipeline.lifecycle import _lock_launch_group
from app.services.ai_pipeline.provider_errors import (
    ERROR_CODE_PROVIDER_TIMEOUT,
    ERROR_CODE_PROVIDER_TRANSPORT,
)
from app.services.ai_pipeline.worker import (
    ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED,
    ERROR_STALE_EXECUTION_CANCELLED,
    ERROR_STALE_EXECUTION_REQUEUED,
    mark_stage_cancelled,
)
from app.services.pricing import AI_REPORT_CHIP_COST, AI_REPORT_MODULE_KEY

# --- Advisory lock salt'lari (lifecycle.py salt=0/1/2 ile KARISMAZ) --------------
# salt=0 baseline (simulation_worker), salt=1 init, salt=2 settlement (lifecycle).
# Bu iki mutasyon KENDI salt'larini kullanir (uc mevcut adres alanindan ayri).
_RETRY_LOCK_SALT: Final = 3
_CANCEL_LOCK_SALT: Final = 4


# --- Manuel retry hata uygunlugu (TYPED/stabil error-code allowlist) -------------
# Karar STRING icinde kelime aramaya DEGIL, sabit error-code kimliklerine
# dayanir (bkz. provider_errors.py / retry_policy.py / worker.py). Bu kodlar
# GECICI/kurtarilabilir terminal basarisizliklardir - manuel (ucretli) retry'a
# uygundur.
_RETRYABLE_MANUAL_ERROR_CODES: Final[frozenset[str]] = frozenset(
    {
        ERROR_CODE_PROVIDER_TIMEOUT,  # "provider_timeout"
        ERROR_CODE_PROVIDER_TRANSPORT,  # "provider_transport_error"
        ERROR_STALE_EXECUTION_ATTEMPTS_EXHAUSTED,  # "stale_execution_attempts_exhausted"
        ERROR_STALE_EXECUTION_CANCELLED,  # "stale_execution_cancelled"
        ERROR_STALE_EXECUTION_REQUEUED,  # "stale_execution_requeued"
    }
)

# Terminal pipeline durumlari (yeni claim gelmez).
_TERMINAL_PIPELINE_STATUSES: Final = (
    AIPipelineStatus.SUCCEEDED,
    AIPipelineStatus.FAILED,
    AIPipelineStatus.CANCELLED,
)
# Retry gerektiren (yeniden kuyruga alinacak) terminal-basarisiz durumlar.
_RETRY_TRIGGERING_STATUSES: Final = (
    AIPipelineStatus.FAILED,
    AIPipelineStatus.CANCELLED,
    AIPipelineStatus.PARTIAL,
)
# Cancel edilebilir (nonterminal) pipeline durumlari.
_CANCELLABLE_PIPELINE_STATUSES: Final = (
    AIPipelineStatus.QUEUED,
    AIPipelineStatus.RUNNING,
    AIPipelineStatus.PARTIAL,
)


# --- Domain hatalari (queries.py `(code, message)` deseni) ------------------------


class AIPipelineMutationError(Exception):
    """AI pipeline mutasyon katmanindaki tum domain hatalarinin temel sinifi.

    `HTTPException`/API tipi DEGILDIR; `code` guvenle disari verilebilecek kisa
    bir tanimlayici, `message` yalnizca log/gelistirici icindir (ham payload/PII
    TASIMAZ)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class SimulationRunNotFoundError(AIPipelineMutationError):
    """`SimulationRun` verilen id + organizasyon icin bulunamadi (yok YA DA baska
    org'a ait - iki durum cagiran acisindan AYIRT EDILEMEZ, cross-tenant sizmaz)."""


class AIPipelineGroupConflictError(AIPipelineMutationError):
    """Grup, istenen mutasyon (retry/cancel) icin uygun durumda degil (409)."""


# Retry conflict/integrity kodlari.
ERROR_SIMULATION_RUN_NOT_FOUND = "simulation_run_not_found"
ERROR_RETRY_NOT_REQUESTED = "ai_retry_not_requested"
ERROR_RETRY_NO_PIPELINE = "ai_retry_no_pipeline"
ERROR_RETRY_NOTHING_TO_RETRY = "ai_retry_nothing_to_retry"
ERROR_RETRY_PIPELINE_ACTIVE = "ai_retry_pipeline_active"
ERROR_RETRY_WAITING_FOR_SETTLEMENT = "ai_retry_waiting_for_settlement"
ERROR_RETRY_RESERVATION_CONSUMED = "ai_retry_reservation_consumed"
ERROR_RETRY_INTEGRITY = "ai_retry_integrity"
ERROR_RETRY_NON_RETRYABLE = "ai_retry_non_retryable_failure"
# Cancel conflict kodlari.
ERROR_CANCEL_NO_PIPELINE = "ai_cancel_no_pipeline"
ERROR_CANCEL_ALREADY_COMPLETED = "ai_cancel_already_completed"


# --- Strict response gorunumleri (madde 11; queries.py deseni) -------------------


class AIRetryResult(BaseModel):
    """Basarili bir manuel retry'in GUVENLI ozeti. `extra="forbid"`: rezervasyon/
    ledger id'si gibi ic alanlar sonradan sessizce eklenemez."""

    model_config = ConfigDict(extra="forbid")

    simulation_run_id: uuid.UUID
    launch_group_id: uuid.UUID
    group_status: str
    pipeline_count: int
    requeued_pipeline_count: int
    preserved_pipeline_count: int
    requeued_stage_count: int
    manual_retry_count: int
    charged_chips: int


class AICancelResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    simulation_run_id: uuid.UUID
    launch_group_id: uuid.UUID
    group_status: str
    cancelled_pipeline_count: int
    cancelled_stage_count: int
    released_chips: int


# --- Ortak yardimcilar -----------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _ai_report_requested(run: SimulationRun) -> bool:
    modules = (run.input_snapshot or {}).get("modules") or []
    return AI_REPORT_MODULE_KEY in modules


async def _load_scoped_run(
    session: AsyncSession, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID
) -> SimulationRun:
    """Tenant sinirini SORGUNUN parcasi yapar. Run yoksa YA DA baska org'a aitse
    AYNI `SimulationRunNotFoundError` (cross-tenant var/yok bilgisi sizmaz)."""

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


async def _load_group_sibling_runs(
    session: AsyncSession, run: SimulationRun
) -> tuple[uuid.UUID, list[SimulationRun]]:
    """Launch grubunu kanonik belirler: `launch_run_id` varsa TUM sibling'lar,
    yoksa yalnizca bu run. Grup kimligi (`launch_group_id`) advisory lock
    anahtaridir."""

    if run.launch_run_id is not None:
        launch_group_id = run.launch_run_id
        stmt = (
            select(SimulationRun)
            .where(SimulationRun.launch_run_id == launch_group_id)
            .order_by(SimulationRun.id)
        )
        siblings = list((await session.execute(stmt)).scalars().all())
    else:
        launch_group_id = run.id
        siblings = [run]
    return launch_group_id, siblings


async def _load_group_pipelines(session: AsyncSession, run_ids: list[uuid.UUID]) -> list[AIPipelineRun]:
    stmt = (
        select(AIPipelineRun)
        .where(AIPipelineRun.simulation_run_id.in_(run_ids))
        .options(selectinload(AIPipelineRun.stages))
        .order_by(AIPipelineRun.simulation_run_id)
    )
    return list((await session.execute(stmt)).scalars().all())


def _shared_reservation_id(ai_runs: list[SimulationRun]) -> uuid.UUID:
    """AI raporu istenmis run'larin PAYLASTIGI tek AI rezervasyon id'sini
    dogrular ve dondurur (birden fazla/eksik ise integrity conflict)."""

    reservation_ids = {r.ai_chip_reservation_id for r in ai_runs if r.ai_chip_reservation_id is not None}
    if len(reservation_ids) != 1:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_INTEGRITY,
            "AI raporu run'lari tek bir paylasilan rezervasyon id'si tasimiyor",
        )
    return next(iter(reservation_ids))


# =============================================================================
# 1) MANUEL RETRY
# =============================================================================


async def retry_ai_pipeline_group(
    session: AsyncSession, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID
) -> AIRetryResult:
    """Terminal basarisiz/iptal edilmis bir AI pipeline launch grubunu, YENI bir
    50 Chip rezervasyonu alarak MANUEL yeniden ucretlendirir ve yeniden kuyruga
    alir.

    Uygunluk (aksi halde `AIPipelineGroupConflictError`, hicbir Chip hareketi
    YOK): ai_report istenmis; pipeline'lar mevcut; TUM pipeline'lar terminal;
    en az biri FAILED/CANCELLED/PARTIAL; basarisiz stage error_code'lari
    retryable allowlist'te; paylasilan rezervasyon RELEASED. Bakiye yetersizse
    `InsufficientChipBalanceError` (hicbir state degismez).
    """

    run = await _load_scoped_run(
        session, organization_id=organization_id, simulation_run_id=simulation_run_id
    )
    launch_group_id, siblings = await _load_group_sibling_runs(session, run)

    # Grup kilidi (init/settlement/baseline'dan AYRI salt). Es-zamanli iki retry
    # serilestirilir: ikincisi kilidi aldiginda durumu (yeni RESERVED rezervasyon
    # / QUEUED pipeline) gorup conflict doner - cift ucret/cift reset olmaz.
    await _lock_launch_group(session, launch_group_id, salt=_RETRY_LOCK_SALT)

    if not any(_ai_report_requested(r) for r in siblings):
        raise AIPipelineGroupConflictError(ERROR_RETRY_NOT_REQUESTED, "bu grup icin ai_report istenmemis")

    ai_runs = [r for r in siblings if r.ai_chip_reservation_id is not None]
    if not ai_runs:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_NO_PIPELINE, "grup icin AI rezervasyonu tasiyan run yok"
        )
    reservation_id = _shared_reservation_id(ai_runs)

    ai_run_ids = [r.id for r in ai_runs]
    pipelines = await _load_group_pipelines(session, ai_run_ids)
    if len(pipelines) != len(ai_run_ids):
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_NO_PIPELINE, "grup pipeline kayitlari eksik (henuz olusturulmamis)"
        )

    # TUM pipeline'lar terminal olmali (aktif QUEUED/RUNNING varsa retry YOK).
    if any(p.status not in _TERMINAL_PIPELINE_STATUSES for p in pipelines):
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_PIPELINE_ACTIVE, "grupta hala aktif (QUEUED/RUNNING) pipeline var"
        )

    to_retry = [p for p in pipelines if p.status in _RETRY_TRIGGERING_STATUSES]
    if not to_retry:
        # Hepsi SUCCEEDED.
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_NOTHING_TO_RETRY, "yeniden denenecek terminal-basarisiz pipeline yok"
        )

    # Manuel retry error-code uygunlugu (yalnizca FAILED pipeline'lardaki FAILED
    # stage'ler icin; CANCELLED pipeline kullanici iptalidir - dogal olarak
    # retryable). Ayrica FAILED stage'de validated_output varsa integrity.
    for pipeline in to_retry:
        if pipeline.status == AIPipelineStatus.CANCELLED:
            continue
        for stage in pipeline.stages:
            if stage.status != AIPipelineStageStatus.FAILED:
                continue
            if stage.validated_output is not None:
                raise AIPipelineGroupConflictError(
                    ERROR_RETRY_INTEGRITY,
                    "basarisiz stage validated_output tasiyor (butunluk hatasi)",
                )
            if stage.error_code not in _RETRYABLE_MANUAL_ERROR_CODES:
                raise AIPipelineGroupConflictError(
                    ERROR_RETRY_NON_RETRYABLE,
                    "basarisiz stage guvenli manuel retry'a uygun olmayan bir hata kodu tasiyor",
                )

    # Paylasilan rezervasyon RELEASED olmali (settlement tamamlanmis).
    reservation = await session.get(ChipReservation, reservation_id)
    if reservation is None or reservation.organization_id != organization_id:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_INTEGRITY, "paylasilan AI rezervasyonu bulunamadi/organizasyon uyumsuz"
        )
    if reservation.status == ChipReservationStatus.RESERVED:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_WAITING_FOR_SETTLEMENT,
            "AI rezervasyonu hala RESERVED (settlement henuz tamamlanmadi)",
        )
    if reservation.status == ChipReservationStatus.CONSUMED:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_RESERVATION_CONSUMED, "AI rezervasyonu zaten CONSUMED (retry edilemez)"
        )

    # Jenerasyon: grup pipeline'lari AYNI manual_retry_count'u tasimali.
    generations = {p.manual_retry_count for p in pipelines}
    if len(generations) != 1:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_INTEGRITY, "grup pipeline'lari tutarsiz manual_retry_count tasiyor"
        )
    new_generation = next(iter(generations)) + 1

    # --- Buradan itibaren mutasyon. ONCE rezervasyon (bakiye yetersizse
    # InsufficientChipBalanceError, HICBIR state degismeden yukselir). ---
    idempotency_key = f"ai-report-retry:{launch_group_id}:{new_generation}"
    new_reservation = await chip_ledger.reserve_chips(
        session,
        organization_id,
        AI_REPORT_CHIP_COST,
        f"AI raporu manuel retry (jenerasyon {new_generation})",
        run_id=run.id,
        idempotency_key=idempotency_key,
    )

    # Pipeline/stage reset (yalnizca basarisiz/iptal pipeline'lar; SUCCEEDED
    # pipeline/stage KORUNUR). SUCCEEDED stage'ler + execution kimlikleri/pinleri
    # DEGISMEZ; yalnizca yeniden calisacak stage'ler QUEUED'a doner.
    requeued_stage_count = 0
    for pipeline in to_retry:
        for stage in pipeline.stages:
            if stage.status == AIPipelineStageStatus.SUCCEEDED:
                continue
            # KORUNANLAR: idempotency_key, execution_idempotency_key, provider,
            # model_name, input_hash (ayni execution kimligiyle retry).
            stage.status = AIPipelineStageStatus.QUEUED
            stage.attempt_count = 0
            stage.error_code = None
            stage.started_at = None
            stage.finished_at = None
            requeued_stage_count += 1
        pipeline.status = AIPipelineStatus.QUEUED
        pipeline.cancel_requested = False
        pipeline.started_at = None
        pipeline.finished_at = None

    # manual_retry_count: grup icindeki TUM pipeline'larda ayni yeni jenerasyon
    # (SUCCEEDED korunanlar dahil - grup tutarliligi).
    for pipeline in pipelines:
        pipeline.manual_retry_count = new_generation

    # Yeni rezervasyon referansi TUM sibling AI run'larina (eski baseline
    # chip_reservation_id DEGISMEZ; yalnizca ai_chip_reservation_id guncellenir).
    for ai_run in ai_runs:
        ai_run.ai_chip_reservation_id = new_reservation.id

    await session.flush()

    preserved = [p for p in pipelines if p.status == AIPipelineStatus.SUCCEEDED]
    return AIRetryResult(
        simulation_run_id=run.id,
        launch_group_id=launch_group_id,
        group_status=AIPipelineStatus.QUEUED.value if to_retry else AIPipelineStatus.SUCCEEDED.value,
        pipeline_count=len(pipelines),
        requeued_pipeline_count=len(to_retry),
        preserved_pipeline_count=len(preserved),
        requeued_stage_count=requeued_stage_count,
        manual_retry_count=new_generation,
        charged_chips=AI_REPORT_CHIP_COST,
    )


# =============================================================================
# 2) CANCEL
# =============================================================================


async def cancel_ai_pipeline_group(
    session: AsyncSession, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID
) -> AICancelResult:
    """Aktif (nonterminal) bir AI pipeline launch grubunu iptal eder ve
    (RESERVED ise) paylasilan AI rezervasyonunu serbest birakir.

    Idempotent: zaten iptal edilmis/RELEASED bir grupta ikinci cagri ikinci Chip
    release URETMEZ, stage'leri bozmaz, ayni guvenli sonucu doner. CONSUMED
    rezervasyon + tum pipeline'lar SUCCEEDED ise cancel REDDEDILIR (tamamlanmis
    is iptal edilemez). Baseline SimulationRun/Report/Persona DEGISMEZ; successor
    OLUSTURULMAZ; provider CAGRILMAZ.

    Stage terminalizasyonu (Faz 3C.3 SORUN 2): iptal transaction'inda NONTERMINAL
    (QUEUED + RUNNING) stage'lerin TAMAMI, paylasilan `worker.mark_stage_cancelled`
    ile ATOMIK olarak CANCELLED yapilir - CANCELLED bir pipeline altinda ORFAN
    RUNNING stage KALMAZ. RUNNING stage'in geç provider sonucu iki katmanli CAS ile
    reddedilir (stage.status artik RUNNING degil + pipeline CANCELLED/generation);
    bkz. worker._relock_for_persist. SUCCEEDED (+ diger terminal) stage ciktilari
    KORUNUR (silinmez); attempt_count/provider/model/execution key DEGISMEZ.
    """

    run = await _load_scoped_run(
        session, organization_id=organization_id, simulation_run_id=simulation_run_id
    )
    launch_group_id, siblings = await _load_group_sibling_runs(session, run)

    await _lock_launch_group(session, launch_group_id, salt=_CANCEL_LOCK_SALT)

    ai_runs = [r for r in siblings if r.ai_chip_reservation_id is not None]
    if not ai_runs:
        raise AIPipelineGroupConflictError(
            ERROR_CANCEL_NO_PIPELINE, "grup icin AI rezervasyonu tasiyan run yok"
        )

    ai_run_ids = [r.id for r in ai_runs]
    pipelines = await _load_group_pipelines(session, ai_run_ids)
    if not pipelines:
        raise AIPipelineGroupConflictError(ERROR_CANCEL_NO_PIPELINE, "grup icin AI pipeline yok")

    reservation_id = _shared_reservation_id(ai_runs)
    reservation = await session.get(ChipReservation, reservation_id)
    if reservation is None or reservation.organization_id != organization_id:
        raise AIPipelineGroupConflictError(
            ERROR_RETRY_INTEGRITY, "paylasilan AI rezervasyonu bulunamadi/organizasyon uyumsuz"
        )

    # CONSUMED + tum pipeline'lar SUCCEEDED: tamamlanmis is iptal edilemez.
    if reservation.status == ChipReservationStatus.CONSUMED and all(
        p.status == AIPipelineStatus.SUCCEEDED for p in pipelines
    ):
        raise AIPipelineGroupConflictError(
            ERROR_CANCEL_ALREADY_COMPLETED, "grup basariyla tamamlanmis (iptal edilemez)"
        )

    now = _now()
    cancelled_pipeline_count = 0
    cancelled_stage_count = 0
    for pipeline in pipelines:
        if pipeline.status not in _CANCELLABLE_PIPELINE_STATUSES:
            continue  # SUCCEEDED/FAILED/CANCELLED terminal - dokunma.
        pipeline.cancel_requested = True
        pipeline.status = AIPipelineStatus.CANCELLED
        if pipeline.finished_at is None:
            pipeline.finished_at = now
        cancelled_pipeline_count += 1
        for stage in pipeline.stages:
            # NONTERMINAL (QUEUED henuz baslamamis + RUNNING calisan) stage'lerin
            # TAMAMI ATOMIK olarak terminal CANCELLED yapilir (Faz 3C.3 SORUN 2):
            # CANCELLED pipeline altinda ORFAN bir RUNNING stage KALMAZ. Paylasilan
            # `worker.mark_stage_cancelled` kullanilir (error_code tutarli, davranis
            # reaper ile ayni). RUNNING stage'in gec provider sonucu iki katmanda
            # reddedilir: (a) stage.status artik RUNNING degil -> stage-level CAS,
            # (b) pipeline CANCELLED/cancel_requested -> pipeline-level CAS (bkz.
            # worker._relock_for_persist). SUCCEEDED (+ diger terminal) stage'ler
            # KORUNUR; attempt_count/provider/model/execution key DEGISMEZ.
            if stage.status in (AIPipelineStageStatus.QUEUED, AIPipelineStageStatus.RUNNING):
                mark_stage_cancelled(stage, now=now)
                cancelled_stage_count += 1

    # Paylasilan rezervasyon RESERVED ise serbest birak (idempotent no-op eger
    # zaten RELEASED). CONSUMED (ama hepsi SUCCEEDED degil - teorik) durumunda
    # release_reservation InvalidChipReservationStateError firlatmasin diye
    # yalnizca RESERVED'da cagirilir.
    released_chips = 0
    if reservation.status == ChipReservationStatus.RESERVED:
        released = await chip_ledger.release_reservation(
            session,
            organization_id,
            reservation_id,
            "AI pipeline grubu manuel iptal edildi",
        )
        released_chips = released.amount

    await session.flush()

    return AICancelResult(
        simulation_run_id=run.id,
        launch_group_id=launch_group_id,
        group_status=AIPipelineStatus.CANCELLED.value,
        cancelled_pipeline_count=cancelled_pipeline_count,
        cancelled_stage_count=cancelled_stage_count,
        released_chips=released_chips,
    )


__all__ = [
    "AIPipelineMutationError",
    "SimulationRunNotFoundError",
    "AIPipelineGroupConflictError",
    "AIRetryResult",
    "AICancelResult",
    "ERROR_SIMULATION_RUN_NOT_FOUND",
    "ERROR_RETRY_NOT_REQUESTED",
    "ERROR_RETRY_NO_PIPELINE",
    "ERROR_RETRY_NOTHING_TO_RETRY",
    "ERROR_RETRY_PIPELINE_ACTIVE",
    "ERROR_RETRY_WAITING_FOR_SETTLEMENT",
    "ERROR_RETRY_RESERVATION_CONSUMED",
    "ERROR_RETRY_INTEGRITY",
    "ERROR_RETRY_NON_RETRYABLE",
    "ERROR_CANCEL_NO_PIPELINE",
    "ERROR_CANCEL_ALREADY_COMPLETED",
    "retry_ai_pipeline_group",
    "cancel_ai_pipeline_group",
]
