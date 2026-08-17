"""AI pipeline YASAM DONGUSU (lifecycle) reconciliation servisi (Faz 3C.2B2).

Bu modul, basariyla tamamlanmis (SUCCEEDED) ve `ai_report` secilmis launch
gruplari icin iki BAGIMSIZ, cron-guvenli reconciliation cycle'i saglar:

- `run_ai_pipeline_initialization_cycle`: eligible launch gruplari icin
  `app.services.ai_pipeline.orchestration.initialize_ai_pipeline`i (per-run,
  ayni grup icindeki TUM varyantlar icin TEK bir transaction'da) cagirir.
- `run_ai_reservation_settlement_cycle`: paylasilan AI Chip rezervasyonunu,
  grubun AI pipeline sonuclarina gore `consume_reservation`/`release_reservation`
  ile CONSUMED/RELEASED yapar.

Mimari karar (bkz. gorev talimati "MIMARI KARAR"): pipeline initialization ve
Chip settlement, `app.services.simulation_worker`in UZUN baseline-run
transaction'larina GOMULMEZ - baseline commit'inden SONRA process cokerse,
sonraki cron calismasi (bu modul) isi tamamlayabilir. Bu nedenle:

- HICBIR fonksiyon burada provider/network cagirmaz, stage CALISTIRMAZ, stage
  mantigini KOPYALAMAZ (`orchestration.initialize_ai_pipeline`/`chip_ledger.
  consume_reservation`/`release_reservation` cekirdeklerini YENIDEN KULLANIR).
- Her cycle sinirli sayida (bkz. `limit`) launch grubunu, deterministik sirada
  (`launch_run_id` ASC) isler; sonsuz loop/sleep ICERMEZ.
- Coklu worker'a guvenlidir: her grup, `app.services.simulation_worker.
  _resolve_launch_group`daki AYNI `pg_advisory_xact_lock(hashtextextended(...))`
  desenini (FARKLI bir "salt" ile - bkz. `_INIT_LOCK_SALT`/`_SETTLEMENT_LOCK_SALT`)
  kullanarak kilitlenir; boylece ayni deadlock sinifina (coklu-satir sirali
  `FOR UPDATE`) asla dusulmez.

Hata siniflandirmasi (bkz. gorev talimati "5. INIT HATA SINIFLANDIRMASI"):
`orchestration.AIPipelineInitializationError` (ve TUM alt siniflari) KALICI
(permanent) domain/integrity hatalaridir - yakalanir, guvenli loglanir, grup
transaction'i (yarim pipeline birakmadan) geri alinir ve paylasilan AI
rezervasyonu `release_reservation` ile serbest birakilir. Bunun DISINDAKI HER
istisna (DB baglanti hatasi, beklenmeyen programlama hatasi vb.) GECICI
(transient) sayilir: rezervasyon RESERVED kalir, hata guvenli loglanir ve
arq gorunurlugu icin YENIDEN FIRLATILIR (bu cycle invocation'i orada durur;
BU ANA KADAR basariyla islenmis gruplarin sonuclari zaten kendi ayri
transaction'larinda commit edilmis oldugundan kaybolmaz).

ADJUSTMENT/refund ledger turu KULLANILMAZ - mevcut reserve -> consume | release
yasam dongusunun idempotent durum davranisi (bkz. `chip_ledger.
consume_reservation`/`release_reservation` dokstring'i) tek finansal
otoritedir.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_maker
from app.logging_config import get_logger
from app.models.ai_pipeline import AIPipelineRun, AIPipelineStatus
from app.models.billing import ChipReservation, ChipReservationStatus
from app.models.simulations import SimulationRun, SimulationStatus
from app.services import chip_ledger
from app.services.ai_pipeline import orchestration
from app.services.exceptions import ChipReservationNotFoundError, InvalidChipReservationStateError
from app.services.pricing import AI_REPORT_MODULE_KEY

logger = get_logger("ai_pipeline_lifecycle")

# --- Sabitler ----------------------------------------------------------------

# Bir cycle invocation'inda islenecek MAKSIMUM launch grubu sayisi (sinirli/
# bounded calisma - gorev talimati Part 2/6). Deger, mevcut "tek invocation'da
# sinirli is" konvansiyonuyla (bkz. app.services.simulation_worker.
# CLAIM_BATCH_SIZE, app.services.page_analysis benzeri) tutarli, orta buyuklukte
# bir sabittir.
DEFAULT_INIT_GROUP_LIMIT: Final = 20
DEFAULT_SETTLEMENT_GROUP_LIMIT: Final = 20

# `pg_advisory_xact_lock(hashtextextended(key, salt))` "salt" degerleri.
# `app.services.simulation_worker._resolve_launch_group` AYNI `launch_run_id`
# anahtarini salt=0 ile kilitler (baseline grup muhasebesi). Bu modulun iki
# cycle'i KASITLI olarak FARKLI salt degerleri kullanir - boylece init/
# settlement/baseline birbirini GEREKSIZ yere bloklamaz (uc ayri kilit
# adres alani), ama HER BIRI kendi ic calismalarinda TEK bir grubu ayni anda
# yalnizca bir cagiranin islemesini garanti eder (coklu worker guvenligi).
_INIT_LOCK_SALT: Final = 1
_SETTLEMENT_LOCK_SALT: Final = 2

# Kalici init hatalarinda FAILED pipeline stage'ine yazilan, guvenli/sabit hata
# kodlari (rezervasyon butunluk hatalari - `orchestration` domain kodlarindan
# AYRI). Frontend bunlari kontrollu bir "basarisiz" durumu olarak gosterir.
_ERROR_INCONSISTENT_AI_RESERVATION: Final = "inconsistent_ai_reservation"
_ERROR_AI_RESERVATION_NOT_FOUND: Final = "ai_reservation_not_found"

# AI pipeline sonucunun rezervasyonu RELEASE etmesini gerektiren terminal
# "basarisiz" durumlar (gorev talimati Part 7.B).
_RELEASE_TRIGGERING_STATUSES: Final = (
    AIPipelineStatus.FAILED,
    AIPipelineStatus.CANCELLED,
    AIPipelineStatus.PARTIAL,
)


def _ai_report_requested(run: SimulationRun) -> bool:
    modules = (run.input_snapshot or {}).get("modules") or []
    return AI_REPORT_MODULE_KEY in modules


async def _lock_launch_group(session: AsyncSession, launch_run_id: uuid.UUID, *, salt: int) -> None:
    """`app.services.simulation_worker._resolve_launch_group` ile AYNI
    desende, TEK bir advisory lock (satir sirasindan bagimsiz) alir - coklu
    worker/cycle es-zamanli calismasinda deadlock SINIFI yapisal olarak
    ortadan kalkar (bkz. modul dokstring'i)."""

    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(launch_run_id), salt))))


