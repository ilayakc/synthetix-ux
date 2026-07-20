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
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.page_analysis import PageAnalysis, PageAnalysisStatus
from app.models.tenancy import Organization
from app.services import page_analysis as page_analysis_service
from app.services import url_safety
from app.services.exceptions import PageAnalysisNotFoundError, UnauthorizedPageAnalysisError

# `test_engine`, `session` ve `organization` fixture'lari artik
# tests/conftest.py'de paylasilan altyapidan gelir (izole test veritabani +
# her testte rollback).

pytestmark = pytest.mark.integration


def _fixture_analyzer_snapshot(*, redirect_count: int = 0, final_url: str = "https://example.com/") -> dict:
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
            "base64_data": base64.b64encode(b"fake-png-bytes").decode("ascii"),
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
    assert analysis.screenshot_data == b"fake-png-bytes"
    assert analysis.screenshot_expires_at is not None

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
