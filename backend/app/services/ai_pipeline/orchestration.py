"""AI pipeline INITIALIZATION servisi (Faz 3B.2A).

`initialize_ai_pipeline`, TAMAMLANMIS (SUCCEEDED) bir `SimulationRun`den,
gelecekteki bir worker'in (Faz 3B.2B) tuketecegi veriyi HAZIRLAR:
- tekil (idempotent) bir `AIPipelineRun` kaydi,
- ilk asama olarak TEK bir QUEUED `AIPipelineStage` (EVIDENCE_PREPARATION)
  satiri - GERCEK, deterministik bir `idempotency_key`/`input_hash` ile.

Bu servis HICBIR provider cagirmaz, HICBIR stage'i CALISTIRMAZ (Stage 1
dahil), arq/worker/claim/retry mantigi ICERMEZ. Yalnizca veriyi dogrular ve
persist eder.

Transaction sahipligi (bkz. repo konvansiyonu - app.services.test_wizard.
launch_draft ve app.db.get_session): bu servis `session.add()` + `flush()`
yapar ama `commit()` ETMEZ; commit'i CAGIRAN taraf (router/worker) ustlenir.
Insert, es-zamanli cift-cagri yarisina karsi bir SAVEPOINT (`begin_nested`)
icinde yapilir - unique kisit ihlali (`IntegrityError`) yalnizca o ic birimi
geri alir, cagiranin dis transaction'ini bozmadan; ardindan mevcut satir
yeniden sorgulanip dondurulur.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_pipeline import (
    AIPipelineRun,
    AIPipelineStage,
    AIPipelineStageStatus,
    AIPipelineStageType,
    AIPipelineStatus,
)
from app.models.personas import Persona
from app.models.reports import Report
from app.models.simulations import SimulationRun, SimulationStatus
from app.services.ai_pipeline.schemas import MAX_PERSONAS_PER_PIPELINE
from app.services.ai_pipeline.stage_hashing import deterministic_stage_idempotency_key
from app.services.ai_pipeline.stage_sources import (
    build_evidence_stage_source,
    evidence_stage_source_input_hash,
)

# --- Domain hatalari (Part 7) ----------------------------------------------------


class AIPipelineInitializationError(Exception):
    """Initialization katmanindaki tum domain hatalarinin temel sinifi.

    `HTTPException`/API-katmani tipi DEGILDIR - saf Python istisnasidir,
    ileride bir API katmani tarafindan cevrilecek. `code` ileride guvenle
    disari verilebilecek kisa/sabit bir tanimlayicidir.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class AINotRequestedError(AIPipelineInitializationError):
    """`ai_requested` acikca True degilse firlatilir."""


class SimulationRunNotFoundError(AIPipelineInitializationError):
    """Verilen id (ve organizasyon) icin `SimulationRun` bulunamadiginda."""


class SimulationRunNotEligibleError(AIPipelineInitializationError):
    """`SimulationRun` pipeline baslatmaya uygun degil (yanlis status / iptal)."""


class MissingReportError(AIPipelineInitializationError):
    """Bu `SimulationRun` icin bir `Report` bulunmadiginda."""


class InvalidPersonaSetError(AIPipelineInitializationError):
    """Persona kumesi eksik/gecersiz (yok, >limit, yinelenen index, agirlik <= 0)."""


class PopulationTotalMismatchError(AIPipelineInitializationError):
    """Persona population_weight toplami, run'in beklenen toplamiyla eslesmedigi."""


ERROR_AI_NOT_REQUESTED = "ai_not_requested"
ERROR_SIMULATION_RUN_NOT_FOUND = "simulation_run_not_found"
ERROR_SIMULATION_RUN_NOT_ELIGIBLE = "simulation_run_not_eligible"
ERROR_MISSING_REPORT = "missing_report"
ERROR_INVALID_PERSONA_SET = "invalid_persona_set"
ERROR_POPULATION_TOTAL_MISMATCH = "population_total_mismatch"


# --- Yardimcilar -----------------------------------------------------------------