async def _load_group_runs(session: AsyncSession, launch_run_id: uuid.UUID) -> list[SimulationRun]:
    stmt = (
        select(SimulationRun).where(SimulationRun.launch_run_id == launch_run_id).order_by(SimulationRun.id)
    )
    return list((await session.execute(stmt)).scalars().all())


# =============================================================================
# 1) PENDING AI PIPELINE INITIALIZATION CYCLE
# =============================================================================


@dataclass(frozen=True)
class InitializationCycleResult:
    """Tek bir `run_ai_pipeline_initialization_cycle` cagrisinin GUVENLI
    (secretsiz) ozeti - yalnizca sayimlar/sureler (bkz. gorev talimati
    "10. GUVENLI LOGGING")."""

    groups_scanned: int = 0
    groups_initialized: int = 0
    pipelines_initialized: int = 0
    groups_not_ready: int = 0
    groups_permanent_error: int = 0
    duration_ms: float = 0.0


class _InitOutcomeKind:
    INITIALIZED = "initialized"
    NOT_READY = "not_ready"
    PERMANENT_ERROR = "permanent_error"


@dataclass(frozen=True)
class _InitGroupOutcome:
    kind: str
    pipeline_count: int = 0
    reservation_id: uuid.UUID | None = None
    error_code: str | None = None


