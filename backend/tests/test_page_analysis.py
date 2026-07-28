"""URL analiz servisi (page_analysis) icin servis/DB katmani guvenlik ve
durum makinesi testleri.

Gercek bir Postgres'e karsi calisir (bkz. `test_simulation_engine.py` ile
ayni `session`/`organization` fixture deseni); analyzer'in HTTP cagrisi
`httpx.MockTransport` ile sahtelenir (gercek Playwright/ag erisimi yok).

API katmani testleri (`TestClient` + cookie/CSRF) kasitli olarak AYRI bir
dosyadadir: bkz. `test_page_analysis_api.py` docstring'i (event loop
izolasyonu).
"""

import base64
import hashlib
import io
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from PIL import Image
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.design_assets import DesignAsset
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.models.tenancy import Organization
from app.services import design_assets as design_assets_service
from app.services import page_analysis as page_analysis_service
from app.services import url_safety
from app.services.exceptions import (
    DesignAssetNotFoundError,
    DesignAssetUnavailableError,
    PageAnalysisNotFoundError,
    PageAnalysisSourceConflictError,
    UnauthorizedPageAnalysisError,
)

# `test_engine`, `session` ve `organization` fixture'lari artik
# tests/conftest.py'de paylasilan altyapidan gelir (izole test veritabani +
# her testte rollback).

pytestmark = pytest.mark.integration


def _png_bytes(width: int = 40, height: int = 30, color: tuple[int, int, int] = (10, 200, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(80, 40, 10)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _animated_webp_bytes(width: int = 20, height: int = 20) -> bytes:
    frame_a = Image.new("RGB", (width, height), color=(255, 0, 0))
    frame_b = Image.new("RGB", (width, height), color=(0, 0, 255))
    buffer = io.BytesIO()
    frame_a.save(buffer, format="WEBP", save_all=True, append_images=[frame_b], duration=100, loop=0)
    return buffer.getvalue()


# Analyzer'in gercekte urettigi ekran goruntusunun sozlesmesiyle tutarli,
# TEK bir yerde tanimlanmis GERCEK/gecerli varsayilan PNG - eski, sahte
# `b"fake-png-bytes"` fixture'i (gercek bir gorsel DEGILDI) YERINE gecti;
# artik hem sıkı URL screenshot dogrulamasindan GECEBILIR hem de baseline
# testlerin is mantigini (SUCCEEDED, redaksiyon vb.) DEGISTIRMEZ.
def _default_url_screenshot_bytes() -> bytes:
    return _png_bytes(width=1366, height=900, color=(240, 240, 240))


def _fixture_analyzer_snapshot(
    *, redirect_count: int = 0, final_url: str = "https://example.com/", screenshot_bytes: bytes | None = None
) -> dict:
    screenshot_bytes = screenshot_bytes if screenshot_bytes is not None else _default_url_screenshot_bytes()
    return {
        "snapshot_version": "page-feature-snapshot-live-2026.1",
        "source": "analyzer_live",
        "analyzer_version": "analyzer-2026.1",
        "url": "https://example.com/",
        "final_url": final_url,
        "redirect_count": redirect_count,
        "title": "Example Domain",
        "headings": [{"level": 1, "text": "Example Domain", "order": 0}],
        "text_stats": {
            "word_count": 42,
            "avg_sentence_word_count": 8.5,
            "visible_text_char_count": 250,
            "heading_count": 1,
        },
        "controls": {"link_count": 1, "button_count": 0, "form_count": 0, "form_field_count": 0},
        "element_boxes": [{"role": "heading", "x": 10.0, "y": 20.0, "width": 300.0, "height": 40.0}],
        "layout_regions": {
            "ust_navigasyon": None,
            "hero_baslik": {"x_pct": 5.0, "y_pct": 10.0, "width_pct": 90.0, "height_pct": 8.0},
            "birincil_cta": None,
            "govde_metni": None,
            "alt_bilgi": None,
        },
        "performance": {
            "dom_content_loaded_ms": 120.0,
            "load_event_ms": 200.0,
            "first_contentful_paint_ms": 100.0,
            "total_navigation_ms": 200.0,
        },
        "contrast_candidates": [
            {
                "selector": "p",
                "foreground": "rgb(0, 0, 0)",
                "background": "rgb(255, 255, 255)",
                "ratio": 21.0,
                "meets_aa": True,
            }
        ],
        "accessibility_precheck": {
            "disclaimer": (
                "Bu, axe-core ile calistirilan otomatik bir ON KONTROLDUR; tam bir WCAG "
                "uygunluk sertifikasi degildir."
            ),
            "violations": [],
            "passes_count": 5,
            "incomplete_count": 0,
        },
        "screenshot": {
            "format": "png",
            "width": 1366,
            "height": 900,
            "base64_data": base64.b64encode(screenshot_bytes).decode("ascii"),
        },
        "warnings": [] if redirect_count == 0 else [f"{redirect_count} yonlendirme takip edildi"],
    }


def _mock_client(
    *, status_code: int = 200, json_body: dict | None = None, raise_exc: Exception | None = None
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status_code, json=json_body)

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


async def _make_queued_analysis(
    session: AsyncSession, organization: Organization, *, url: str = "https://example.com/"
) -> PageAnalysis:
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        url=url,
        authorization_confirmed=True,
    )
    return analysis


async def _upload_asset(session: AsyncSession, organization: Organization, **overrides) -> DesignAsset:
    return await design_assets_service.upload_design_asset(
        session,
        organization_id=organization.id,
        uploaded_by_user_id=None,
        raw_bytes=_png_bytes(),
        **overrides,
    )


def _analyzer_should_not_be_called_client() -> httpx.AsyncClient:
    """DesignAsset kaynakli isleme sirasinda analyzer'a HICBIR istek yapilmamasi
    gerektigini kanitlamak icin kullanilir - herhangi bir istek gelirse test
    basarisiz olur."""

    async def handler(
        request: httpx.Request,
    ) -> httpx.Response:  # pragma: no cover - hicbir zaman cagrilmamali
        raise AssertionError(f"analyzer'a beklenmeyen istek yapildi: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Sema / SSRF reddi (url_safety, servis katmani) --------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "not-a-url",
        "",
    ],
)
@pytest.mark.security
async def test_create_analysis_rejects_disallowed_schemes(
    session: AsyncSession, organization: Organization, url: str
):
    with pytest.raises(url_safety.UnsafeUrlError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            url=url,
            authorization_confirmed=True,
        )


