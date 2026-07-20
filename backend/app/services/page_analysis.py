"""URL analiz is akisi: queued -> running -> succeeded/failed.

`app.services.simulation_worker` ile ayni durum makinesi desenini izler:
`claim_next_queued` bekleyen isleri `SELECT ... FOR UPDATE SKIP LOCKED` ile
kilitleyip 'running'e gecirir (birden fazla worker sureci ayni isi iki kez
almaz); `process_analysis` analyzer servisini (ayri container, Playwright
tabanli SSRF-guvenli navigasyon - bkz. docs/security.md) HTTP uzerinden
cagirir ve sonucu kalici olarak yazar; `reap_stale_running` worker cokmesi
nedeniyle 'running'de takili kalan isleri kurtarir.

Idempotency: analyzer cagrisi salt-okunur ve pasiftir (hedef site uzerinde
hicbir yan etki uretmez - form gonderme/giris/tiklama yoktur); dolayisiyla
bir isin tekrar islenmesi (ör. worker cokmesi sonrasi reap tarafindan
yeniden kuyruga alinmasi) her zaman guvenlidir ve ayrica bir dedupe
anahtarina ihtiyac duymaz.

Redaksiyon: bu modulun yazdigi `features` alani, analyzer'in ureteği
turetilmis/redakte edilmis alanlardir (bkz. analyzer/app/schemas.py);
ham HTML, form degerleri, cookie veya token higbir zaman bu modulden
gecmez (analyzer bunlari zaten uretmez/dondurmez).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_maker
from app.models.page_analysis import PageAnalysis, PageAnalysisStatus
from app.services import url_safety
from app.services.exceptions import PageAnalysisNotFoundError, UnauthorizedPageAnalysisError

logger = logging.getLogger("synthetix.page_analysis")

CLAIM_BATCH_SIZE = 5


def _now() -> datetime:
    return datetime.now(UTC)


async def create_analysis(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    url: str,
    authorization_confirmed: bool,
) -> PageAnalysis:
    """Yeni bir analiz isi olusturur ve kuyruga (queued) ekler.

    Kullanici, URL'yi analiz etme yetkisini acikca onaylamadan
    (`authorization_confirmed=True`) hicbir is olusturulmaz. URL sozdizimi
    ve DNS/IP dogrulamasi burada da yapilir (hizli, kullanici dostu 400
    hatasi icin); otoriter/son dogrulama analyzer servisinde tekrarlanir
    (bkz. modul dokstring'i ve docs/security.md).
    """

    if not authorization_confirmed:
        raise UnauthorizedPageAnalysisError("Bu URL'yi analiz etme yetkisini onaylamadan istek islenemez")

    # DNS cozumleme engelleyici (blocking) oldugu icin event loop'u
    # kilitlememesi adina bir thread'de calistirilir.
    validated = await asyncio.to_thread(url_safety.validate_public_url, url)

    analysis = PageAnalysis(
        organization_id=organization_id,
        requested_by_user_id=requested_by_user_id,
        url=validated.url,
        authorization_confirmed=True,
        status=PageAnalysisStatus.QUEUED,
    )
    session.add(analysis)
    await session.flush()
    return analysis


async def get_owned_analysis(
    session: AsyncSession, organization_id: uuid.UUID, analysis_id: uuid.UUID
) -> PageAnalysis:
    result = await session.execute(select(PageAnalysis).where(PageAnalysis.id == analysis_id))
    analysis = result.scalar_one_or_none()
    if analysis is None or analysis.organization_id != organization_id:
        raise PageAnalysisNotFoundError(f"Analiz bulunamadi: {analysis_id}")
    return analysis


async def claim_next_queued(session: AsyncSession, limit: int = CLAIM_BATCH_SIZE) -> list[PageAnalysis]:
    """Bekleyen (queued) analiz islerini kilitleyip 'running' durumuna gecirir."""

    result = await session.execute(
        select(PageAnalysis)
        .where(PageAnalysis.status == PageAnalysisStatus.QUEUED)
        .order_by(PageAnalysis.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    analyses = list(result.scalars().all())
    for analysis in analyses:
        analysis.status = PageAnalysisStatus.RUNNING
        analysis.started_at = _now()
        analysis.attempt_count += 1
    await session.flush()
    return analyses


def _extract_features(snapshot: dict) -> dict:
    """Analyzer yanitindan, ekran goruntusu HARIC tum turetilmis alanlari cikarir.

    Ekran goruntusu ayri bir sutunda (`screenshot_data`, sureli) saklanir;
    bu fonksiyonun dondurdugu `features` JSON'i yalnizca metin/yapisal/
    performans/erisilebilirlik verisidir.
    """

    return {
        "final_url": snapshot["final_url"],
        "redirect_count": snapshot["redirect_count"],
        "title": snapshot["title"],
        "headings": snapshot["headings"],
        "text_stats": snapshot["text_stats"],
        "controls": snapshot["controls"],
        "element_boxes": snapshot["element_boxes"],
        "layout_regions": snapshot.get("layout_regions") or {},
        "performance": snapshot["performance"],
        "contrast_candidates": snapshot["contrast_candidates"],
        "accessibility_precheck": snapshot["accessibility_precheck"],
        "warnings": snapshot.get("warnings", []),
    }


async def _call_analyzer(client: httpx.AsyncClient, url: str) -> dict:
    response = await client.post(
        f"{settings.analyzer_base_url}/internal/analyze",
        json={"url": url, "authorization_confirmed": True},
        headers={"X-Analyzer-Token": settings.analyzer_shared_token},
        timeout=settings.analyzer_request_timeout_seconds,
    )
    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"analyzer hatasi ({response.status_code}): {detail}")
    return response.json()


async def process_analysis(
    session: AsyncSession, analysis: PageAnalysis, *, client: httpx.AsyncClient | None = None
) -> None:
    """Tek bir 'running' analiz isini sonuna kadar isler (basarili/basarisiz)."""

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient()

    try:
        snapshot = await _call_analyzer(client, analysis.url)
    except Exception as exc:  # analyzer HTTP hatasi, timeout, baglanti hatasi, SSRF reddi
        analysis.status = PageAnalysisStatus.FAILED
        analysis.error = str(exc)
        analysis.finished_at = _now()
        await session.flush()
        logger.warning("analiz basarisiz (id=%s): %s", analysis.id, exc)
        return
    finally:
        if owns_client:
            await client.aclose()

    screenshot_b64 = snapshot["screenshot"]["base64_data"]
    analysis.screenshot_data = base64.b64decode(screenshot_b64)
    analysis.screenshot_expires_at = _now() + timedelta(
        seconds=settings.page_analysis_screenshot_retention_seconds
    )
    analysis.snapshot_version = snapshot["snapshot_version"]
    analysis.analyzer_version = snapshot["analyzer_version"]
    analysis.source = snapshot["source"]
    analysis.features = _extract_features(snapshot)
    analysis.status = PageAnalysisStatus.SUCCEEDED
    analysis.finished_at = _now()
    await session.flush()


async def reap_stale_running(
    session: AsyncSession,
    *,
    timeout_seconds: int | None = None,
    max_attempts: int | None = None,
) -> int:
    """'running'de takili kalmis (worker cokmesi vb.) isleri kurtarir."""

    timeout_seconds = timeout_seconds or settings.page_analysis_stale_timeout_seconds
    max_attempts = max_attempts or settings.page_analysis_max_attempts

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    result = await session.execute(
        select(PageAnalysis)
        .where(PageAnalysis.status == PageAnalysisStatus.RUNNING, PageAnalysis.updated_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    stale = list(result.scalars().all())

    for analysis in stale:
        if analysis.attempt_count < max_attempts:
            analysis.status = PageAnalysisStatus.QUEUED
            analysis.started_at = None
        else:
            analysis.status = PageAnalysisStatus.FAILED
            analysis.error = "Zaman asimi: maksimum deneme sayisina ulasildi"
            analysis.finished_at = _now()
        await session.flush()

    return len(stale)


async def purge_expired_screenshots(session: AsyncSession) -> int:
    """Saklama suresi dolmus ekran goruntusu verilerini siler; metadata satiri kalir."""

    result = await session.execute(
        select(PageAnalysis).where(
            PageAnalysis.screenshot_data.is_not(None),
            PageAnalysis.screenshot_expires_at.is_not(None),
            PageAnalysis.screenshot_expires_at < _now(),
        )
    )
    expired = list(result.scalars().all())
    for analysis in expired:
        analysis.screenshot_data = None
        analysis.screenshot_expires_at = None
    await session.flush()
    return len(expired)


async def _process_claimed_analysis(analysis_id: uuid.UUID) -> None:
    """Tek bir kilitli analiz isini kendi oturumunda isler; hata halinde 'failed' isaretler."""

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(PageAnalysis).where(PageAnalysis.id == analysis_id).with_for_update()
            )
            analysis = result.scalar_one_or_none()
            if analysis is None or analysis.status != PageAnalysisStatus.RUNNING:
                return
            await process_analysis(session, analysis)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("process_analysis basarisiz oldu (id=%s)", analysis_id)

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(PageAnalysis).where(PageAnalysis.id == analysis_id).with_for_update()
            )
            analysis = result.scalar_one_or_none()
            if analysis is not None and analysis.status == PageAnalysisStatus.RUNNING:
                analysis.status = PageAnalysisStatus.FAILED
                analysis.error = "Beklenmeyen analiz hatasi"
                analysis.finished_at = _now()
                await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("analiz %s icin basarisizlik kaydi da basarisiz oldu", analysis_id)


async def run_queue_cycle() -> None:
    """arq cron girdisi: bekleyen analiz islerini alir ve isler. Kendi oturumunu acar."""

    async with async_session_maker() as session:
        try:
            analyses = await claim_next_queued(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("claim_next_queued basarisiz oldu")
            return

    for analysis in analyses:
        await _process_claimed_analysis(analysis.id)


async def run_reap_cycle() -> None:
    """arq cron girdisi: 'running'de takili kalmis analiz islerini kurtarir."""

    async with async_session_maker() as session:
        try:
            await reap_stale_running(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("reap_stale_running basarisiz oldu")


async def run_purge_cycle() -> None:
    """arq cron girdisi: saklama suresi dolmus ekran goruntusu verilerini siler."""

    async with async_session_maker() as session:
        try:
            await purge_expired_screenshots(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("purge_expired_screenshots basarisiz oldu")


__all__ = [
    "CLAIM_BATCH_SIZE",
    "create_analysis",
    "get_owned_analysis",
    "claim_next_queued",
    "process_analysis",
    "reap_stale_running",
    "purge_expired_screenshots",
    "run_queue_cycle",
    "run_reap_cycle",
    "run_purge_cycle",
]
