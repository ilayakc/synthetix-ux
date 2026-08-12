"""Analyzer HTTP uc noktasinin (/internal/analyze) testleri.

Gercek bir Chromium baslatilmaz: `app.main.analyze_url`, `monkeypatch` ile
sahte bir uygulamaya baglanir. Amac, gercek tarayici davranisini degil,
uc nokta seviyesindeki guvenlik kontrollerini (token, yetki onayi, SSRF
hata haritalama, hata kodlari) dogrulamaktir.
"""

from fastapi.testclient import TestClient

from app import main
from app.browser import AnalysisError
from app.config import settings
from app.schemas import PageFeatureSnapshotV1
from app.url_safety import UnsafeUrlError

client = TestClient(main.app)

VALID_HEADERS = {"X-Analyzer-Token": settings.analyzer_shared_token}


def _fixture_snapshot() -> PageFeatureSnapshotV1:
    return PageFeatureSnapshotV1.model_validate(
        {
            "url": "https://example.com/",
            "final_url": "https://example.com/",
            "redirect_count": 0,
            "title": "Example",
            "headings": [],
            "text_stats": {
                "word_count": 10,
                "avg_sentence_word_count": 5.0,
                "visible_text_char_count": 60,
                "heading_count": 1,
            },
            "controls": {"link_count": 1, "button_count": 0, "form_count": 0, "form_field_count": 0},
            "element_boxes": [],
            "layout_regions": {},
            "performance": {
                "dom_content_loaded_ms": 100.0,
                "load_event_ms": 150.0,
                "first_contentful_paint_ms": 90.0,
                "total_navigation_ms": 150.0,
            },
            "contrast_candidates": [],
            "accessibility_precheck": {"violations": [], "passes_count": 3, "incomplete_count": 0},
            "screenshot": {"width": 1366, "height": 900, "base64_data": "aGVsbG8="},
            "warnings": [],
        }
    )


def test_health_check_does_not_require_token():
    response = client.get("/health")
    assert response.status_code == 200


def test_missing_token_is_rejected():
    response = client.post(
        "/internal/analyze", json={"url": "https://example.com/", "authorization_confirmed": True}
    )
    assert response.status_code == 401


def test_wrong_token_is_rejected():
    response = client.post(
        "/internal/analyze",
        json={"url": "https://example.com/", "authorization_confirmed": True},
        headers={"X-Analyzer-Token": "wrong-token"},
    )
    assert response.status_code == 401


def test_missing_authorization_confirmation_is_rejected():
    response = client.post(
        "/internal/analyze",
        json={"url": "https://example.com/", "authorization_confirmed": False},
        headers=VALID_HEADERS,
    )
    assert response.status_code == 400
    assert "yetki" in response.json()["detail"].lower()


def test_unsafe_url_is_rejected_with_valid_token_and_authorization(monkeypatch):
    async def _raise_unsafe(_url: str):
        raise UnsafeUrlError("Hostname engellenmis bir IP'ye cozumleniyor")

    monkeypatch.setattr(main, "analyze_url", _raise_unsafe)

    response = client.post(
        "/internal/analyze",
        json={"url": "http://127.0.0.1/", "authorization_confirmed": True},
        headers=VALID_HEADERS,
    )
    assert response.status_code == 400


def test_analysis_timeout_returns_502(monkeypatch):
    async def _raise_timeout(_url: str):
        raise AnalysisError("Sayfa zaman asimina ugradi (15s)")

    monkeypatch.setattr(main, "analyze_url", _raise_timeout)

    response = client.post(
        "/internal/analyze",
        json={"url": "https://slow.example.com/", "authorization_confirmed": True},
        headers=VALID_HEADERS,
    )
    assert response.status_code == 502


def test_empty_snapshot_returns_typed_safe_502(monkeypatch):
    async def _raise_empty(_url: str):
        raise AnalysisError("guvenli bos sayfa mesaji", code="empty_page_snapshot")

    monkeypatch.setattr(main, "analyze_url", _raise_empty)
    response = client.post(
        "/internal/analyze",
        json={"url": "https://example.com/", "authorization_confirmed": True},
        headers=VALID_HEADERS,
    )

    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "empty_page_snapshot",
        "message": "guvenli bos sayfa mesaji",
    }


def test_successful_analysis_returns_versioned_snapshot(monkeypatch):
    async def _fake_analyze(_url: str):
        return _fixture_snapshot()

    monkeypatch.setattr(main, "analyze_url", _fake_analyze)

    response = client.post(
        "/internal/analyze",
        json={"url": "https://example.com/", "authorization_confirmed": True},
        headers=VALID_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "analyzer_live"
    assert body["analyzer_version"]
    assert body["snapshot_version"]
    assert "otomatik" in body["accessibility_precheck"]["disclaimer"].lower()