@pytest.mark.security
async def test_create_analysis_rejects_credentials_in_url(session: AsyncSession, organization: Organization):
    with pytest.raises(url_safety.UnsafeUrlError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            url="https://user:pass@example.com/",
            authorization_confirmed=True,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",  # bulut metadata IP'si
        "http://10.0.0.5/",
        "http://[::1]/",
    ],
)
@pytest.mark.security
async def test_create_analysis_rejects_private_and_metadata_targets(
    session: AsyncSession, organization: Organization, url: str
):
    with pytest.raises(url_safety.UnsafeUrlError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            url=url,
            authorization_confirmed=True,
        )


@pytest.mark.security
async def test_create_analysis_dns_rebinding_mock_is_rejected(
    session: AsyncSession, organization: Organization, monkeypatch
):
    """Hostname, dogrulama sirasinda ozel bir IP'ye cozumleniyormus gibi
    sahtelenir (rebinding senaryosunun 'saldiri basarili olsaydi' hali);
    dogrulamanin bu durumu tespit edip reddettigini kanitlar."""

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("10.0.0.9",))

    with pytest.raises(url_safety.UnsafeUrlError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            url="https://rebinding-target.example.com/",
            authorization_confirmed=True,
        )


# --- Yetki onayi --------------------------------------------------------------


@pytest.mark.security
async def test_create_analysis_requires_authorization_confirmation(
    session: AsyncSession, organization: Organization
):
    with pytest.raises(UnauthorizedPageAnalysisError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            url="https://example.com/",
            authorization_confirmed=False,
        )


# --- Basarili akis (public fixture, mock analyzer) ----------------------------


