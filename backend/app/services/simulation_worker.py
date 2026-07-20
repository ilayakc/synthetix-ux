"""Simulasyon calistirma durum makinesi: queued -> running -> succeeded/failed/cancelled.

Bu modul heuristic motoru (`app.engine.baseline`) is akisina baglar:

- `claim_next_queued_runs`: bekleyen isleri `SELECT ... FOR UPDATE SKIP
  LOCKED` ile kilitleyerek `running`'e gecirir (birden fazla worker sureci
  ayni isi iki kez almaz).
- `process_run`: motoru calistirir, ilerlemeyi asamalar halinde Redis'e
  (gecici) ve DB'ye (kalici) yazar, basarida entitlement/Chip
  rezervasyonunu tuketir (`consume_*`), basarisizlik/iptalde serbest
  birakir (`release_*`) - bkz. app.services.entitlements / chip_ledger.
- `cancel_run` / `retry_run`: API tarafindan cagrilan kullanici eylemleri.
- `reap_stale_running_runs`: worker cokerse "running"de takili kalan
  isleri yeniden kuyruga alir veya deneme sinirina ulasmissa basarisiz
  sayar.

Idempotency: `consume_entitlement`/`consume_reservation` ve
`release_entitlement`/`release_reservation` zaten idempotenttir (tekrar
cagrildiginda hata firlatmadan mevcut durumu dondurur/veya durum uyumsuzsa
acik bir hata firlatir); bu modul o hatalari (ornegin bir A/B testinin
diger varyanti zaten tuketmisse) sessizce yutar - boylece ayni rezervasyonu
paylasan birden fazla calistirma (`launch_run_id`) icin cift tuketim/cift
serbest birakma olusmaz.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import async_session_maker
from app.engine.advanced_modules import (
    ModuleInputError,
    run_campaign_cta_analysis,
    run_synthetic_attention_estimate,
)
from app.engine.baseline import SimulationInputError, run_baseline_simulation
from app.engine.rules_config import CURRENT_RULES_VERSION
from app.models.reports import Report
from app.models.simulations import SimulationRun, SimulationStatus
from app.services import chip_ledger, device_network_analysis, simulation_progress
from app.services import entitlements as entitlements_service
from app.services.exceptions import (
    ChipReservationNotFoundError,
    EntitlementNotFoundError,
    InvalidChipReservationStateError,
    InvalidEntitlementStateError,
    InvalidSimulationStateError,
    ModuleProcessingError,
    SimulationRunNotFoundError,
)

logger = logging.getLogger("synthetix.simulation_worker")

CLAIM_BATCH_SIZE = 5
STALE_RUNNING_TIMEOUT_SECONDS = 300
MAX_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(UTC)


async def _set_progress(
    session: AsyncSession, run: SimulationRun, *, status: SimulationStatus, percent: int, message: str
) -> None:
    run.progress_percent = percent
    run.progress_message = message
    await session.flush()
    await simulation_progress.publish_progress(run.id, status=status.value, percent=percent, message=message)


async def claim_next_queued_runs(session: AsyncSession, limit: int = CLAIM_BATCH_SIZE) -> list[SimulationRun]:
    """Bekleyen (queued) isleri kilitleyip 'running' durumuna gecirir."""

    result = await session.execute(
        select(SimulationRun)
        .where(SimulationRun.status == SimulationStatus.QUEUED)
        .order_by(SimulationRun.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    runs = list(result.scalars().all())
    for run in runs:
        run.status = SimulationStatus.RUNNING
        run.started_at = _now()
        run.attempt_count += 1
        run.progress_percent = 0
        run.progress_message = "Kuyruktan alindi, isleniyor"
    await session.flush()
    return runs


async def _is_cancel_requested(session: AsyncSession, run_id: uuid.UUID) -> bool:
    result = await session.execute(select(SimulationRun.cancel_requested).where(SimulationRun.id == run_id))
    return bool(result.scalar_one_or_none())


async def _consume_reservation_for_run(session: AsyncSession, run: SimulationRun) -> None:
    # NOT: bagimsiz `if`ler (elif degil) - bkz. app.services.test_wizard.
    # launch_draft: bir run hem `free_entitlement_feature_key` (temel test
    # ucretsiz hakki) HEM DE `chip_reservation_id` (secili gelismis
    # modullerin Chip'i) tasiyabilir; ikisi de varsa ikisi de tuketilmelidir.
    if run.free_entitlement_feature_key:
        assert run.launch_run_id is not None
        await entitlements_service.consume_entitlement(
            session, run.organization_id, run.free_entitlement_feature_key, run.launch_run_id
        )
    if run.chip_reservation_id:
        await chip_ledger.consume_reservation(
            session, run.organization_id, run.chip_reservation_id, f"simulasyon basarili: {run.id}"
        )


async def _release_reservation_for_run(session: AsyncSession, run: SimulationRun) -> None:
    # Ayni gerekce (bagimsiz `if`ler): bkz. `_consume_reservation_for_run`.
    try:
        if run.free_entitlement_feature_key:
            assert run.launch_run_id is not None
            await entitlements_service.release_entitlement(
                session, run.organization_id, run.free_entitlement_feature_key, run.launch_run_id
            )
    except (
        InvalidEntitlementStateError,
        EntitlementNotFoundError,
    ):
        # Ayni rezervasyonu paylasan bir kardes calistirma (ayni
        # launch_run_id, ornegin A/B'nin diger varyanti) zaten tuketmis
        # olabilir; bu durumda serbest birakma islemi sessizce atlanir
        # (odeme zaten gerceklesmis sayilir, ikinci kez tuketim/iade
        # uretilmez).
        logger.info("run %s icin entitlement rezervasyonu zaten sonuclanmis, release atlandi", run.id)

    try:
        if run.chip_reservation_id:
            await chip_ledger.release_reservation(
                session, run.organization_id, run.chip_reservation_id, f"simulasyon basarisiz/iptal: {run.id}"
            )
    except (
        InvalidChipReservationStateError,
        ChipReservationNotFoundError,
    ):
        # Ayni rezervasyonu paylasan bir kardes calistirma (ayni
        # launch_run_id, ornegin A/B'nin diger varyanti) zaten tuketmis
        # olabilir; bu durumda serbest birakma islemi sessizce atlanir
        # (odeme zaten gerceklesmis sayilir, ikinci kez tuketim/iade
        # uretilmez).
        logger.info("run %s icin Chip rezervasyonu zaten sonuclanmis, release atlandi", run.id)


async def _process_selected_modules(run: SimulationRun) -> dict:
    """Sihirbazda secilen gelismis modulleri (varsa) isler ve sonuclarini dondurur.

    `input_snapshot["modules"]` bos/eksikse bos sozluk doner (hicbir yan
    etki/analyzer cagrisi yapilmaz). Bilinmeyen bir modul anahtari (teoride
    olusmamali - bkz. `app.services.test_wizard.validate_patch_fields`)
    sessizce atlanir, is dusurulmez.
    """

    modules: list[str] = run.input_snapshot.get("modules") or []
    module_results: dict = {}

    for module_key in modules:
        if module_key == "campaign_cta_test":
            module_results[module_key] = run_campaign_cta_analysis(
                run.input_snapshot, run.deterministic_seed, rules_version=CURRENT_RULES_VERSION
            )
        elif module_key == "synthetic_attention_estimate":
            module_results[module_key] = run_synthetic_attention_estimate(
                run.input_snapshot, run.deterministic_seed, rules_version=CURRENT_RULES_VERSION
            )
        elif module_key == "network_device_test":
            url = run.input_snapshot.get("url")
            if not url or not isinstance(url, str):
                raise ModuleInputError("input_snapshot.url gereklidir (network_device_test)")
            module_results[module_key] = await device_network_analysis.run_network_device_test(url)

    return module_results


async def process_run(session: AsyncSession, run: SimulationRun) -> None:
    """Tek bir 'running' calistirmayi sonuna kadar isler (basarili/basarisiz/iptal)."""

    await _set_progress(
        session, run, status=SimulationStatus.RUNNING, percent=10, message="Sayfa ozellikleri okunuyor"
    )

    if await _is_cancel_requested(session, run.id):
        await _finalize_cancelled(session, run)
        return

    try:
        result = run_baseline_simulation(
            run.input_snapshot, run.deterministic_seed, rules_version=CURRENT_RULES_VERSION
        )
    except SimulationInputError as exc:
        await _finalize_failed(session, run, error=str(exc))
        return

    await _set_progress(
        session, run, status=SimulationStatus.RUNNING, percent=40, message="Gelismis moduller isleniyor"
    )

    if await _is_cancel_requested(session, run.id):
        await _finalize_cancelled(session, run)
        return

    try:
        module_results = await _process_selected_modules(run)
    except (ModuleInputError, ModuleProcessingError) as exc:
        # Herhangi bir secili modul kurtarilamaz bir hatayla basarisiz
        # olursa TUM run 'failed' isaretlenir; boylece kismi bir sonuc asla
        # 'succeeded' olarak isaretlenmez ve rezervasyon (Chip/ucretsiz hak)
        # asagida _finalize_failed -> _release_reservation_for_run ile
        # (zaten idempotent) serbest birakilir - Chip haksiz tuketilmez.
        await _finalize_failed(session, run, error=str(exc))
        return

    if module_results:
        result["modules"] = module_results
        attention = module_results.get("synthetic_attention_estimate")
        if attention is not None:
            result["attention_grid"] = attention["grid"]

    await _set_progress(
        session, run, status=SimulationStatus.RUNNING, percent=70, message="Metrikler hesaplandi"
    )

    if await _is_cancel_requested(session, run.id):
        await _finalize_cancelled(session, run)
        return

    run.result = result
    run.model_version = result["engine_version"]
    run.rules_version = result["rules_version"]
    run.fixture_version = result["fixture_version"]
    run.status = SimulationStatus.SUCCEEDED
    run.finished_at = _now()
    run.progress_percent = 100
    run.progress_message = "Tamamlandi"
    await session.flush()

    await _consume_reservation_for_run(session, run)

    session.add(
        Report(
            organization_id=run.organization_id,
            simulation_run_id=run.id,
            title="Sentetik simulasyon sonucu",
            content=result,
        )
    )
    await session.flush()

    await simulation_progress.publish_progress(
        run.id, status=SimulationStatus.SUCCEEDED.value, percent=100, message="Tamamlandi"
    )


async def _finalize_failed(session: AsyncSession, run: SimulationRun, *, error: str) -> None:
    run.status = SimulationStatus.FAILED
    run.error = error
    run.finished_at = _now()
    run.progress_message = "Basarisiz"
    await session.flush()
    await _release_reservation_for_run(session, run)
    await simulation_progress.publish_progress(
        run.id, status=SimulationStatus.FAILED.value, percent=run.progress_percent, message=error
    )


async def _finalize_cancelled(session: AsyncSession, run: SimulationRun) -> None:
    run.status = SimulationStatus.CANCELLED
    run.finished_at = _now()
    run.progress_message = "Kullanici tarafindan iptal edildi"
    await session.flush()
    await _release_reservation_for_run(session, run)
    await simulation_progress.publish_progress(
        run.id,
        status=SimulationStatus.CANCELLED.value,
        percent=run.progress_percent,
        message=run.progress_message,
    )


async def _get_owned_run(
    session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun:
    result = await session.execute(select(SimulationRun).where(SimulationRun.id == run_id).with_for_update())
    run = result.scalar_one_or_none()
    if run is None or run.organization_id != organization_id:
        raise SimulationRunNotFoundError(f"Calistirma bulunamadi: {run_id}")
    return run


async def cancel_run(session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID) -> SimulationRun:
    """Bir calistirmayi iptal eder. Terminal durumdaysa hicbir yan etki uretmeden dondurur."""

    run = await _get_owned_run(session, organization_id, run_id)

    if run.status in (SimulationStatus.SUCCEEDED, SimulationStatus.FAILED, SimulationStatus.CANCELLED):
        return run

    if run.status == SimulationStatus.QUEUED:
        await _finalize_cancelled(session, run)
        return run

    # RUNNING: worker'in bir sonraki kontrol noktasinda gormesi icin bayrak
    # birakilir; is hemen durdurulmaz (ic tutarliligi bozmamak icin).
    run.cancel_requested = True
    await session.flush()
    return run


async def retry_run(session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID) -> SimulationRun:
    """Basarisiz bir calistirmayi, rezervasyonu yeniden yaparak kuyruga alir."""

    run = await _get_owned_run(session, organization_id, run_id)

    if run.status != SimulationStatus.FAILED:
        raise InvalidSimulationStateError(
            f"Yalnizca 'failed' durumundaki calistirmalar yeniden denenebilir (mevcut: {run.status.value})"
        )

    # NOT: bagimsiz `if`ler (elif degil) - bkz. `_consume_reservation_for_run`
    # ve app.services.test_wizard.launch_draft: bir run hem ucretsiz hak hem
    # de (secili gelismis moduller icin) bir Chip rezervasyonu tasiyabilir.
    if run.free_entitlement_feature_key:
        assert run.launch_run_id is not None
        await entitlements_service.reserve_entitlement(
            session, run.organization_id, run.free_entitlement_feature_key, run.launch_run_id
        )
    if run.chip_reservation_id:
        previous = await session.get(chip_ledger.ChipReservation, run.chip_reservation_id)
        amount = previous.amount if previous is not None else 0
        if amount > 0:
            new_reservation = await chip_ledger.reserve_chips(
                session,
                run.organization_id,
                amount,
                f"simulasyon yeniden deneme: {run.id}",
                run_id=run.launch_run_id,
                idempotency_key=f"simulation-retry:{run.id}:{run.attempt_count + 1}",
            )
            run.chip_reservation_id = new_reservation.id

    run.status = SimulationStatus.QUEUED
    run.error = None
    run.result = None
    run.progress_percent = 0
    run.progress_message = "Yeniden kuyruga alindi"
    run.started_at = None
    run.finished_at = None
    run.cancel_requested = False
    await session.flush()
    return run


async def reap_stale_running_runs(
    session: AsyncSession,
    *,
    timeout_seconds: int = STALE_RUNNING_TIMEOUT_SECONDS,
    max_attempts: int = MAX_ATTEMPTS,
) -> int:
    """'running' durumunda takili kalmis (worker cokmesi vb.) isleri kurtarir."""

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    result = await session.execute(
        select(SimulationRun)
        .where(SimulationRun.status == SimulationStatus.RUNNING, SimulationRun.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    stale_runs = list(result.scalars().all())

    for run in stale_runs:
        if run.attempt_count < max_attempts:
            run.status = SimulationStatus.QUEUED
            run.progress_percent = 0
            run.progress_message = "Zaman asimi, yeniden kuyruga alindi"
            run.started_at = None
        else:
            run.status = SimulationStatus.FAILED
            run.error = "Zaman asimi: maksimum deneme sayisina ulasildi"
            run.finished_at = _now()
            await session.flush()
            await _release_reservation_for_run(session, run)
            continue
        await session.flush()

    return len(stale_runs)


async def _process_claimed_run(run_id: uuid.UUID) -> None:
    """Tek bir kilitli calistirmayi kendi oturumunda isler; hata halinde 'failed' isaretler."""

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(SimulationRun).where(SimulationRun.id == run_id).with_for_update()
            )
            run = result.scalar_one_or_none()
            if run is None or run.status != SimulationStatus.RUNNING:
                return
            await process_run(session, run)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("process_run basarisiz oldu (run_id=%s)", run_id)

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(SimulationRun).where(SimulationRun.id == run_id).with_for_update()
            )
            run = result.scalar_one_or_none()
            if run is not None and run.status == SimulationStatus.RUNNING:
                await _finalize_failed(session, run, error="Beklenmeyen motor hatasi")
                await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("run %s icin basarisizlik kaydi da basarisiz oldu", run_id)


async def run_queue_cycle() -> None:
    """arq cron girdisi: bekleyen isleri alir ve isler. Kendi oturumunu acar."""

    async with async_session_maker() as session:
        try:
            runs = await claim_next_queued_runs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("claim_next_queued_runs basarisiz oldu")
            return

    for run in runs:
        await _process_claimed_run(run.id)


async def run_reap_cycle() -> None:
    """arq cron girdisi: 'running'de takili kalmis isleri kurtarir. Kendi oturumunu acar."""

    async with async_session_maker() as session:
        try:
            await reap_stale_running_runs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("reap_stale_running_runs basarisiz oldu")


__all__ = [
    "CLAIM_BATCH_SIZE",
    "STALE_RUNNING_TIMEOUT_SECONDS",
    "MAX_ATTEMPTS",
    "claim_next_queued_runs",
    "process_run",
    "cancel_run",
    "retry_run",
    "reap_stale_running_runs",
    "run_queue_cycle",
    "run_reap_cycle",
]
