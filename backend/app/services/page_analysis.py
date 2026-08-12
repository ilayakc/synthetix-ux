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
import binascii
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_maker
from app.logging_config import get_logger
from app.logging_utils import log_duration
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.models.reports import Report
from app.models.simulations import SimulationRun
from app.services import design_assets as design_assets_service
from app.services import image_safety, image_visual_analysis, url_safety
from app.services.exceptions import (
    DesignAssetNotFoundError,
    DesignAssetUnavailableError,
    ImageTooLargeError,
    InvalidImageError,
    PageAnalysisNotFoundError,
    PageAnalysisSourceConflictError,
    UnauthorizedPageAnalysisError,
)

logger = logging.getLogger("synthetix.page_analysis")
_analyzer_logger = get_logger("external")

CLAIM_BATCH_SIZE = 5

# Analyzer'in sozlesmesi (bkz. analyzer/app/schemas.py) yalnizca PNG
# uretir - baska hicbir format burada guvenilir kabul edilmez.
_URL_SCREENSHOT_ALLOWED_FORMATS: dict[str, str] = {"PNG": "image/png"}

# Guvenilir saklanan DesignAsset.content_type (MIME) -> PageAnalysis worker'inin
# yeniden dogrulama icin kabul ettigi tek format (bkz. _verify_design_asset_snapshot).
_DESIGN_ASSET_ALLOWED_FORMATS = image_safety.STANDARD_IMAGE_FORMATS

# Genel, guvenli hata mesajlari - ic dogrulama ayrinti/format/boyut bilgisi
# (ki bunlar zaten sentetik/ham-veri-icermeyen metinlerdir) API yanitina asla
# dogrudan yansitilmaz; yalnizca WARNING seviyeli log'da tutulur.
_SCREENSHOT_VALIDATION_FAILED_MESSAGE = "Ekran goruntusu guvenlik dogrulamasindan gecemedi"
EMPTY_PAGE_SNAPSHOT_CODE = "empty_page_snapshot"
ANALYZER_UNAVAILABLE_CODE = "analyzer_unavailable"
ANALYZER_REJECTED_CODE = "analyzer_rejected"
_ALLOWED_ANALYZER_ERROR_CODES = {EMPTY_PAGE_SNAPSHOT_CODE}
EMPTY_PAGE_SNAPSHOT_MESSAGE = (
    "Sayfa acildi ancak analiz edilebilir metin veya etkilesim alani yuklenmedi. "
    "Site otomatik tarayicilara bos icerik donduruyor olabilir."
)


class AnalyzerResponseError(RuntimeError):
    """Analyzer HTTP yanitini retry kararinda durum koduyla birlikte tasir."""

    def __init__(self, status_code: int, detail: str, *, code: str | None = None):
        super().__init__(f"analyzer hatasi ({status_code}): {detail}")
        self.status_code = status_code
        self.detail = detail
        self.code = code or ANALYZER_REJECTED_CODE


def _now() -> datetime:
    return datetime.now(UTC)