async def test_process_analysis_success_stores_versioned_snapshot_and_redacts(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(json_body=_fixture_analyzer_snapshot())
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.snapshot_version == "page-feature-snapshot-live-2026.1"
    assert analysis.analyzer_version == "analyzer-2026.1"
    assert analysis.source == "analyzer_live"
    assert analysis.screenshot_data == _default_url_screenshot_bytes()
    assert analysis.screenshot_expires_at is not None
    assert analysis.image_width == 1366
    assert analysis.image_height == 900
    assert analysis.screenshot_content_type == "image/png"
    assert analysis.content_sha256 == hashlib.sha256(_default_url_screenshot_bytes()).hexdigest()

    # Redaksiyon: yalnizca bilinen, turetilmis alanlar saklanir; ham HTML,
    # form degeri, cookie veya token asla bir alan adi olarak gorunmez.
    assert set(analysis.features.keys()) == {
        "final_url",
        "redirect_count",
        "title",
        "headings",
        "text_stats",
        "controls",
        "element_boxes",
        "layout_regions",
        "performance",
        "contrast_candidates",
        "accessibility_precheck",
        "warnings",
    }
    forbidden_substrings = ("html", "cookie", "token", "form_value", "password")
    features_repr = str(analysis.features).lower()
    for forbidden in forbidden_substrings:
        assert forbidden not in features_repr


async def test_process_analysis_stores_redirect_metadata(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    snapshot = _fixture_analyzer_snapshot(redirect_count=2, final_url="https://example.com/hosgeldiniz")
    client = _mock_client(json_body=snapshot)
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.features["redirect_count"] == 2
    assert analysis.features["final_url"] == "https://example.com/hosgeldiniz"
    assert "yonlendirme" in analysis.features["warnings"][0]


# --- Basarisizlik: timeout / buyuk yanit / analyzer SSRF reddi ---------------


async def test_process_analysis_marks_failed_on_timeout(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(raise_exc=httpx.TimeoutException("navigasyon zaman asimi"))
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert "zaman asimi" in analysis.error.lower()
    assert analysis.screenshot_data is None


async def test_process_analysis_marks_failed_when_analyzer_rejects_oversized_response(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(
        status_code=502, json_body={"detail": "Yanit boyutu sinirini asti (>10485760 bayt)"}
    )
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert "boyutu" in analysis.error.lower()


@pytest.mark.security
async def test_process_analysis_marks_failed_when_analyzer_rejects_ssrf(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(
        status_code=400, json_body={"detail": "Hostname engellenmis bir IP'ye cozumleniyor"}
    )
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert "engellenmis" in analysis.error.lower()


# --- Durum makinesi: claim / reap --------------------------------------------


async def test_claim_next_queued_transitions_to_running(session: AsyncSession, organization: Organization):
    analysis = await _make_queued_analysis(session, organization)
    claimed = await page_analysis_service.claim_next_queued(session, limit=10_000)

    assert any(a.id == analysis.id for a in claimed)
    await session.refresh(analysis)
    assert analysis.status == PageAnalysisStatus.RUNNING
    assert analysis.attempt_count == 1
    assert analysis.started_at is not None


async def test_reap_stale_running_requeues_when_under_max_attempts(
    session: AsyncSession, organization: Organization
):
    import datetime as dt

    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING
    analysis.attempt_count = 1
    await session.flush()
    await session.execute(
        PageAnalysis.__table__.update()
        .where(PageAnalysis.id == analysis.id)
        .values(updated_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    )

    reaped = await page_analysis_service.reap_stale_running(session, timeout_seconds=1, max_attempts=3)
    assert reaped == 1
    await session.refresh(analysis)
    assert analysis.status == PageAnalysisStatus.QUEUED


async def test_reap_stale_running_fails_after_max_attempts(session: AsyncSession, organization: Organization):
    import datetime as dt

    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING
    analysis.attempt_count = 3
    await session.flush()
    await session.execute(
        PageAnalysis.__table__.update()
        .where(PageAnalysis.id == analysis.id)
        .values(updated_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    )

    await page_analysis_service.reap_stale_running(session, timeout_seconds=1, max_attempts=3)
    await session.refresh(analysis)
    assert analysis.status == PageAnalysisStatus.FAILED


@pytest.mark.security
async def test_get_owned_analysis_rejects_other_organization(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_queued_analysis(session, organization)
    with pytest.raises(PageAnalysisNotFoundError):
        await page_analysis_service.get_owned_analysis(session, uuid.uuid4(), analysis.id)


async def test_purge_expired_screenshots_clears_data_but_keeps_row(
    session: AsyncSession, organization: Organization, monkeypatch
):
    import datetime as dt

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING
    client = _mock_client(json_body=_fixture_analyzer_snapshot())
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()
    assert analysis.screenshot_data is not None

    analysis.screenshot_expires_at = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    await session.flush()

    purged = await page_analysis_service.purge_expired_screenshots(session)
    assert purged == 1
    await session.refresh(analysis)
    assert analysis.screenshot_data is None
    assert analysis.screenshot_expires_at is None
    # Metadata satiri (basarili sonuc/ozellikler) korunur, yalnizca ikili veri silinir.
    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.features is not None


# --- URL capture: yeni boyut/content-type/hash alanlari ----------------------


async def test_process_analysis_url_populates_dimensions_and_hash(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    png_bytes = _png_bytes(width=64, height=48)
    snapshot = _fixture_analyzer_snapshot()
    snapshot["screenshot"]["base64_data"] = base64.b64encode(png_bytes).decode("ascii")
    client = _mock_client(json_body=snapshot)
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.source_kind == PageAnalysisSourceKind.URL
    assert analysis.image_width == 64
    assert analysis.image_height == 48
    assert analysis.screenshot_content_type == "image/png"
    assert analysis.content_sha256 == hashlib.sha256(png_bytes).hexdigest()


async def test_process_analysis_url_rejects_undecodable_screenshot_bytes(
    session: AsyncSession, organization: Organization, monkeypatch
):
    """Analyzer'in gonderdigi 'screenshot' metadata'sina (format/boyut alanlari)
    TEK BASINA guvenilmez - saklanacak byte dizisi GERCEKTEN decode edilerek
    dogrulanir. Decode basarisiz olursa is 'failed' olmalidir; kismi/tutarsiz
    bir SUCCEEDED kaydi ASLA birakilmaz."""

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    snapshot = _fixture_analyzer_snapshot()
    snapshot["screenshot"]["base64_data"] = base64.b64encode(b"not-a-real-image").decode("ascii")
    client = _mock_client(json_body=snapshot)
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None
    assert analysis.image_width is None
    assert analysis.image_height is None
    assert analysis.screenshot_content_type is None
    assert analysis.content_sha256 is None
    assert analysis.snapshot_version is None
    assert analysis.analyzer_version is None
    assert analysis.features is None
    assert analysis.error == "Ekran goruntusu guvenlik dogrulamasindan gecemedi"
    # Ic hata ayrintisi (ham binary, stack trace) API/DB'ye sizmaz - yalnizca
    # genel, sabit bir mesaj saklanir.
    assert "not-a-real-image" not in analysis.error
    assert "Traceback" not in analysis.error


async def test_process_analysis_url_rejects_format_mismatch(
    session: AsyncSession, organization: Organization, monkeypatch
):
    """Analyzer sozlesmesi yalnizca PNG uretir; saklanacak bayt dizisi GERCEKTE
    baska bir formatsa (ör. JPEG) - sozlesme celismesi - guvenli bicimde
    reddedilir."""

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    snapshot = _fixture_analyzer_snapshot(screenshot_bytes=_jpeg_bytes())
    client = _mock_client(json_body=snapshot)
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


async def test_process_analysis_url_rejects_animated_format(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    snapshot = _fixture_analyzer_snapshot(screenshot_bytes=_animated_webp_bytes())
    client = _mock_client(json_body=snapshot)
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


async def test_process_analysis_url_rejects_oversized_byte_count(
    session: AsyncSession, organization: Organization, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    monkeypatch.setattr(settings, "page_analysis_screenshot_max_bytes", 10)
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(json_body=_fixture_analyzer_snapshot())
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


async def test_process_analysis_url_rejects_oversized_dimensions(
    session: AsyncSession, organization: Organization, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    monkeypatch.setattr(settings, "page_analysis_screenshot_max_dimension", 10)
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(json_body=_fixture_analyzer_snapshot())
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


async def test_process_analysis_url_rejects_decompression_bomb(
    session: AsyncSession, organization: Organization, monkeypatch
):
    """Pillow'un kendi `MAX_IMAGE_PIXELS` esiginin 2 katindan fazlasi HER ZAMAN
    `DecompressionBombError` firlatir (bkz. test_design_assets_api.py'deki
    ayni desen) - bu, `page_analysis_screenshot_max_pixels`'den BAGIMSIZ,
    Pillow'un kendi savunma katmanidir ve devre disi birakilmaz."""

    from PIL import Image as PILImage

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 100)
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    client = _mock_client(
        json_body=_fixture_analyzer_snapshot(screenshot_bytes=_png_bytes(width=50, height=40))
    )
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


async def test_process_analysis_url_rejects_malformed_base64(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)
    analysis.status = PageAnalysisStatus.RUNNING

    snapshot = _fixture_analyzer_snapshot()
    snapshot["screenshot"]["base64_data"] = "%%%not-valid-base64%%%"
    client = _mock_client(json_body=snapshot)
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


# --- Ortak kaynak sozlesmesi: request dogrulama --------------------------------


async def test_create_analysis_accepts_url_only(
    session: AsyncSession, organization: Organization, monkeypatch
):
    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        url="https://example.com/",
        authorization_confirmed=True,
    )
    assert analysis.source_kind == PageAnalysisSourceKind.URL
    assert analysis.design_asset_id is None


async def test_create_analysis_accepts_design_asset_only(session: AsyncSession, organization: Organization):
    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    assert analysis.source_kind == PageAnalysisSourceKind.DESIGN_ASSET
    assert analysis.design_asset_id == asset.id
    assert analysis.url is None
    assert analysis.status == PageAnalysisStatus.QUEUED


async def test_create_analysis_rejects_both_url_and_design_asset(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    with pytest.raises(PageAnalysisSourceConflictError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            url="https://example.com/",
            design_asset_id=asset.id,
            authorization_confirmed=True,
        )


async def test_create_analysis_rejects_neither_source(session: AsyncSession, organization: Organization):
    with pytest.raises(PageAnalysisSourceConflictError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
        )


# --- Tenant ve asset guvenligi -------------------------------------------------


@pytest.mark.security
async def test_create_analysis_design_asset_rejects_other_organization(
    session: AsyncSession, organization: Organization, make_organization
):
    other_org = await make_organization("Other Org")
    asset = await _upload_asset(session, other_org)

    with pytest.raises(DesignAssetNotFoundError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            design_asset_id=asset.id,
        )


async def test_create_analysis_rejects_deleted_design_asset(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    await design_assets_service.delete_asset(session, organization.id, asset.id)

    with pytest.raises(DesignAssetUnavailableError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            design_asset_id=asset.id,
        )


async def test_create_analysis_rejects_expired_design_asset(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    asset.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(DesignAssetUnavailableError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            design_asset_id=asset.id,
        )


async def test_create_analysis_rejects_asset_with_purged_binary(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    asset.image_data = None
    await session.flush()

    with pytest.raises(DesignAssetUnavailableError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            design_asset_id=asset.id,
        )


async def test_create_analysis_design_asset_tenant_cannot_be_overridden(
    session: AsyncSession, organization: Organization, make_organization
):
    """`organization_id` her zaman cagiran (principal) tarafindan belirlenir;
    servis imzasinda istekten gelen baska bir alan yoktur - bu test, bir
    baska org'un asset'ine, o org'un id'siyle erisimin calistigini ama
    KENDI org'un id'siyle CALISMADIGINI (404) dogrulayarak tenant izolasyonunu
    kanitlar."""

    owner_org = await make_organization("Owner Org")
    asset = await _upload_asset(session, owner_org)

    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=owner_org.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    assert analysis.organization_id == owner_org.id

    with pytest.raises(DesignAssetNotFoundError):
        await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            design_asset_id=asset.id,
        )


async def test_process_analysis_design_asset_fails_when_deleted_after_queue(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    # Kuyruga alindiktan SONRA silinir - worker create-time kontrolune
    # guvenmemeli, kendi bagimsiz kontrolunu yapmali.
    await design_assets_service.delete_asset(session, organization.id, asset.id)

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None
    assert analysis.error is not None
    # Ic hata ayrintisi (asset id, DB detaylari) API'ye/loglara sizmaz.
    assert str(asset.id) not in analysis.error


async def test_process_analysis_design_asset_fails_when_expired_after_queue(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    asset.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.screenshot_data is None


# --- Worker/analyzer ayrimi -----------------------------------------------------


async def test_process_analysis_design_asset_never_calls_analyzer(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        # AssertionError firlamadan basariyla tamamlanmasi, analyzer'a HICBIR
        # istek yapilmadiginin kaniti (mock transport, herhangi bir istekte
        # AssertionError firlatir).
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED


async def test_process_analysis_design_asset_produces_no_dom_fields(
    session: AsyncSession, organization: Organization
):
    """DesignAsset yolu, yerel OpenCV analizinden `features` uretir (Paket 4C)
    ama hicbir zaman DOM-only alan (element_boxes/layout_regions/
    contrast_candidates - bunlar yalnizca gercek analyzer/DOM verisiyle
    doldurulur) icermez; `feature_source` acikca `visual_heuristic` olur."""

    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.features is not None
    assert analysis.features["feature_source"] == "visual_heuristic"
    assert "visual_cta_candidates" in analysis.features
    assert "synthetic_attention_estimate" in analysis.features
    for dom_only_field in ("element_boxes", "layout_regions", "contrast_candidates", "controls"):
        assert dom_only_field not in analysis.features


async def test_process_analysis_design_asset_opencv_failure_yields_safe_failed(
    session: AsyncSession, organization: Organization, monkeypatch: pytest.MonkeyPatch
):
    """OpenCV/gorsel analiz basarisiz olursa is guvenli bicimde FAILED olmali;
    ne kismi `features` ne de `screenshot_data` yazilmali, analyzer'a hicbir
    istek yapilmamali, ic hata ayrintisi analysis.error'a sizmamalidir."""

    from app.services import image_visual_analysis

    def _boom(image_bytes: bytes) -> dict:
        raise image_visual_analysis.VisualAnalysisError("gizli ic hata ayrintisi")

    monkeypatch.setattr(image_visual_analysis, "analyze_screenshot", _boom)

    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.FAILED
    assert analysis.features is None
    assert analysis.screenshot_data is None
    assert analysis.error == "Gorsel analiz basarisiz oldu"
    assert "gizli ic hata ayrintisi" not in (analysis.error or "")


async def test_process_analysis_design_asset_idempotent_on_retry(
    session: AsyncSession, organization: Organization
):
    """Ayni is iki kez islenirse (ör. reap sonrasi yeniden kuyruga alinip
    tekrar calistirilirsa) sonuc tutarli kalir - analyzer'a hicbir istek
    yapilmadigi icin dogal olarak idempotenttir."""

    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
        first_hash = analysis.content_sha256
        analysis.status = PageAnalysisStatus.RUNNING
        await session.flush()
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.content_sha256 == first_hash


# --- Snapshot davranisi ---------------------------------------------------------


async def test_process_analysis_design_asset_snapshot_is_byte_identical(
    session: AsyncSession, organization: Organization
):
    source_bytes = _png_bytes(width=77, height=55)
    asset = await design_assets_service.upload_design_asset(
        session,
        organization_id=organization.id,
        uploaded_by_user_id=None,
        raw_bytes=source_bytes,
    )
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    # DesignAsset upload sirasinda EXIF temizleme/yeniden encode edildigi icin
    # `asset.image_data`, orijinal `source_bytes` ile birebir ayni olmayabilir
    # (bkz. app.services.design_assets); dogru karsilastirma, PageAnalysis'in
    # KENDI kaynagi olan `asset.image_data` ile bire bir ayniligidir.
    assert analysis.screenshot_data == asset.image_data
    assert analysis.image_width == asset.width
    assert analysis.image_height == asset.height
    assert analysis.screenshot_content_type == asset.content_type.value
    assert analysis.content_sha256 == hashlib.sha256(analysis.screenshot_data).hexdigest()


async def test_process_analysis_design_asset_same_source_yields_same_hash(
    session: AsyncSession, organization: Organization
):
    source_bytes = _png_bytes(width=33, height=22)
    asset_a = await design_assets_service.upload_design_asset(
        session,
        organization_id=organization.id,
        uploaded_by_user_id=None,
        raw_bytes=source_bytes,
    )
    asset_b = await design_assets_service.upload_design_asset(
        session,
        organization_id=organization.id,
        uploaded_by_user_id=None,
        raw_bytes=source_bytes,
    )

    analyses = []
    for asset in (asset_a, asset_b):
        analysis = await page_analysis_service.create_analysis(
            session,
            organization_id=organization.id,
            requested_by_user_id=None,
            design_asset_id=asset.id,
        )
        analysis.status = PageAnalysisStatus.RUNNING
        await session.flush()
        client = _analyzer_should_not_be_called_client()
        try:
            await page_analysis_service.process_analysis(session, analysis, client=client)
        finally:
            await client.aclose()
        analyses.append(analysis)

    assert analyses[0].content_sha256 == analyses[1].content_sha256
    assert analyses[0].screenshot_data == analyses[1].screenshot_data


async def test_completed_design_asset_snapshot_survives_source_soft_delete(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    snapshot_bytes = analysis.screenshot_data
    assert snapshot_bytes is not None

    # Orijinal DesignAsset SONRADAN silinir (soft-delete: ikili veri temizlenir).
    await design_assets_service.delete_asset(session, organization.id, asset.id)

    await session.refresh(analysis)
    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.screenshot_data == snapshot_bytes


async def test_page_analysis_purge_still_clears_design_asset_screenshot(
    session: AsyncSession, organization: Organization
):
    import datetime as dt

    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    analysis.screenshot_expires_at = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    await session.flush()

    purged = await page_analysis_service.purge_expired_screenshots(session)
    assert purged == 1
    await session.refresh(analysis)
    assert analysis.screenshot_data is None
    assert analysis.status == PageAnalysisStatus.SUCCEEDED


# --- FK ON DELETE SET NULL / CHECK constraint uyumu (gercek hard-delete) ------


async def test_design_asset_real_hard_delete_nulls_fk_and_preserves_snapshot(
    session: AsyncSession, organization: Organization
):
    """Soft-delete DEGIL: DesignAsset satiri veritabanindan GERCEKTEN silinir.

    `fk_page_analyses_design_asset_id` (`ON DELETE SET NULL`) ve gevsetilmis
    CHECK constraint (bkz. migration d1e4a8c2f6b9 - orijinal, uygulanmis
    c9a2f6e1b7d4 migration'i degistirilmedi) birlikte uyumlu olmalidir: bu
    DELETE ifadesi HATASIZ tamamlanmali, tamamlanmis PageAnalysis satiri VE
    kendi `screenshot_data` kopyasi KALMALIDIR."""

    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()

    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()

    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    snapshot_bytes = analysis.screenshot_data
    assert snapshot_bytes is not None
    analysis_id = analysis.id

    # Gercek hard-delete - uygulama kodu bunu ASLA yapmaz (design_assets_service
    # yalnizca soft-delete sunar); burasi yalnizca DB-seviyesi FK/CHECK
    # constraint uyumunu dogrudan kanitlamak icindir.
    await session.execute(sa_delete(DesignAsset).where(DesignAsset.id == asset.id))
    await session.flush()

    await session.refresh(analysis)
    assert analysis.status == PageAnalysisStatus.SUCCEEDED
    assert analysis.design_asset_id is None  # FK ON DELETE SET NULL calisti
    assert analysis.source_kind == PageAnalysisSourceKind.DESIGN_ASSET  # provenance korunur
    assert analysis.screenshot_data == snapshot_bytes
    assert analysis.content_sha256 == hashlib.sha256(snapshot_bytes).hexdigest()

    # Preview endpoint'i (router coroutine'i dogrudan cagrilarak) hala calisir.
    from app.routers import page_analysis as page_analysis_router

    response = await page_analysis_router.get_screenshot(
        analysis_id, organization_id=organization.id, session=session
    )
    assert response.status_code == 200
    assert response.body == snapshot_bytes

    # Metadata response, kaynak turunu korur ve kaynak kaydinin artik mevcut
    # olmadigini guvenli bicimde ifade eder (ham DB detayi sizdirmadan).
    metadata_response = await page_analysis_router.get_analysis(
        analysis_id, organization_id=organization.id, session=session
    )
    assert metadata_response.source_kind == "design_asset"
    assert metadata_response.design_asset_id is None
    assert metadata_response.design_asset_still_linked is False


async def test_url_analysis_metadata_response_design_asset_still_linked_is_none(
    session: AsyncSession, organization: Organization, monkeypatch
):
    """URL kaynaginda kavram gecerli olmadigi icin alan `None` kalir."""

    monkeypatch.setattr(url_safety, "resolve_host_ips", lambda hostname: ("93.184.216.34",))
    analysis = await _make_queued_analysis(session, organization)

    from app.routers import page_analysis as page_analysis_router

    metadata_response = await page_analysis_router.get_analysis(
        analysis.id, organization_id=organization.id, session=session
    )
    assert metadata_response.source_kind == "url"
    assert metadata_response.design_asset_still_linked is None


async def test_design_asset_analysis_metadata_response_still_linked_true_before_delete(
    session: AsyncSession, organization: Organization
):
    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )

    from app.routers import page_analysis as page_analysis_router

    metadata_response = await page_analysis_router.get_analysis(
        analysis.id, organization_id=organization.id, session=session
    )
    assert metadata_response.source_kind == "design_asset"
    assert metadata_response.design_asset_still_linked is True


async def test_organization_hard_delete_cascades_without_fk_errors(
    session: AsyncSession, organization: Organization, make_organization
):
    """Organizasyonun kendisi gercekten hard-delete edildiginde (CASCADE:
    organizations -> design_assets VE organizations -> page_analyses), FK/CHECK
    constraint hatasi OLUSMAMALIDIR ve baska bir tenant'in kayitlari
    ETKILENMEMELIDIR."""

    other_org = await make_organization("Untouched Org")

    asset = await _upload_asset(session, organization)
    analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=organization.id,
        requested_by_user_id=None,
        design_asset_id=asset.id,
    )
    analysis.status = PageAnalysisStatus.RUNNING
    await session.flush()
    client = _analyzer_should_not_be_called_client()
    try:
        await page_analysis_service.process_analysis(session, analysis, client=client)
    finally:
        await client.aclose()
    assert analysis.status == PageAnalysisStatus.SUCCEEDED

    other_asset = await _upload_asset(session, other_org)
    other_analysis = await page_analysis_service.create_analysis(
        session,
        organization_id=other_org.id,
        requested_by_user_id=None,
        design_asset_id=other_asset.id,
    )
    other_analysis_id = other_analysis.id

    # Gercek organization hard-delete - herhangi bir FK/CHECK constraint
    # hatasi FIRLATMADAN tamamlanmalidir.
    await session.execute(sa_delete(Organization).where(Organization.id == organization.id))
    await session.flush()

    remaining_for_deleted_org = await session.execute(
        select(PageAnalysis).where(PageAnalysis.organization_id == organization.id)
    )
    assert remaining_for_deleted_org.scalars().all() == []

    remaining_assets_for_deleted_org = await session.execute(
        select(DesignAsset).where(DesignAsset.organization_id == organization.id)
    )
    assert remaining_assets_for_deleted_org.scalars().all() == []

    # Diger tenant'in kaydi ETKILENMEMIS olmali.
    other_still_there = await session.execute(
        select(PageAnalysis).where(PageAnalysis.id == other_analysis_id)
    )
    assert other_still_there.scalar_one() is not None