async def _find_pending_init_launch_groups(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """`ai_report` icin AI Chip rezerve edilmis, SUCCEEDED VE HENUZ pipeline'i
    olmayan EN AZ bir `SimulationRun`in ait oldugu launch gruplarini
    (`launch_run_id`), deterministik sirada VE sinirli sayida dondurur.

    Bu YALNIZCA bir ON-FILTRE'dir (SQL'de JSON `modules` icerigi
    sorgulanmaz - `ai_chip_reservation_id IS NOT NULL` zaten yalnizca
    `ai_report` secildiginde doldurulur, bkz. app.services.test_wizard.
    launch_draft); grubun TAM uygunlugu (`ai_report` modul kontrolu DAHIL)
    `_process_one_init_group` icinde, kilitli/taze veriyle YENIDEN dogrulanir.
    """

    no_pipeline_yet = ~exists(
        select(AIPipelineRun.id).where(AIPipelineRun.simulation_run_id == SimulationRun.id)
    )
    stmt = (
        select(SimulationRun.launch_run_id)
        .where(
            SimulationRun.status == SimulationStatus.SUCCEEDED,
            SimulationRun.ai_chip_reservation_id.is_not(None),
            SimulationRun.launch_run_id.is_not(None),
            no_pipeline_yet,
        )
        .distinct()
        .order_by(SimulationRun.launch_run_id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [row for row in rows if row is not None]


async def _release_for_permanent_error(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    organization_id: uuid.UUID,
    reservation_id: uuid.UUID,
    reason: str,
) -> None:
    """Kalici (permanent) bir init hatasi sonrasi paylasilan AI rezervasyonunu
    AYRI/taze bir transaction'da serbest birakir. `release_reservation`
    idempotent oldugu icin (zaten RELEASED/CONSUMED ise guvenle atlanir/hata
    firlatir) coklu worker'in ayni hatayi es-zamanli islemesi guvenlidir."""

    async with session_maker() as session:
        try:
            await chip_ledger.release_reservation(session, organization_id, reservation_id, reason)
            await session.commit()
        except (InvalidChipReservationStateError, ChipReservationNotFoundError) as exc:
            await session.rollback()
            logger.warning(
                "ai pipeline init: kalici hata sonrasi rezervasyon beklenen durumda degil",
                extra={"reservation_id": str(reservation_id), "detail_code": exc.__class__.__name__},
            )


async def _record_group_initialization_failure(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    organization_id: uuid.UUID,
    run_ids: list[uuid.UUID],
    error_code: str,
) -> None:
    """Kalici init hatasi sonrasi, gruptaki HER run icin AYRI/taze bir
    transaction'da status=FAILED bir pipeline kaydi olusturur (idempotent).

    Boylece `ai_report` secilmis basarili bir run, initialization kalici olarak
    basarisiz oldugunda ACIKLAMASIZ ve SONSUZ `ai_pipeline_not_found` (404)
    durumunda kalmaz - GET /ai-pipeline artik kontrollu bir FAILED durumu +
    guvenli stage error_code dondurur. Kayit basarisiz olsa BILE (beklenmeyen)
    rezervasyon serbest birakma adimi bloke edilmez - yalnizca guvenli loglanir."""

    async with session_maker() as session:
        try:
            for run_id in run_ids:
                await orchestration.record_initialization_failure(
                    session,
                    organization_id=organization_id,
                    simulation_run_id=run_id,
                    error_code=error_code,
                )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "ai pipeline init: kalici hata kaydi (FAILED pipeline) olusturulamadi",
                extra={"error_code": error_code},
            )


async def _process_one_init_group(
    session_maker: async_sessionmaker[AsyncSession], launch_run_id: uuid.UUID
) -> _InitGroupOutcome:
    async with session_maker() as session:
        await _lock_launch_group(session, launch_run_id, salt=_INIT_LOCK_SALT)
        runs = await _load_group_runs(session, launch_run_id)
        if not runs:
            await session.rollback()
            return _InitGroupOutcome(kind=_InitOutcomeKind.NOT_READY)

        organization_ids = {r.organization_id for r in runs}
        if len(organization_ids) != 1:
            # Teorik savunma durumu: launch_draft TUM kardesleri TEK bir
            # transaction'da, AYNI organization_id ile olusturur (bkz. modul
            # dokstring'i). Ambigu oldugu icin HICBIR rezervasyona
            # dokunmadan guvenle atlanir.
            await session.rollback()
            logger.warning(
                "ai pipeline init: launch grubu icinde organizasyon tutarsizligi, atlaniyor",
                extra={"launch_group_id": str(launch_run_id)},
            )
            return _InitGroupOutcome(kind=_InitOutcomeKind.PERMANENT_ERROR)
        organization_id = next(iter(organization_ids))

        if not all(r.status == SimulationStatus.SUCCEEDED for r in runs):
            # En az bir varyant henuz SUCCEEDED degil (veya FAILED/CANCELLED) -
            # bu grup icin initialization YAPILMAZ (spec test 5/6). Baseline
            # grup basarisizsa AI rezervasyonu zaten `_resolve_launch_group`
            # tarafindan RELEASED edilmis/edilecektir - burada dokunulmaz.
            await session.rollback()
            return _InitGroupOutcome(kind=_InitOutcomeKind.NOT_READY)

        if not any(_ai_report_requested(r) for r in runs):
            # `ai_chip_reservation_id` dolu ama modul listesinde `ai_report`
            # yok (beklenmeyen veri durumu) - hicbir sey yapilmaz (spec test 12
            # ile ayni "no-op" davranisi, hata FIRLATILMAZ).
            await session.rollback()
            return _InitGroupOutcome(kind=_InitOutcomeKind.NOT_READY)

        # Rollback SONRASI ORM attribute'lari expire olacagi icin run id'leri
        # SIMDI (duz bir liste olarak) yakalanir - kalici hata/backfill kaydinda
        # lazy-load (MissingGreenlet) riski olmadan kullanilir.
        group_run_ids = [r.id for r in runs]

        reservation_ids = {r.ai_chip_reservation_id for r in runs}
        if None in reservation_ids or len(reservation_ids) != 1:
            # Bir/birden fazla varyantta rezervasyon eksik VEYA varyantlar
            # FARKLI rezervasyon id'leri tasiyor (spec test 9/10) - kalici
            # integrity hatasi: bulunabilen TUM (non-null) id'ler serbest
            # birakilir. Ayrica gruba GORUNUR bir FAILED pipeline kaydi yazilir
            # ki bu run'lar da sonsuza kadar `ai_pipeline_not_found` (404)
            # kalmasin (Part 2).
            await session.rollback()
            concrete_ids = {rid for rid in reservation_ids if rid is not None}
            for concrete_reservation_id in concrete_ids:
                await _release_for_permanent_error(
                    session_maker,
                    organization_id=organization_id,
                    reservation_id=concrete_reservation_id,
                    reason="ai pipeline init: tutarsiz/eksik AI rezervasyon id'si",
                )
            await _record_group_initialization_failure(
                session_maker,
                organization_id=organization_id,
                run_ids=group_run_ids,
                error_code=_ERROR_INCONSISTENT_AI_RESERVATION,
            )
            return _InitGroupOutcome(
                kind=_InitOutcomeKind.PERMANENT_ERROR, error_code=_ERROR_INCONSISTENT_AI_RESERVATION
            )
        (maybe_reservation_id,) = reservation_ids
        assert maybe_reservation_id is not None
        reservation_id: uuid.UUID = maybe_reservation_id

        reservation = await session.get(ChipReservation, reservation_id)
        if reservation is None or reservation.organization_id != organization_id:
            await session.rollback()
            logger.warning(
                "ai pipeline init: AI rezervasyonu bulunamadi/organizasyon uyumsuz",
                extra={"launch_group_id": str(launch_run_id)},
            )
            await _record_group_initialization_failure(
                session_maker,
                organization_id=organization_id,
                run_ids=group_run_ids,
                error_code=_ERROR_AI_RESERVATION_NOT_FOUND,
            )
            return _InitGroupOutcome(
                kind=_InitOutcomeKind.PERMANENT_ERROR, error_code=_ERROR_AI_RESERVATION_NOT_FOUND
            )

        # Rezervasyon durumuna gore uc yol:
        #  - RESERVED: normal (henuz sonuclanmamis) grup -> standart init.
        #  - RELEASED + pipeline YOK (on-filtre `no_pipeline_yet`) + TUM run'lar
        #    SUCCEEDED + `ai_report` secili => bu, ESKI bozuk permanent-init
        #    davranisindan (rezervasyonu release edip pipeline kaydi olusturmayan)
        #    KALMIS bir kayittir (Part 2 legacy reconciliation). Init YENIDEN
        #    degerlendirilir; veri artik uygunsa pipeline RECOVER edilir
        #    (rezervasyona DOKUNULMAZ -> ikinci kez ucret/Chip ALINMAZ), hala
        #    gecersizse GORUNUR bir FAILED kayit yazilir. Boylece run sonsuza kadar
        #    NOT_READY/404 kalmaz.
        #  - CONSUMED (veya beklenmeyen diger): mesru sekilde sonuclanmis kabul
        #    edilir - init YAPILMAZ (spec test 11 davranisi korunur).
        reservation_status_value = reservation.status.value
        if reservation.status == ChipReservationStatus.RESERVED:
            reservation_is_reserved = True
            is_legacy_recovery = False
        elif reservation.status == ChipReservationStatus.RELEASED:
            reservation_is_reserved = False
            is_legacy_recovery = True
        else:
            await session.rollback()
            return _InitGroupOutcome(kind=_InitOutcomeKind.NOT_READY)

        # --- Atomik grup initialization: TUM varyantlar AYNI transaction'da.
        try:
            for run in runs:
                await orchestration.initialize_ai_pipeline(
                    session,
                    organization_id=organization_id,
                    simulation_run_id=run.id,
                    ai_requested=True,
                )
        except orchestration.AIPipelineInitializationError as exc:
            # KALICI (permanent) domain/integrity hatasi (bkz. `orchestration`
            # modulunun typed hata hiyerarsisi - Report/Persona eksik/gecersiz
            # vb.). TUM transaction (bu grup icin bu cycle'da eklenen TUM
            # pipeline satirlari DAHIL) geri alinir - yarim pipeline KALMAZ.
            await session.rollback()
            logger.warning(
                "ai pipeline init: kalici init hatasi, grup icin FAILED pipeline kaydediliyor",
                extra={
                    "launch_group_id": str(launch_run_id),
                    "error_code": exc.code,
                    "legacy_recovery": is_legacy_recovery,
                },
            )
            # ONCE kalici hatayi GORUNUR/DURDURULABILIR bir FAILED pipeline olarak
            # persist et (aksi halde frontend sonsuza kadar `ai_pipeline_not_found`
            # gorurdu ve gercek error_code kaybolurdu).
            await _record_group_initialization_failure(
                session_maker,
                organization_id=organization_id,
                run_ids=group_run_ids,
                error_code=exc.code,
            )
            # Rezervasyonu YALNIZCA hala RESERVED ise serbest birak. Legacy
            # recovery'de rezervasyon ZATEN terminal (RELEASED/CONSUMED) - tekrar
            # dokunulmaz (idempotency + cift muhasebe onlenir).
            if reservation_is_reserved:
                await _release_for_permanent_error(
                    session_maker,
                    organization_id=organization_id,
                    reservation_id=reservation_id,
                    reason=f"ai pipeline init: kalici hata ({exc.code})",
                )
            return _InitGroupOutcome(kind=_InitOutcomeKind.PERMANENT_ERROR, error_code=exc.code)

        await session.commit()
        if is_legacy_recovery:
            logger.info(
                "ai pipeline init: legacy grup recover edildi (rezervasyon terminal, yeni ucret yok)",
                extra={"launch_group_id": str(launch_run_id), "reservation_status": reservation_status_value},
            )
        return _InitGroupOutcome(
            kind=_InitOutcomeKind.INITIALIZED, pipeline_count=len(runs), reservation_id=reservation_id
        )


async def run_ai_pipeline_initialization_cycle(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int = DEFAULT_INIT_GROUP_LIMIT,
) -> InitializationCycleResult:
    """Bekleyen (`ai_report` secilmis, SUCCEEDED, henuz pipeline'i olmayan)
    launch gruplarini bulur ve HER biri icin atomik/idempotent AI pipeline
    initialization dener (bkz. modul dokstring'i).

    Tek cagrida EN FAZLA `limit` grup islenir; sonsuz loop/sleep YOKTUR;
    provider/network cagrisi YAPILMAZ. Deterministik sira: `launch_run_id`
    ASC. Beklenmeyen (GECICI/transient) bir hata olursa guvenli loglanir ve
    YENIDEN FIRLATILIR (bu cycle invocation'i o noktada durur; bu ana kadar
    basariyla islenmis gruplarin sonuclari kendi transaction'larinda zaten
    commit edilmis oldugundan KAYBOLMAZ).
    """

    started = time.perf_counter()
    async with session_maker() as session:
        launch_group_ids = await _find_pending_init_launch_groups(session, limit=limit)

    groups_initialized = 0
    pipelines_initialized = 0
    groups_not_ready = 0
    groups_permanent_error = 0

    for launch_group_id in launch_group_ids:
        try:
            outcome = await _process_one_init_group(session_maker, launch_group_id)
        except Exception:
            logger.exception(
                "ai pipeline initialization cycle: grup islenirken beklenmeyen (gecici) hata",
                extra={"task": "ai_pipeline_initialization_cycle", "launch_group_id": str(launch_group_id)},
            )
            raise

        if outcome.kind == _InitOutcomeKind.INITIALIZED:
            groups_initialized += 1
            pipelines_initialized += outcome.pipeline_count
        elif outcome.kind == _InitOutcomeKind.PERMANENT_ERROR:
            groups_permanent_error += 1
        else:
            groups_not_ready += 1

    duration_ms = (time.perf_counter() - started) * 1000
    result = InitializationCycleResult(
        groups_scanned=len(launch_group_ids),
        groups_initialized=groups_initialized,
        pipelines_initialized=pipelines_initialized,
        groups_not_ready=groups_not_ready,
        groups_permanent_error=groups_permanent_error,
        duration_ms=duration_ms,
    )
    logger.info(
        "ai pipeline initialization cycle tamamlandi",
        extra={
            "task": "ai_pipeline_initialization_cycle",
            "groups_scanned": result.groups_scanned,
            "groups_initialized": result.groups_initialized,
            "pipelines_initialized": result.pipelines_initialized,
            "groups_not_ready": result.groups_not_ready,
            "groups_permanent_error": result.groups_permanent_error,
            "duration_ms": result.duration_ms,
        },
    )
    return result


# =============================================================================
# 2) AI RESERVATION SETTLEMENT CYCLE
# =============================================================================


@dataclass(frozen=True)
class SettlementCycleResult:
    """Tek bir `run_ai_reservation_settlement_cycle` cagrisinin GUVENLI ozeti."""

    groups_scanned: int = 0
    groups_consumed: int = 0
    groups_released: int = 0
    groups_waiting: int = 0
    groups_skipped_integrity: int = 0
    duration_ms: float = 0.0


class _SettlementOutcomeKind:
    CONSUMED = "consumed"
    RELEASED = "released"
    WAITING = "waiting"
    SKIPPED_INTEGRITY = "skipped_integrity"


@dataclass(frozen=True)
class _SettlementGroupOutcome:
    kind: str
    reservation_id: uuid.UUID | None = None


async def _find_settlement_candidate_launch_groups(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """AI Chip rezervasyonu HALA `RESERVED` olan launch gruplarini
    (`launch_run_id`), deterministik sirada VE sinirli sayida dondurur.

    Bu bilerek "pipeline sayisi tam mi" kontrolu YAPMAZ (bkz. Part 7.C/7.D) -
    o karar `_process_one_settlement_group` icinde, kilitli/taze veriyle
    verilir; burada yalnizca "hala islenecek bir sey olabilir mi" sorusuna
    ucuz/genis bir on-filtre olarak cevap verilir.
    """

    stmt = (
        select(SimulationRun.launch_run_id)
        .join(ChipReservation, ChipReservation.id == SimulationRun.ai_chip_reservation_id)
        .where(
            SimulationRun.ai_chip_reservation_id.is_not(None),
            SimulationRun.launch_run_id.is_not(None),
            ChipReservation.status == ChipReservationStatus.RESERVED,
        )
        .distinct()
        .order_by(SimulationRun.launch_run_id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [row for row in rows if row is not None]


async def _process_one_settlement_group(
    session_maker: async_sessionmaker[AsyncSession], launch_run_id: uuid.UUID
) -> _SettlementGroupOutcome:
    async with session_maker() as session:
        await _lock_launch_group(session, launch_run_id, salt=_SETTLEMENT_LOCK_SALT)
        runs = await _load_group_runs(session, launch_run_id)
        if not runs:
            await session.rollback()
            return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.WAITING)

        organization_ids = {r.organization_id for r in runs}
        if len(organization_ids) != 1:
            await session.rollback()
            logger.warning(
                "ai reservation settlement: launch grubu icinde organizasyon tutarsizligi, atlaniyor",
                extra={"launch_group_id": str(launch_run_id)},
            )
            return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.SKIPPED_INTEGRITY)
        organization_id = next(iter(organization_ids))

        reservation_ids = {r.ai_chip_reservation_id for r in runs if r.ai_chip_reservation_id is not None}
        if len(reservation_ids) != 1:
            # Ya hicbir run'da AI rezervasyonu yok (on-filtre yaris penceresi
            # nedeniyle degisti - baska bir cycle zaten islemis olabilir) YA
            # DA (beklenmeyen) birden fazla FARKLI id var - ikisi de guvenli
            # sekilde "islem yok" olarak ele alinir.
            await session.rollback()
            kind = (
                _SettlementOutcomeKind.SKIPPED_INTEGRITY
                if len(reservation_ids) > 1
                else _SettlementOutcomeKind.WAITING
            )
            return _SettlementGroupOutcome(kind=kind)
        reservation_id = next(iter(reservation_ids))

        reservation = await session.get(ChipReservation, reservation_id)
        if reservation is None or reservation.organization_id != organization_id:
            await session.rollback()
            return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.SKIPPED_INTEGRITY)

        if reservation.status != ChipReservationStatus.RESERVED:
            # Zaten CONSUMED/RELEASED - no-op (crash-recovery/idempotency,
            # spec test 30/31/33).
            await session.rollback()
            return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.WAITING)

        # Beklenen pipeline sayisi: `ai_report` secilmis (== ai rezervasyonlu)
        # TUM run'lar (spec Part 7 giris cumlesi - A/B'de ikisi de).
        expected_run_ids = [r.id for r in runs if r.ai_chip_reservation_id == reservation_id]
        pipeline_stmt = select(AIPipelineRun.simulation_run_id, AIPipelineRun.status).where(
            AIPipelineRun.simulation_run_id.in_(expected_run_ids)
        )
        pipeline_rows = (await session.execute(pipeline_stmt)).all()

        if len(pipeline_rows) < len(expected_run_ids):
            # Pipeline kayitlari henuz TAM olusturulmamis (Part 7.D) -
            # settlement YAPILMAZ, initialization cycle'in tamamlamasi beklenir.
            await session.rollback()
            return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.WAITING)

        statuses = [status for _sim_run_id, status in pipeline_rows]

        if any(status in _RELEASE_TRIGGERING_STATUSES for status in statuses):
            # Part 7.B: en az bir pipeline FAILED/CANCELLED/PARTIAL -> paylasilan
            # rezervasyon TAMAMEN release edilir (diger pipeline'lar hala
            # RUNNING/QUEUED olsa BILE - grup politikasi geregi ucret tamamen
            # birakilir; digerleri OTOMATIK iptal EDILMEZ, yalnizca finansal
            # sonuc cozulur).
            try:
                await chip_ledger.release_reservation(
                    session,
                    organization_id,
                    reservation_id,
                    "AI işlem hattı sonucu: en az bir varyant başarısız, iptal edilmiş veya kısmi",
                )
                await session.commit()
                return _SettlementGroupOutcome(
                    kind=_SettlementOutcomeKind.RELEASED, reservation_id=reservation_id
                )
            except (InvalidChipReservationStateError, ChipReservationNotFoundError) as exc:
                # Es-zamanli baska bir cycle/worker zaten sonuclandirmis (spec
                # test 32/33: concurrent/duplicate cycle tek ledger sonucu).
                await session.rollback()
                logger.warning(
                    "ai reservation settlement: release sirasinda beklenmeyen durum (idempotent atlandi)",
                    extra={"reservation_id": str(reservation_id), "detail_code": exc.__class__.__name__},
                )
                return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.WAITING)

        if all(status == AIPipelineStatus.SUCCEEDED for status in statuses):
            # Part 7.A: TUM beklenen pipeline'lar SUCCEEDED -> TEK bir CONSUME.
            try:
                await chip_ledger.consume_reservation(
                    session,
                    organization_id,
                    reservation_id,
                    "AI işlem hattı sonucu: tüm varyantlar başarılı",
                )
                await session.commit()
                return _SettlementGroupOutcome(
                    kind=_SettlementOutcomeKind.CONSUMED, reservation_id=reservation_id
                )
            except (InvalidChipReservationStateError, ChipReservationNotFoundError) as exc:
                await session.rollback()
                logger.warning(
                    "ai reservation settlement: consume sirasinda beklenmeyen durum (idempotent atlandi)",
                    extra={"reservation_id": str(reservation_id), "detail_code": exc.__class__.__name__},
                )
                return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.WAITING)

        # Part 7.C: bazi pipeline'lar QUEUED/RUNNING, hic terminal basarisizlik
        # yok -> rezervasyon RESERVED kalir, consume/release YOK.
        await session.rollback()
        return _SettlementGroupOutcome(kind=_SettlementOutcomeKind.WAITING)