async def _load_existing_pipeline_run(
    session: AsyncSession, simulation_run_id: uuid.UUID
) -> AIPipelineRun | None:
    """Bu `simulation_run_id` icin var olan `AIPipelineRun`i (varsa) dondurur.

    Ayri bir fonksiyon olmasi kasitli: hem baslangictaki erken-donus
    kontrolu hem de IntegrityError sonrasi yeniden-sorgulama ayni mantigi
    kullanir (ve testte tek noktadan enjekte edilebilir)."""

    stmt = select(AIPipelineRun).where(AIPipelineRun.simulation_run_id == simulation_run_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def _load_personas(session: AsyncSession, simulation_run_id: uuid.UUID) -> list[Persona]:
    stmt = select(Persona).where(Persona.simulation_run_id == simulation_run_id).order_by(Persona.index)
    return list((await session.execute(stmt)).scalars().all())


async def _report_exists(session: AsyncSession, simulation_run_id: uuid.UUID) -> bool:
    stmt = select(Report.id).where(Report.simulation_run_id == simulation_run_id).limit(1)
    return (await session.execute(stmt)).first() is not None


def _validate_persona_set(personas: list[Persona], expected_total: object) -> None:
    """Persona kumesini (dogrulama SIRASI onemli) tum on kosullara gore denetler.

    HICBIR ORM nesnesi construct/add edilmeden ONCE cagirilir - boylece bir
    on kosul ihlali asla yarim bir pipeline/stage satiri birakmaz.
    """

    if not personas:
        raise InvalidPersonaSetError(ERROR_INVALID_PERSONA_SET, "bu simulation_run icin persona satiri yok")
    if len(personas) > MAX_PERSONAS_PER_PIPELINE:
        raise InvalidPersonaSetError(
            ERROR_INVALID_PERSONA_SET,
            f"persona sayisi 1 ile {MAX_PERSONAS_PER_PIPELINE} arasinda olmalidir "
            f"(gelen: {len(personas)})",
        )

    indices = [p.index for p in personas]
    if any(i < 0 for i in indices):
        raise InvalidPersonaSetError(ERROR_INVALID_PERSONA_SET, "persona index degerleri negatif olamaz")
    if len(indices) != len(set(indices)):
        raise InvalidPersonaSetError(ERROR_INVALID_PERSONA_SET, "yinelenen persona index degeri bulundu")

    persona_ids = [p.id for p in personas]
    if len(persona_ids) != len(set(persona_ids)):
        raise InvalidPersonaSetError(ERROR_INVALID_PERSONA_SET, "yinelenen persona id bulundu")

    if any(p.population_weight <= 0 for p in personas):
        raise InvalidPersonaSetError(
            ERROR_INVALID_PERSONA_SET, "her persona population_weight'i pozitif olmalidir"
        )

    if not isinstance(expected_total, int) or isinstance(expected_total, bool):
        raise PopulationTotalMismatchError(
            ERROR_POPULATION_TOTAL_MISMATCH,
            "run.input_snapshot['persona_count'] gecerli bir tam sayi degil",
        )
    total_weight = sum(p.population_weight for p in personas)
    if total_weight != expected_total:
        raise PopulationTotalMismatchError(
            ERROR_POPULATION_TOTAL_MISMATCH,
            "persona population_weight toplami run'in persona_count'u ile eslesmiyor "
            f"(beklenen: {expected_total}, hesaplanan: {total_weight})",
        )


# --- Ana servis ------------------------------------------------------------------


async def initialize_ai_pipeline(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    simulation_run_id: uuid.UUID,
    ai_requested: bool,
) -> AIPipelineRun:
    """Tamamlanmis bir `SimulationRun`den idempotent bir AI pipeline baslatir.

    Davranis:
      1. `ai_requested` acikca True olmali (aksi halde `AINotRequestedError`).
      2. TENANT SINIRI: `SimulationRun` organizasyona GORE (id + org) yuklenir;
         yoksa ya da baska bir org'a aitse AYNI `SimulationRunNotFoundError`
         firlatilir (cross-tenant varlik sizmaz). Bu, idempotency
         kontrolunden ONCE yapilir - IDOR'u onlemek icin.
      3. IDEMPOTENCY: run'in bu org'a aitligi dogrulandiktan SONRA, bu
         `simulation_run_id` icin zaten bir `AIPipelineRun` varsa onu DONDURUR
         (on kosullari yeniden calistirmadan).
      4. Aksi halde TUM on kosullar dogrulanir (asagida) - herhangi biri
         ihlal edilirse DISTINCT bir domain hatasi firlatilir ve HICBIR
         kismi satir persist EDILMEZ (dogrulama, ORM nesnesi olusturulmadan
         ONCE biter).
      5. `AIPipelineRun` + tek bir QUEUED `AIPipelineStage`
         (EVIDENCE_PREPARATION) bir SAVEPOINT icinde eklenir; es-zamanli
         cift-cagri yarisi olursa unique kisit `IntegrityError`i yakalanip
         mevcut satir yeniden sorgulanarak dondurulur.

    Transaction: `commit` ETMEZ - cagiran taraf commit eder (bkz. modul
    dokstring'i). Hicbir provider cagrisi/stage calistirmasi YAPILMAZ.
    """

    # 1) `ai_requested` guard (pipeline/pricing state'inden TURETME yok).
    if ai_requested is not True:
        raise AINotRequestedError(
            ERROR_AI_NOT_REQUESTED, "AI pipeline yalnizca ai_requested=True ile baslatilabilir"
        )

    # 2) TENANT SINIRI ONCE: SimulationRun'i organizasyona GORE yukle. Bu,
    #    idempotency (mevcut pipeline) kontrolunden ONCE yapilmalidir - aksi
    #    halde baska bir organizasyona ait bir simulation_run_id icin var olan
    #    pipeline, cagiran organizasyonun sahipligi DOGRULANMADAN geri
    #    donebilir (cross-tenant / IDOR). Run yoksa YA DA baska bir org'a
    #    aitse AYNI hata (`SimulationRunNotFoundError`) firlatilir - iki durum
    #    cagiran acisindan AYIRT EDILEMEZ olmalidir (cross-tenant varlik sizmaz).
    run_stmt = select(SimulationRun).where(
        SimulationRun.id == simulation_run_id,
        SimulationRun.organization_id == organization_id,
    )
    run = (await session.execute(run_stmt)).scalar_one_or_none()
    if run is None:
        raise SimulationRunNotFoundError(
            ERROR_SIMULATION_RUN_NOT_FOUND,
            "verilen id/organizasyon icin SimulationRun bulunamadi",
        )

    # 3) Idempotency: run'in bu organizasyona ait oldugu KANITLANDIKTAN sonra,
    #    mevcut pipeline satiri varsa onu dondurmek guvenlidir.
    existing = await _load_existing_pipeline_run(session, simulation_run_id)
    if existing is not None:
        return existing

    # 4) Uygunluk dogrulamalari (yalnizca gecerli, org-scoped bir run icin).
    if run.cancel_requested:
        raise SimulationRunNotEligibleError(
            ERROR_SIMULATION_RUN_NOT_ELIGIBLE, "iptal istenmis bir run icin pipeline baslatilamaz"
        )
    if run.status != SimulationStatus.SUCCEEDED:
        raise SimulationRunNotEligibleError(
            ERROR_SIMULATION_RUN_NOT_ELIGIBLE,
            f"pipeline yalnizca SUCCEEDED run icin baslatilabilir (mevcut: {run.status.value})",
        )

    if not await _report_exists(session, simulation_run_id):
        raise MissingReportError(ERROR_MISSING_REPORT, "bu SimulationRun icin Report yok")

    personas = await _load_personas(session, simulation_run_id)
    expected_total = (run.input_snapshot or {}).get("persona_count")
    _validate_persona_set(personas, expected_total)

    # --- Buradan itibaren tum on kosullar gecti; simdi (ve yalnizca simdi)
    # ORM nesnelerini kur. Stage 1 input_hash/idempotency_key, PAYLASILAN saf
    # yardimcilarla, stage'i CALISTIRMADAN hesaplanir.
    evidence_source = build_evidence_stage_source(result=run.result, input_snapshot=run.input_snapshot)
    evidence_input_hash = evidence_stage_source_input_hash(evidence_source)
    evidence_idempotency_key = deterministic_stage_idempotency_key(
        simulation_run_id=simulation_run_id,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        input_hash=evidence_input_hash,
    )

    pipeline_run = AIPipelineRun(
        organization_id=organization_id,
        simulation_run_id=simulation_run_id,
        status=AIPipelineStatus.QUEUED,
    )
    evidence_stage = AIPipelineStage(
        pipeline_run=pipeline_run,
        stage_type=AIPipelineStageType.EVIDENCE_PREPARATION,
        status=AIPipelineStageStatus.QUEUED,
        batch_index=None,
        attempt_count=0,
        input_hash=evidence_input_hash,
        idempotency_key=evidence_idempotency_key,
    )

    # 5) Es-zamanli cift-cagri yarisina karsi SAVEPOINT'li insert.
    try:
        async with session.begin_nested():
            session.add(pipeline_run)
            session.add(evidence_stage)
            await session.flush()
    except IntegrityError:
        # UNIQUE(simulation_run_id) ihlali: baska bir cagri araya girdi.
        # SAVEPOINT geri alindi; dis transaction bozulmadi. Mevcut satiri don.
        existing = await _load_existing_pipeline_run(session, simulation_run_id)
        if existing is None:
            raise
        return existing

    return pipeline_run


__all__ = [
    "AIPipelineInitializationError",
    "AINotRequestedError",
    "SimulationRunNotFoundError",
    "SimulationRunNotEligibleError",
    "MissingReportError",
    "InvalidPersonaSetError",
    "PopulationTotalMismatchError",
    "ERROR_AI_NOT_REQUESTED",
    "ERROR_SIMULATION_RUN_NOT_FOUND",
    "ERROR_SIMULATION_RUN_NOT_ELIGIBLE",
    "ERROR_MISSING_REPORT",
    "ERROR_INVALID_PERSONA_SET",
    "ERROR_POPULATION_TOTAL_MISMATCH",
    "initialize_ai_pipeline",
]
