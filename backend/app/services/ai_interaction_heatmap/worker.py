"""AI etkilesim isi haritasi worker cekirdegi: claim, provider cagrisi, dogrulama,
kalici sonuc + bounded retry + stale kurtarma + Chip settlement.

MIMARI (bkz. gorev talimati): sonuc rapor GORUNTULENIRKEN hesaplanmaz. Bu modul
ayri bir worker cron'undan cagrilir ve dogru akisi uygular:

  QUEUED heatmap job'i (baseline SUCCEEDED) -> RUNNING (claim) -> gercek OpenAI
  selector'i CAGRILIR -> cikti DOGRULANIR (yalnizca gercek candidate_id;
  koordinat semayla reddedilir) -> DB'ye yazilir (SUCCEEDED | FAILED). Ayri bir
  settlement cycle, heatmap job sonuclarina gore Chip'i CONSUMED/RELEASED yapar.

GUVENLIK/ESZAMANLILIK:
- Provider cagrisi ACIK bir DB transaction'i / advisory lock TUTMADAN yapilir:
  once claim (RUNNING) commit edilir, SONRA network cagrisi yapilir, en son ayri
  bir transaction'da sonuc yazilir (ai_report stage claim deseniyle ayni).
- `interaction_heatmaps.simulation_run_id` UNIQUE + per-run `pg_advisory_xact_
  lock`: es-zamanli iki worker AYNI isi iki kez CALISTIRAMAZ.
- Ayni `fingerprint` icin provider IKINCI kez cagrilmaz: claim sirasinda, ayni
  fingerprint'e sahip mevcut bir SUCCEEDED sonuc varsa REUSE edilir (bkz.
  `_find_reusable_result`).
- Ham provider hatasi kullaniciya sizmaz: yalnizca TYPED, sabit `error_code`
  (bkz. provider_errors) saklanir/dondurulur.
- Provider yoksa/yapilandirilmamissa hicbir sey FAILED yapilmaz; job QUEUED kalir
  (deterministik cikti SESSIZCE "AI" gibi sunulmaz - rapor queued/running/failed
  durumunu acikca gosterir).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_maker
from app.logging_config import get_logger
from app.models.billing import ChipReservation, ChipReservationStatus
from app.models.interaction_heatmap import InteractionHeatmap, InteractionHeatmapStatus
from app.models.page_analysis import PageAnalysis
from app.models.simulations import SimulationRun, SimulationStatus
from app.services import chip_ledger
from app.services.ai_interaction_heatmap.candidates import build_interaction_candidates
from app.services.ai_interaction_heatmap.openai_selector import (
    INTERACTION_HEATMAP_SELECTOR_CTX_KEY,
    InteractionHeatmapSelector,
)
from app.services.ai_interaction_heatmap.schemas import (
    INTERACTION_HEATMAP_DISCLAIMER,
    INTERACTION_HEATMAP_PROMPT_VERSION,
    INTERACTION_HEATMAP_SCHEMA_VERSION,
    METHOD_LABELS,
    NO_STRONG_MATCH_WARNING,
    AIInteractionOutput,
    InteractionCandidate,
    InteractionHeatmapResult,
)
from app.services.ai_interaction_heatmap.service import (
    InvalidInteractionOutputError,
    build_interaction_heatmap,
    compute_interaction_heatmap_fingerprint,
)
from app.services.ai_pipeline.provider_errors import AIProviderError
from app.services.exceptions import ChipReservationNotFoundError, InvalidChipReservationStateError
from app.services.pricing import AI_INTERACTION_HEATMAP_MODULE_KEY

logger = get_logger("interaction_heatmap_worker")

# --- Sabitler ---------------------------------------------------------------
MAX_ATTEMPTS: Final = 3
STALE_RUNNING_TIMEOUT_SECONDS: Final = 300
CLAIM_SCAN_LIMIT: Final = 10
DEFAULT_SETTLEMENT_GROUP_LIMIT: Final = 20

# per-run / per-group advisory lock "salt"lari (baseline salt=0, ai_pipeline
# init/settlement salt=1/2 ile CAKISMASIN diye farkli degerler).
_RUN_LOCK_SALT: Final = 11
_SETTLEMENT_LOCK_SALT: Final = 12

# TYPED, guvenli hata kodlari (provider_errors'takilere EK - domain hatalari).
ERROR_CODE_PAGE_ANALYSIS_UNAVAILABLE: Final = "page_analysis_unavailable"
ERROR_CODE_MISSING_TARGET_TASK: Final = "missing_target_task"
ERROR_CODE_OUTPUT_VALIDATION_FAILED: Final = "output_validation_failed"
ERROR_CODE_MAX_ATTEMPTS: Final = "max_attempts_exhausted"
ERROR_CODE_STALE_TIMEOUT: Final = "stale_timeout"

_TERMINAL_HEATMAP_STATUSES = (InteractionHeatmapStatus.SUCCEEDED, InteractionHeatmapStatus.FAILED)


def _heatmap_requested(run: SimulationRun) -> bool:
    modules = (run.input_snapshot or {}).get("modules") or []
    return AI_INTERACTION_HEATMAP_MODULE_KEY in modules


def _now() -> datetime:
    return datetime.now(UTC)


def _fixed_output_selector(
    output: AIInteractionOutput,
) -> Callable[[list[InteractionCandidate], str, str | None], AIInteractionOutput]:
    def select_output(
        _candidates: list[InteractionCandidate],
        _target_task: str,
        _confirmed_candidate_id: str | None,
    ) -> AIInteractionOutput:
        return output

    return select_output


@dataclass
class _RunEvidence:
    candidates: list[InteractionCandidate]
    confirmed_candidate_id: str | None
    content_sha256: str | None
    target_task: str | None
    target_audience: str | None
    persona_distribution: dict | None
    screenshot_data: bytes | None
    screenshot_content_type: str | None


async def _load_run_evidence(session: AsyncSession, run: SimulationRun) -> _RunEvidence | None:
    """Run'in bagli PageAnalysis'inden GERCEK etkilesim adaylarini (koordinatlar
    dahil) ve fingerprint girdilerini toplar. PageAnalysis yok/ozelliksiz ise
    `None` (page_analysis_unavailable)."""

    if run.page_analysis_id is None:
        return None
    analysis = await session.get(PageAnalysis, run.page_analysis_id)
    if analysis is None or analysis.features is None:
        return None

    snapshot = run.input_snapshot or {}
    candidates, confirmed_id = build_interaction_candidates(
        features=analysis.features,
        image_width=analysis.image_width,
        image_height=analysis.image_height,
        source_kind=analysis.source_kind,
        user_confirmed_cta=snapshot.get("user_confirmed_cta"),
    )
    persona_sample = snapshot.get("persona_sample") or {}
    persona_distribution = persona_sample.get("distribution_snapshot")
    return _RunEvidence(
        candidates=candidates,
        confirmed_candidate_id=confirmed_id,
        content_sha256=analysis.content_sha256,
        target_task=snapshot.get("target_task"),
        target_audience=snapshot.get("target_audience"),
        persona_distribution=persona_distribution if isinstance(persona_distribution, dict) else None,
        screenshot_data=analysis.screenshot_data,
        screenshot_content_type=analysis.screenshot_content_type,
    )


async def _advisory_lock_run(session: AsyncSession, run_id: uuid.UUID, *, salt: int) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(run_id), salt))))


async def _find_reusable_result(
    session: AsyncSession, organization_id: uuid.UUID, fingerprint: str, *, exclude_id: uuid.UUID
) -> dict | None:
    """Ayni organizasyonda, AYNI fingerprint'e sahip mevcut bir SUCCEEDED heatmap
    sonucu (result JSON) varsa dondurur - provider IKINCI kez cagrilmasin diye
    (gorev talimati). Yoksa `None`."""

    stmt = (
        select(InteractionHeatmap.result)
        .where(
            InteractionHeatmap.organization_id == organization_id,
            InteractionHeatmap.fingerprint == fingerprint,
            InteractionHeatmap.status == InteractionHeatmapStatus.SUCCEEDED,
            InteractionHeatmap.result.is_not(None),
            InteractionHeatmap.id != exclude_id,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


def _empty_result(*, provider: str, model_name: str, method: str, fingerprint: str) -> InteractionHeatmapResult:
    """Aday YOKKEN (sayfada degerlendirilecek etkilesim alani yok) provider
    CAGRILMADAN uretilen, dogrulanmis BOS sonuc - hotspot uydurulmaz."""

    return InteractionHeatmapResult(
        schema_version=INTERACTION_HEATMAP_SCHEMA_VERSION,
        prompt_version=INTERACTION_HEATMAP_PROMPT_VERSION,
        method=method,
        method_label=METHOD_LABELS.get(method, method),
        model_name=model_name,
        provider=provider,
        fingerprint=fingerprint,
        summary="Sayfada AI tarafindan degerlendirilebilecek net bir etkilesim alani bulunamadi.",
        hotspots=[],
        unmatched_task_warning=NO_STRONG_MATCH_WARNING,
        disclaimer=INTERACTION_HEATMAP_DISCLAIMER,
    )


# =============================================================================
# CLAIM + PROCESS
# =============================================================================


@dataclass
class _ClaimedWork:
    heatmap_id: uuid.UUID
    organization_id: uuid.UUID
    attempt: int
    fingerprint: str
    evidence: _RunEvidence


@dataclass(frozen=True)
class ProcessResult:
    claimed: bool
    outcome: str = "idle"
    heatmap_id: uuid.UUID | None = None
    error_code: str | None = None


async def _find_eligible_run_ids(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    """Baseline SUCCEEDED, heatmap secilmis (rezervasyonlu) ve heatmap job'i HENUZ
    olmayan VEYA QUEUED (denemesi tukenmemis) run'lari deterministik sirada
    dondurur (SQL on-filtre; `_heatmap_requested` Python'da yeniden dogrulanir)."""

    stmt = (
        select(SimulationRun.id, SimulationRun.input_snapshot)
        .outerjoin(InteractionHeatmap, InteractionHeatmap.simulation_run_id == SimulationRun.id)
        .where(
            SimulationRun.status == SimulationStatus.SUCCEEDED,
            SimulationRun.heatmap_chip_reservation_id.is_not(None),
            or_(
                InteractionHeatmap.id.is_(None),
                and_(
                    InteractionHeatmap.status == InteractionHeatmapStatus.QUEUED,
                    InteractionHeatmap.attempt_count < MAX_ATTEMPTS,
                ),
            ),
        )
        .order_by(SimulationRun.id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        run_id
        for run_id, snapshot in rows
        if AI_INTERACTION_HEATMAP_MODULE_KEY in ((snapshot or {}).get("modules") or [])
    ]


@dataclass
class _ClaimResult:
    kind: str  # "skip" | "resolved" | "work"
    process: ProcessResult | None = None
    work: _ClaimedWork | None = None


async def _claim_run(
    session_maker: async_sessionmaker[AsyncSession], run_id: uuid.UUID, selector: InteractionHeatmapSelector
) -> _ClaimResult:
    """Tek bir run icin heatmap job'ini QUEUED->RUNNING claim eder VE provider
    GEREKTIRMEYEN tum sonuclari (aday yok / reuse) BURADA, tek transaction'da
    cozer. Provider gerektiren durumda `_ClaimedWork` dondurur (transaction commit
    edilir, advisory lock birakilir - provider ACIK transaction disinda cagrilir)."""

    async with session_maker() as session:
        await _advisory_lock_run(session, run_id, salt=_RUN_LOCK_SALT)
        run = await session.get(SimulationRun, run_id)
        if (
            run is None
            or run.status != SimulationStatus.SUCCEEDED
            or run.heatmap_chip_reservation_id is None
            or not _heatmap_requested(run)
        ):
            await session.rollback()
            return _ClaimResult(kind="skip")

        hm = (
            await session.execute(
                select(InteractionHeatmap)
                .where(InteractionHeatmap.simulation_run_id == run_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

        if hm is None:
            hm = InteractionHeatmap(
                organization_id=run.organization_id,
                simulation_run_id=run_id,
                status=InteractionHeatmapStatus.QUEUED,
                attempt_count=0,
            )
            session.add(hm)
            try:
                await session.flush()
            except IntegrityError:
                # Es-zamanli baska bir worker satiri olusturmus (unique) - atla.
                await session.rollback()
                return _ClaimResult(kind="skip")

        if hm.status in _TERMINAL_HEATMAP_STATUSES or hm.status == InteractionHeatmapStatus.RUNNING:
            await session.rollback()
            return _ClaimResult(kind="skip")
        if hm.attempt_count >= MAX_ATTEMPTS:
            hm.status = InteractionHeatmapStatus.FAILED
            hm.error_code = ERROR_CODE_MAX_ATTEMPTS
            hm.finished_at = _now()
            await session.commit()
            return _ClaimResult(
                kind="resolved",
                process=ProcessResult(True, "failed", hm.id, ERROR_CODE_MAX_ATTEMPTS),
            )

        # --- CLAIM: QUEUED -> RUNNING
        hm.status = InteractionHeatmapStatus.RUNNING
        hm.attempt_count += 1
        hm.started_at = _now()
        hm.provider = selector.provider_name
        hm.model_name = selector.model_name
        hm.error_code = None
        await session.flush()

        evidence = await _load_run_evidence(session, run)
        if evidence is None:
            hm.status = InteractionHeatmapStatus.FAILED
            hm.error_code = ERROR_CODE_PAGE_ANALYSIS_UNAVAILABLE
            hm.finished_at = _now()
            await session.commit()
            return _ClaimResult(
                kind="resolved",
                process=ProcessResult(True, "failed", hm.id, ERROR_CODE_PAGE_ANALYSIS_UNAVAILABLE),
            )
        if not evidence.target_task:
            hm.status = InteractionHeatmapStatus.FAILED
            hm.error_code = ERROR_CODE_MISSING_TARGET_TASK
            hm.finished_at = _now()
            await session.commit()
            return _ClaimResult(
                kind="resolved",
                process=ProcessResult(True, "failed", hm.id, ERROR_CODE_MISSING_TARGET_TASK),
            )

        fingerprint = compute_interaction_heatmap_fingerprint(
            content_sha256=evidence.content_sha256,
            target_task=evidence.target_task,
            target_audience=evidence.target_audience,
            persona_distribution=evidence.persona_distribution,
            candidates=evidence.candidates,
            user_confirmed_candidate_id=evidence.confirmed_candidate_id,
            model_name=selector.model_name,
        )
        hm.fingerprint = fingerprint

        # Aday yoksa provider CAGRILMADAN dogrulanmis bos sonuc (hotspot uydurulmaz).
        if not evidence.candidates:
            result = _empty_result(
                provider=selector.provider_name,
                model_name=selector.model_name,
                method=selector.method,
                fingerprint=fingerprint,
            )
            hm.status = InteractionHeatmapStatus.SUCCEEDED
            hm.result = result.model_dump(mode="json")
            hm.finished_at = _now()
            await session.commit()
            return _ClaimResult(kind="resolved", process=ProcessResult(True, "succeeded_empty", hm.id))

        # Ayni fingerprint icin mevcut bir SUCCEEDED sonuc varsa provider'i IKINCI
        # kez cagirmadan yeniden kullan.
        reused = await _find_reusable_result(session, run.organization_id, fingerprint, exclude_id=hm.id)
        if reused is not None:
            hm.status = InteractionHeatmapStatus.SUCCEEDED
            hm.result = reused
            hm.finished_at = _now()
            await session.commit()
            return _ClaimResult(kind="resolved", process=ProcessResult(True, "reused", hm.id))

        # Provider GEREKIR: claim'i commit et (lock birakilir), sonucu ayri
        # transaction'da yaz.
        work = _ClaimedWork(
            heatmap_id=hm.id,
            organization_id=run.organization_id,
            attempt=hm.attempt_count,
            fingerprint=fingerprint,
            evidence=evidence,
        )
        await session.commit()
        return _ClaimResult(kind="work", work=work)


async def _reload_running(
    session: AsyncSession, heatmap_id: uuid.UUID, attempt: int
) -> InteractionHeatmap | None:
    """Sonuc yazmadan ONCE job'i FOR UPDATE yeniden yukler; hala RUNNING ve AYNI
    denemedeyse dondurur (aksi halde baska bir worker/reaper mudahale etmis)."""

    hm = (
        await session.execute(
            select(InteractionHeatmap)
            .where(InteractionHeatmap.id == heatmap_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if hm is None or hm.status != InteractionHeatmapStatus.RUNNING or hm.attempt_count != attempt:
        return None
    return hm


async def _persist_success(
    session_maker: async_sessionmaker[AsyncSession],
    work: _ClaimedWork,
    selector: InteractionHeatmapSelector,
    output_dump: dict,
) -> ProcessResult:
    async with session_maker() as session:
        hm = await _reload_running(session, work.heatmap_id, work.attempt)
        if hm is None:
            await session.rollback()
            return ProcessResult(True, "superseded", work.heatmap_id)
        hm.status = InteractionHeatmapStatus.SUCCEEDED
        hm.result = output_dump
        hm.fingerprint = work.fingerprint
        hm.provider = selector.provider_name
        hm.model_name = selector.model_name
        hm.error_code = None
        hm.finished_at = _now()
        await session.commit()
        return ProcessResult(True, "succeeded", work.heatmap_id)


async def _persist_failure(
    session_maker: async_sessionmaker[AsyncSession],
    work: _ClaimedWork,
    *,
    error_code: str,
    retryable: bool,
) -> ProcessResult:
    """Terminal FAILED (retryable=False veya deneme tukendi) VEYA QUEUED'a geri
    (retryable=True ve deneme kaldi) - bounded retry."""

    async with session_maker() as session:
        hm = await _reload_running(session, work.heatmap_id, work.attempt)
        if hm is None:
            await session.rollback()
            return ProcessResult(True, "superseded", work.heatmap_id)
        if retryable and hm.attempt_count < MAX_ATTEMPTS:
            hm.status = InteractionHeatmapStatus.QUEUED
            hm.error_code = error_code
            hm.started_at = None
            await session.commit()
            return ProcessResult(True, "requeued", work.heatmap_id, error_code)
        hm.status = InteractionHeatmapStatus.FAILED
        hm.error_code = error_code
        hm.finished_at = _now()
        await session.commit()
        return ProcessResult(True, "failed", work.heatmap_id, error_code)


async def process_one_interaction_heatmap(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    selector: InteractionHeatmapSelector,
) -> ProcessResult:
    """En fazla BIR heatmap job'ini uctan uca isler (claim -> provider -> persist).

    Provider cagrisi ACIK transaction/lock TUTMADAN yapilir. Beklenmeyen (typed
    OLMAYAN) hata YENIDEN FIRLATILIR - job RUNNING kalir, stale reaper kurtarir.
    """

    async with session_maker() as session:
        run_ids = await _find_eligible_run_ids(session, limit=CLAIM_SCAN_LIMIT)

    for run_id in run_ids:
        claim = await _claim_run(session_maker, run_id, selector)
        if claim.kind == "skip":
            continue
        if claim.kind == "resolved":
            assert claim.process is not None
            return claim.process

        assert claim.work is not None
        work = claim.work
        try:
            sel_result = await selector.select(
                candidates=work.evidence.candidates,
                target_task=work.evidence.target_task or "",
                target_audience=work.evidence.target_audience,
                persona_distribution=work.evidence.persona_distribution,
                confirmed_candidate_id=work.evidence.confirmed_candidate_id,
                screenshot_data=work.evidence.screenshot_data,
                screenshot_content_type=work.evidence.screenshot_content_type,
            )
        except AIProviderError as exc:
            return await _persist_failure(
                session_maker, work, error_code=exc.error_code, retryable=exc.retryable
            )
        except Exception:
            logger.exception(
                "interaction heatmap provider beklenmeyen hata (job RUNNING kalir, reaper kurtarir)",
                extra={"heatmap_id": str(work.heatmap_id)},
            )
            raise

        # Ciktiyi dogrula + koordinatlari GERCEK adaylardan cöz (AI koordinat
        # uretemez). Dogrulama ihlali (bilinmeyen/tekrar candidate_id) KALICI'dir.
        try:
            result = build_interaction_heatmap(
                candidates=work.evidence.candidates,
                target_task=work.evidence.target_task or "",
                target_audience=work.evidence.target_audience,
                persona_distribution=work.evidence.persona_distribution,
                content_sha256=work.evidence.content_sha256,
                confirmed_candidate_id=work.evidence.confirmed_candidate_id,
                model_name=sel_result.model_name,
                provider_name=sel_result.provider_name,
                method=sel_result.method,
                selector=_fixed_output_selector(sel_result.output),
            )
        except InvalidInteractionOutputError:
            return await _persist_failure(
                session_maker, work, error_code=ERROR_CODE_OUTPUT_VALIDATION_FAILED, retryable=False
            )

        return await _persist_success(session_maker, work, selector, result.model_dump(mode="json"))

    return ProcessResult(claimed=False)


# =============================================================================
# STALE RECOVERY (reaper) - provider GEREKTIRMEZ
# =============================================================================


@dataclass(frozen=True)
class StaleRecoveryResult:
    scanned: int = 0
    requeued: int = 0
    failed: int = 0


async def reap_stale_interaction_heatmaps(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    timeout_seconds: int = STALE_RUNNING_TIMEOUT_SECONDS,
) -> StaleRecoveryResult:
    """RUNNING'de takili kalmis (worker cokmesi vb.) job'lari kurtarir: denemesi
    kalanlar QUEUED'a doner, tukenmiş olanlar FAILED olur (stale_timeout)."""

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    async with session_maker() as session:
        stale = (
            await session.execute(
                select(InteractionHeatmap)
                .where(
                    InteractionHeatmap.status == InteractionHeatmapStatus.RUNNING,
                    InteractionHeatmap.started_at.is_not(None),
                    InteractionHeatmap.started_at < cutoff,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()

        requeued = 0
        failed = 0
        for hm in stale:
            if hm.attempt_count < MAX_ATTEMPTS:
                hm.status = InteractionHeatmapStatus.QUEUED
                hm.error_code = ERROR_CODE_STALE_TIMEOUT
                hm.started_at = None
                requeued += 1
            else:
                hm.status = InteractionHeatmapStatus.FAILED
                hm.error_code = ERROR_CODE_STALE_TIMEOUT
                hm.finished_at = _now()
                failed += 1
        await session.commit()
        return StaleRecoveryResult(scanned=len(stale), requeued=requeued, failed=failed)


# =============================================================================
# CHIP SETTLEMENT (per launch group) - provider GEREKTIRMEZ
# =============================================================================


@dataclass(frozen=True)
class SettlementResult:
    groups_scanned: int = 0
    groups_consumed: int = 0
    groups_released: int = 0
    groups_waiting: int = 0
    groups_skipped_integrity: int = 0


async def _find_settlement_groups(session: AsyncSession, *, limit: int) -> list[uuid.UUID]:
    stmt = (
        select(SimulationRun.launch_run_id)
        .join(ChipReservation, ChipReservation.id == SimulationRun.heatmap_chip_reservation_id)
        .where(
            SimulationRun.heatmap_chip_reservation_id.is_not(None),
            SimulationRun.launch_run_id.is_not(None),
            ChipReservation.status == ChipReservationStatus.RESERVED,
        )
        .distinct()
        .order_by(SimulationRun.launch_run_id)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [r for r in rows if r is not None]


async def _settle_one_group(
    session_maker: async_sessionmaker[AsyncSession], launch_run_id: uuid.UUID
) -> str:
    async with session_maker() as session:
        await _advisory_lock_run(session, launch_run_id, salt=_SETTLEMENT_LOCK_SALT)
        runs = list(
            (
                await session.execute(
                    select(SimulationRun).where(SimulationRun.launch_run_id == launch_run_id)
                )
            ).scalars().all()
        )
        expected = [r for r in runs if r.heatmap_chip_reservation_id is not None]
        if not expected:
            await session.rollback()
            return "waiting"

        organization_ids = {r.organization_id for r in expected}
        reservation_ids = {r.heatmap_chip_reservation_id for r in expected}
        if len(organization_ids) != 1 or len(reservation_ids) != 1:
            await session.rollback()
            return "skipped_integrity"
        organization_id = next(iter(organization_ids))
        reservation_id = next(iter(reservation_ids))
        assert reservation_id is not None

        reservation = await session.get(ChipReservation, reservation_id)
        if reservation is None or reservation.organization_id != organization_id:
            await session.rollback()
            return "skipped_integrity"
        if reservation.status != ChipReservationStatus.RESERVED:
            await session.rollback()
            return "waiting"

        # Baseline basarisizligi normalde `_resolve_launch_group` tarafindan zaten
        # release edilir; savunma amacli burada da ele alinir.
        if any(r.status in (SimulationStatus.FAILED, SimulationStatus.CANCELLED) for r in expected):
            return await _finish_settlement(
                session, organization_id, reservation_id, consume=False,
                reason="AI etkilesim isi haritasi: en az bir varyant baseline'da basarisiz",
            )
        if not all(r.status == SimulationStatus.SUCCEEDED for r in expected):
            await session.rollback()
            return "waiting"

        expected_ids = [r.id for r in expected]
        heatmaps = list(
            (
                await session.execute(
                    select(InteractionHeatmap).where(
                        InteractionHeatmap.simulation_run_id.in_(expected_ids)
                    )
                )
            ).scalars().all()
        )
        if len(heatmaps) < len(expected_ids):
            await session.rollback()
            return "waiting"

        statuses = [h.status for h in heatmaps]
        if any(s == InteractionHeatmapStatus.FAILED for s in statuses):
            return await _finish_settlement(
                session, organization_id, reservation_id, consume=False,
                reason="AI etkilesim isi haritasi: en az bir varyant basarisiz",
            )
        if all(s == InteractionHeatmapStatus.SUCCEEDED for s in statuses):
            return await _finish_settlement(
                session, organization_id, reservation_id, consume=True,
                reason="AI etkilesim isi haritasi: tum varyantlar basarili",
            )
        await session.rollback()
        return "waiting"


async def _finish_settlement(
    session: AsyncSession,
    organization_id: uuid.UUID,
    reservation_id: uuid.UUID,
    *,
    consume: bool,
    reason: str,
) -> str:
    try:
        if consume:
            await chip_ledger.consume_reservation(session, organization_id, reservation_id, reason)
        else:
            await chip_ledger.release_reservation(session, organization_id, reservation_id, reason)
        await session.commit()
        return "consumed" if consume else "released"
    except (InvalidChipReservationStateError, ChipReservationNotFoundError):
        await session.rollback()
        return "waiting"


async def settle_interaction_heatmap_reservations(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    *,
    limit: int = DEFAULT_SETTLEMENT_GROUP_LIMIT,
) -> SettlementResult:
    async with session_maker() as session:
        group_ids = await _find_settlement_groups(session, limit=limit)

    consumed = released = waiting = skipped = 0
    for group_id in group_ids:
        outcome = await _settle_one_group(session_maker, group_id)
        if outcome == "consumed":
            consumed += 1
        elif outcome == "released":
            released += 1
        elif outcome == "skipped_integrity":
            skipped += 1
        else:
            waiting += 1
    return SettlementResult(
        groups_scanned=len(group_ids),
        groups_consumed=consumed,
        groups_released=released,
        groups_waiting=waiting,
        groups_skipped_integrity=skipped,
    )


# =============================================================================
# arq CYCLE WRAPPER'LARI (ince zamanlama katmani)
# =============================================================================


class InvalidCtxSelectorError(Exception):
    """ctx'deki secici degeri mevcut ama sozlesmeye uymuyorsa firlatilir."""


def resolve_ctx_selector(ctx: dict[str, object]) -> InteractionHeatmapSelector | None:
    selector = ctx.get(INTERACTION_HEATMAP_SELECTOR_CTX_KEY)
    if selector is None:
        return None
    if not isinstance(selector, InteractionHeatmapSelector):
        raise InvalidCtxSelectorError("ctx heatmap secici sozlesmesine uymuyor")
    return selector


async def run_interaction_heatmap_queue_cycle(
    ctx: dict[str, object],
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
) -> dict[str, object]:
    """En fazla BIR heatmap job'ini isler (arq processor cycle). Secici ctx'e
    ACIKCA enjekte edilmelidir; yoksa/yanlissa hicbir sey claim EDILMEZ, hicbir
    sey FAILED yapilmaz (job'lar QUEUED kalir)."""

    try:
        selector = resolve_ctx_selector(ctx)
    except InvalidCtxSelectorError:
        return {"task": "interaction_heatmap_processor", "status": "disabled", "reason": "invalid_selector"}
    if selector is None:
        return {"task": "interaction_heatmap_processor", "status": "disabled", "reason": "no_selector"}

    started = time.perf_counter()
    result = await process_one_interaction_heatmap(session_maker, selector=selector)
    logger.info(
        "interaction heatmap job islendi",
        extra={
            "task": "interaction_heatmap_processor",
            "claimed": result.claimed,
            "outcome": result.outcome,
            "heatmap_id": str(result.heatmap_id) if result.heatmap_id else None,
            "error_code": result.error_code,
            "duration_ms": (time.perf_counter() - started) * 1000,
        },
    )
    return {
        "task": "interaction_heatmap_processor",
        "status": "processed",
        "claimed": result.claimed,
        "outcome": result.outcome,
    }


async def run_interaction_heatmap_reap_cycle(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
) -> dict[str, object]:
    result = await reap_stale_interaction_heatmaps(session_maker)
    summary = {
        "task": "interaction_heatmap_reaper",
        "scanned": result.scanned,
        "requeued": result.requeued,
        "failed": result.failed,
    }
    logger.info("interaction heatmap stale reaper calisti", extra=summary)
    return summary


async def run_interaction_heatmap_settlement_cycle(
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
) -> dict[str, object]:
    result = await settle_interaction_heatmap_reservations(session_maker)
    summary = {
        "task": "interaction_heatmap_settlement",
        "groups_scanned": result.groups_scanned,
        "groups_consumed": result.groups_consumed,
        "groups_released": result.groups_released,
        "groups_waiting": result.groups_waiting,
        "groups_skipped_integrity": result.groups_skipped_integrity,
    }
    logger.info("interaction heatmap settlement cycle tamamlandi", extra=summary)
    return summary


__all__ = [
    "MAX_ATTEMPTS",
    "STALE_RUNNING_TIMEOUT_SECONDS",
    "ProcessResult",
    "StaleRecoveryResult",
    "SettlementResult",
    "process_one_interaction_heatmap",
    "reap_stale_interaction_heatmaps",
    "settle_interaction_heatmap_reservations",
    "resolve_ctx_selector",
    "run_interaction_heatmap_queue_cycle",
    "run_interaction_heatmap_reap_cycle",
    "run_interaction_heatmap_settlement_cycle",
    "InvalidCtxSelectorError",
]