async def run_ai_reservation_settlement_cycle(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int = DEFAULT_SETTLEMENT_GROUP_LIMIT,
) -> SettlementCycleResult:
    """Hala `RESERVED` olan AI Chip rezervasyonlarini/launch gruplarini bulur
    ve HER biri icin, grubun AI pipeline sonuclarina gore (bkz. Part 7 kurallari
    A-E) consume/release settlement dener.

    Tek cagrida EN FAZLA `limit` grup islenir; sonsuz loop/sleep YOKTUR;
    provider cagrisi/stage calistirmasi YAPILMAZ. Deterministik sira:
    `launch_run_id` ASC. Beklenmeyen (GECICI) bir hata guvenli loglanir ve
    YENIDEN FIRLATILIR (bkz. `run_ai_pipeline_initialization_cycle` ile ayni
    "bu ana kadarki basarili sonuclar kaybolmaz" garantisi).
    """

    started = time.perf_counter()
    async with session_maker() as session:
        launch_group_ids = await _find_settlement_candidate_launch_groups(session, limit=limit)

    groups_consumed = 0
    groups_released = 0
    groups_waiting = 0
    groups_skipped_integrity = 0

    for launch_group_id in launch_group_ids:
        try:
            outcome = await _process_one_settlement_group(session_maker, launch_group_id)
        except Exception:
            logger.exception(
                "ai reservation settlement cycle: grup islenirken beklenmeyen (gecici) hata",
                extra={"task": "ai_reservation_settlement_cycle", "launch_group_id": str(launch_group_id)},
            )
            raise

        if outcome.kind == _SettlementOutcomeKind.CONSUMED:
            groups_consumed += 1
        elif outcome.kind == _SettlementOutcomeKind.RELEASED:
            groups_released += 1
        elif outcome.kind == _SettlementOutcomeKind.SKIPPED_INTEGRITY:
            groups_skipped_integrity += 1
        else:
            groups_waiting += 1

    duration_ms = (time.perf_counter() - started) * 1000
    result = SettlementCycleResult(
        groups_scanned=len(launch_group_ids),
        groups_consumed=groups_consumed,
        groups_released=groups_released,
        groups_waiting=groups_waiting,
        groups_skipped_integrity=groups_skipped_integrity,
        duration_ms=duration_ms,
    )
    logger.info(
        "ai reservation settlement cycle tamamlandi",
        extra={
            "task": "ai_reservation_settlement_cycle",
            "groups_scanned": result.groups_scanned,
            "groups_consumed": result.groups_consumed,
            "groups_released": result.groups_released,
            "groups_waiting": result.groups_waiting,
            "groups_skipped_integrity": result.groups_skipped_integrity,
            "duration_ms": result.duration_ms,
        },
    )
    return result


__all__ = [
    "DEFAULT_INIT_GROUP_LIMIT",
    "DEFAULT_SETTLEMENT_GROUP_LIMIT",
    "InitializationCycleResult",
    "SettlementCycleResult",
    "run_ai_pipeline_initialization_cycle",
    "run_ai_reservation_settlement_cycle",
]