async def create_analysis(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
    url: str | None = None,
    design_asset_id: uuid.UUID | None = None,
    authorization_confirmed: bool = False,
) -> PageAnalysis:
    """Yeni bir analiz isi olusturur ve kuyruga (queued) ekler.

    Ortak kaynak sozlesmesi: `url` ve `design_asset_id`'den TAM OLARAK biri
    verilmelidir (bkz. app.routers.page_analysis - istemci sozlesmesi bu
    kuralı 422 ile zaten zorunlu kilar; burasi servis katmaninda ikinci,
    bagimsiz bir savunma katmanidir).

    URL kaynagi: kullanici, URL'yi analiz etme yetkisini acikca onaylamadan
    (`authorization_confirmed=True`) hicbir is olusturulmaz. URL sozdizimi
    ve DNS/IP dogrulamasi burada da yapilir (hizli, kullanici dostu 400
    hatasi icin); otoriter/son dogrulama analyzer servisinde tekrarlanir
    (bkz. modul dokstring'i ve docs/security.md).

    DesignAsset kaynagi: `authorization_confirmed` kavrami gecerli degildir
    (kullanici zaten kendi yukledigi kendi gorselini analiz ediyor). Asset'in
    ayni organizasyona ait, aktif, silinmemis, suresi dolmamis ve ikili
    verisinin mevcut oldugu burada dogrulanir (`ensure_asset_usable`) - ama
    bu yalnizca create-time bir kontroldur; worker, isleme zamaninda AYNI
    kontrolleri BAGIMSIZ olarak tekrar yapar (bkz. process_analysis).
    """

    has_url = url is not None
    has_asset = design_asset_id is not None
    if has_url == has_asset:
        raise PageAnalysisSourceConflictError(
            "Tam olarak bir kaynak belirtilmelidir: 'url' veya 'design_asset_id'"
        )

    if has_asset:
        assert design_asset_id is not None
        asset = await design_assets_service.get_owned_asset(session, organization_id, design_asset_id)
        design_assets_service.ensure_asset_usable(asset)

        analysis = PageAnalysis(
            organization_id=organization_id,
            requested_by_user_id=requested_by_user_id,
            source_kind=PageAnalysisSourceKind.DESIGN_ASSET,
            design_asset_id=asset.id,
            url=None,
            # DesignAsset kaynaginda yetki onayi kavrami gecerli degil (kullanici
            # zaten kendi yukledigi kendi gorselini analiz ediyor) - `True` sabit.
            authorization_confirmed=True,
            status=PageAnalysisStatus.QUEUED,
        )
        session.add(analysis)
        await session.flush()
        return analysis

    if not authorization_confirmed:
        raise UnauthorizedPageAnalysisError("Bu URL'yi analiz etme yetkisini onaylamadan istek islenemez")

    assert url is not None

    # DNS cozumleme engelleyici (blocking) oldugu icin event loop'u
    # kilitlememesi adina bir thread'de calistirilir.
    validated = await asyncio.to_thread(url_safety.validate_public_url, url)

    analysis = PageAnalysis(
        organization_id=organization_id,
        requested_by_user_id=requested_by_user_id,
        source_kind=PageAnalysisSourceKind.URL,
        url=validated.url,
        design_asset_id=None,
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
        analysis.error = None
        analysis.error_code = None
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
    with log_duration(
        _analyzer_logger,
        "external",
        "analyzer.analyze",
        slow_threshold_ms=settings.slow_request_threshold_ms,
    ):
        response = await client.post(
            f"{settings.analyzer_base_url}/internal/analyze",
            json={"url": url, "authorization_confirmed": True},
            headers={"X-Analyzer-Token": settings.analyzer_shared_token},
            timeout=settings.analyzer_request_timeout_seconds,
        )
        if response.status_code != 200:
            try:
                detail_payload = response.json().get("detail", response.text)
            except ValueError:
                detail_payload = response.text
            if isinstance(detail_payload, dict):
                raw_code = detail_payload.get("code")
                code = (
                    raw_code
                    if isinstance(raw_code, str) and raw_code in _ALLOWED_ANALYZER_ERROR_CODES
                    else ANALYZER_REJECTED_CODE
                )
                message = detail_payload.get("message")
                detail = message if isinstance(message, str) else "Analyzer istegi tamamlanamadi"
            else:
                code = None
                detail = str(detail_payload)
            raise AnalyzerResponseError(response.status_code, detail, code=code)
        return response.json()


def _is_retryable_analyzer_failure(exc: Exception) -> bool:
    """Yalnizca gecici ag/servis hatalarini yeniden dener.

    SSRF/4xx ve boyut limiti gibi ayni girdide degismeyecek reddetmeler
    terminaldir. Timeout, baglanti kopmasi, 408/429 ve 5xx ise Render cold
    start dahil gecici altyapi durumlarinda iyilesebilir.
    """

    if isinstance(exc, httpx.RequestError):
        return True
    if not isinstance(exc, AnalyzerResponseError):
        return False
    if "yanit boyutu sinirini asti" in exc.detail.lower():
        return False
    return exc.status_code in (408, 429) or exc.status_code >= 500


async def _record_analyzer_failure(
    session: AsyncSession,
    analysis: PageAnalysis,
    exc: Exception,
) -> None:
    retryable = _is_retryable_analyzer_failure(exc)
    attempts_remaining = analysis.attempt_count < settings.page_analysis_max_attempts
    if isinstance(exc, AnalyzerResponseError):
        analysis.error_code = exc.code[:100]
        # Yeni typed analyzer sozlesmesinde kullaniciya yalnizca bizim sabit,
        # guvenli mesajimiz acilir. Eski analyzer surumlerinin duz metin
        # yanitlari geriye uyumluluk icin mevcut bicimini korur.
        analysis.error = (
            EMPTY_PAGE_SNAPSHOT_MESSAGE
            if exc.code == EMPTY_PAGE_SNAPSHOT_CODE
            else str(exc)
        )
    else:
        analysis.error_code = ANALYZER_UNAVAILABLE_CODE
        analysis.error = str(exc)
    if retryable and attempts_remaining:
        analysis.status = PageAnalysisStatus.QUEUED
        analysis.started_at = None
        analysis.finished_at = None
        logger.warning(
            "analiz gecici hatadan sonra yeniden kuyruga alindi (id=%s, deneme=%s/%s): %s",
            analysis.id,
            analysis.attempt_count,
            settings.page_analysis_max_attempts,
            exc,
        )
    else:
        analysis.status = PageAnalysisStatus.FAILED
        analysis.finished_at = _now()
        logger.warning("analiz basarisiz (id=%s): %s", analysis.id, exc)
    await session.flush()


def _verify_design_asset_snapshot(image_bytes: bytes, content_type_mime: str) -> tuple[int, int]:
    """Saklanan DesignAsset binary'sini YENIDEN, ortak `image_safety` hattindan
    decode ederek dogrular: format/kare-sayisi/boyut/piksel + saklanan
    `content_type` ile tutarlilik. Zaten upload sirasinda ayni hattan gecmis
    olsa da, bu ikinci, bagimsiz bir kontroldur - analyzer'in metadata'sina
    veya saklanan width/height kolonuna KOR guvenilmez."""

    try:
        image, decoded_content_type = image_safety.decode_and_validate_image(
            image_bytes,
            allowed_formats=_DESIGN_ASSET_ALLOWED_FORMATS,
            max_dimension=settings.design_asset_max_dimension,
            max_pixels=settings.design_asset_max_pixels,
        )
    except (InvalidImageError, ImageTooLargeError) as exc:
        raise DesignAssetUnavailableError("Tasarim gorseli guvenlik dogrulamasindan gecemedi") from exc

    with image:
        width, height = image.size

    if decoded_content_type != content_type_mime:
        raise DesignAssetUnavailableError("Tasarim gorseli format tutarsizligi tespit edildi")

    return width, height


async def _process_design_asset_source(session: AsyncSession, analysis: PageAnalysis) -> None:
    """DesignAsset kaynakli bir analiz isini isler: analyzer'a HICBIR ISTEK
    YAPILMAZ, DOM alanlari uretilmez - guvenli dogrulama hattindan gecmis
    binary, degismez bir snapshot olarak kopyalanir VE yerel/deterministik
    OpenCV gorsel analizi (bkz. app.services.image_visual_analysis) ile
    `features` doldurulur (CTA adaylari + sentetik dikkat tahmini,
    `feature_source="visual_heuristic"`).

    Create ile bu fonksiyonun calistigi an arasinda asset silinmis/expire
    olmus olabilir; bu yuzden sahiplik + kullanilabilirlik burada BAGIMSIZ
    olarak yeniden dogrulanir (create-time kontrolune guvenilmez). Herhangi
    bir adim (asset dogrulama VEYA gorsel analiz) basarisiz olursa
    `screenshot_data`/`features` veya kismi ikili veri ASLA yazilmaz - is
    guvenli ve genel bir mesajla 'failed' olur, ic hata ayrintisi
    API'ye/loglara sizmaz (yalnizca WARNING seviyeli log'da tutulur); bu
    yolda analyzer HTTP servisi hicbir zaman cagrilmaz.
    """

    assert analysis.design_asset_id is not None

    try:
        asset = await design_assets_service.get_owned_asset(
            session, analysis.organization_id, analysis.design_asset_id
        )
        design_assets_service.ensure_asset_usable(asset)
        assert asset.image_data is not None
        image_bytes = asset.image_data
        content_type = asset.content_type
        width, height = _verify_design_asset_snapshot(image_bytes, content_type.value)
    except (DesignAssetNotFoundError, DesignAssetUnavailableError) as exc:
        analysis.status = PageAnalysisStatus.FAILED
        analysis.error = "Tasarim gorseli artik kullanilabilir durumda degil"
        analysis.finished_at = _now()
        await session.flush()
        logger.warning("design_asset kaynakli analiz basarisiz (id=%s): %s", analysis.id, exc)
        return

    try:
        features = image_visual_analysis.analyze_screenshot(image_bytes)
    except image_visual_analysis.VisualAnalysisError as exc:
        analysis.status = PageAnalysisStatus.FAILED
        analysis.error = "Gorsel analiz basarisiz oldu"
        analysis.finished_at = _now()
        await session.flush()
        logger.warning("design_asset kaynakli gorsel analiz basarisiz (id=%s): %s", analysis.id, exc)
        return

    analysis.screenshot_data = image_bytes
    analysis.screenshot_expires_at = _now() + timedelta(
        seconds=settings.page_analysis_screenshot_retention_seconds
    )
    analysis.screenshot_content_type = content_type.value
    analysis.image_width = width
    analysis.image_height = height
    analysis.content_sha256 = hashlib.sha256(image_bytes).hexdigest()
    analysis.snapshot_version = "design-asset-snapshot-1"
    analysis.analyzer_version = None
    analysis.source = "design_asset"
    # DOM olmadigi icin element_boxes/layout_regions/contrast_candidates
    # uretilmez (bunlar yalnizca gercek DOM/analyzer verisiyle doldurulur) -
    # ama yerel OpenCV analizinden gelen visual_cta_candidates/synthetic_
    # attention_estimate DOLU yazilir (feature_source="visual_heuristic").
    analysis.features = features
    analysis.error = None
    analysis.error_code = None
    analysis.status = PageAnalysisStatus.SUCCEEDED
    analysis.finished_at = _now()
    await session.flush()


async def process_analysis(
    session: AsyncSession, analysis: PageAnalysis, *, client: httpx.AsyncClient | None = None
) -> None:
    """Tek bir 'running' analiz isini sonuna kadar isler (basarili/basarisiz)."""

    if analysis.source_kind == PageAnalysisSourceKind.DESIGN_ASSET:
        await _process_design_asset_source(session, analysis)
        return

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient()

    assert analysis.url is not None

    try:
        snapshot = await _call_analyzer(client, analysis.url)
    except Exception as exc:  # analyzer HTTP hatasi, timeout, baglanti hatasi, SSRF reddi
        await _record_analyzer_failure(session, analysis, exc)
        return
    finally:
        if owns_client:
            await client.aclose()

    screenshot_b64 = snapshot["screenshot"]["base64_data"]
    try:
        screenshot_bytes = base64.b64decode(screenshot_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        analysis.status = PageAnalysisStatus.FAILED
        analysis.error = _SCREENSHOT_VALIDATION_FAILED_MESSAGE
        analysis.finished_at = _now()
        await session.flush()
        logger.warning(
            "URL kaynakli analiz ekran goruntusu base64 cozumlemesi basarisiz (id=%s): %s", analysis.id, exc
        )
        return

    # Analyzer'in bildirdigi genislik/yukseklik/format TEK BASINA guvenilmez;
    # saklanmadan ONCE GERCEKTEN decode edilerek dogrulanir (byte boyutu/format/
    # kare-sayisi/boyut/piksel/decompression-bomb - bkz. app.services.image_safety,
    # ayni DesignAsset yukleme hattiyla PAYLASILAN kod). Dogrulama basarisiz
    # olursa is guvenli bicimde 'failed' olur - kismi/tutarsiz bir SUCCEEDED
    # kaydi ASLA birakilmaz; ic hata ayrintisi yalnizca WARNING log'unda kalir.
    if len(screenshot_bytes) > settings.page_analysis_screenshot_max_bytes:
        analysis.status = PageAnalysisStatus.FAILED
        analysis.error = _SCREENSHOT_VALIDATION_FAILED_MESSAGE
        analysis.finished_at = _now()
        await session.flush()
        logger.warning(
            "URL kaynakli analiz ekran goruntusu boyutu sinirin uzerinde (id=%s, bytes=%s)",
            analysis.id,
            len(screenshot_bytes),
        )
        return

    try:
        image, real_content_type = image_safety.decode_and_validate_image(
            screenshot_bytes,
            allowed_formats=_URL_SCREENSHOT_ALLOWED_FORMATS,
            max_dimension=settings.page_analysis_screenshot_max_dimension,
            max_pixels=settings.page_analysis_screenshot_max_pixels,
        )
    except (InvalidImageError, ImageTooLargeError) as exc:
        analysis.status = PageAnalysisStatus.FAILED
        analysis.error = _SCREENSHOT_VALIDATION_FAILED_MESSAGE
        analysis.finished_at = _now()
        await session.flush()
        logger.warning(
            "URL kaynakli analiz ekran goruntusu dogrulamasi basarisiz (id=%s): %s", analysis.id, exc
        )
        return

    with image:
        real_width, real_height = image.size

    analysis.screenshot_data = screenshot_bytes
    analysis.screenshot_expires_at = _now() + timedelta(
        seconds=settings.page_analysis_screenshot_retention_seconds
    )
    analysis.screenshot_content_type = real_content_type
    analysis.image_width = real_width
    analysis.image_height = real_height
    analysis.content_sha256 = hashlib.sha256(screenshot_bytes).hexdigest()
    analysis.snapshot_version = snapshot["snapshot_version"]
    analysis.analyzer_version = snapshot["analyzer_version"]
    analysis.source = snapshot["source"]
    features = _extract_features(snapshot)
    # URL kaynaginda tiklama adaylari DOM semantiginden gelir; gorsel odak ise
    # ayni guvenli screenshot'in piksel kontrasti/kenar yogunlugundan AYRI
    # uretilir. Boylece bir fiyat baslik etiketi tasiyor diye otomatik olarak
    # "gorsel odak" sonucu olmaz ve tiklama puaniyla karismaz.
    try:
        visual_features = image_visual_analysis.analyze_screenshot(screenshot_bytes)
    except image_visual_analysis.VisualAnalysisError as exc:
        logger.warning("URL screenshot gorsel dikkat analizi atlandi (id=%s): %s", analysis.id, exc)
    else:
        features["visual_attention_algorithm_version"] = visual_features.get("algorithm_version")
        features["synthetic_attention_estimate"] = visual_features.get("synthetic_attention_estimate")
    analysis.features = features
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
    """Saklama suresi dolmus ekran goruntusu verilerini siler; metadata satiri kalir.

    Paket 4 Final: kisa TTL'si dolmus olsa bile, bagli oldugu run'in
    TAMAMLANMIS bir Report'u varsa purge ertelenir - bunun yerine, raporun
    OLUSTURULMA zamanindan (Report.created_at) itibaren islenen, ayrica
    belgelenmis ve SINIRLI (suresiz DEGIL) bir `report_linked_screenshot_
    retention_seconds` penceresi uygulanir (bkz. app.config.settings) -
    aksi halde tamamlanmis bir raporun gorsel katmani, rapora hicbir bagi
    olmayan kisa-omurlu bir capture gibi sessizce kaybolurdu. Rapora/run'a
    HIC baglanmamis capture'lar (veya raporu henuz olusturulmamis run'lar)
    icin davranis DEGISMEZ - mevcut kisa TTL ile purge edilmeye devam eder.
    """

    result = await session.execute(
        select(PageAnalysis).where(
            PageAnalysis.screenshot_data.is_not(None),
            PageAnalysis.screenshot_expires_at.is_not(None),
            PageAnalysis.screenshot_expires_at < _now(),
        )
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return 0

    candidate_ids = [analysis.id for analysis in candidates]
    linked_result = await session.execute(
        select(SimulationRun.page_analysis_id, Report.created_at)
        .join(Report, Report.simulation_run_id == SimulationRun.id)
        .where(SimulationRun.page_analysis_id.in_(candidate_ids))
    )
    # Ayni PageAnalysis'e (teorik olarak) birden fazla Report baglanmis olsa
    # bile, ongorulebilir/tutarli davranis icin EN ERKEN Report.created_at
    # esas alinir (en uzun degil).
    earliest_report_created_at: dict[uuid.UUID, datetime] = {}
    for analysis_id, created_at in linked_result.all():
        existing = earliest_report_created_at.get(analysis_id)
        if existing is None or created_at < existing:
            earliest_report_created_at[analysis_id] = created_at

    now = _now()
    expired: list[PageAnalysis] = []
    for analysis in candidates:
        report_created_at = earliest_report_created_at.get(analysis.id)
        if report_created_at is not None:
            report_retained_until = report_created_at + timedelta(
                seconds=settings.report_linked_screenshot_retention_seconds
            )
            if report_retained_until > now:
                continue
        expired.append(analysis)

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
