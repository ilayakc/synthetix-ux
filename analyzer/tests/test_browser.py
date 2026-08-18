import asyncio

import pytest

from app import browser


class _Request:
    def __init__(self, *, url: str, resource_type: str):
        self.url = url
        self.resource_type = resource_type


class _Route:
    def __init__(self, request: _Request):
        self.request = request
        self.aborted = False
        self.continued = False

    async def abort(self):
        self.aborted = True

    async def continue_(self):
        self.continued = True


class _ResponseRequest:
    def __init__(self, *, resource_type: str, frame):
        self.resource_type = resource_type
        self.frame = frame


class _Response:
    def __init__(self, *, resource_type: str, frame, length: int):
        self.headers = {"content-length": str(length)}
        self.request = _ResponseRequest(resource_type=resource_type, frame=frame)
        self.url = "https://cdn.example.com/assets/large-resource"


class _Page:
    def __init__(self):
        self.main_frame = object()


def test_oversized_main_document_is_terminal(monkeypatch):
    monkeypatch.setattr(browser.settings, "max_response_bytes", 100)
    page = _Page()
    response = _Response(resource_type="document", frame=page.main_frame, length=101)

    details = browser._oversized_response_details(response, page)

    assert details == ("fail", "cdn.example.com", "document", 101)


@pytest.mark.parametrize("resource_type", ["image", "script", "font", "xhr"])
def test_oversized_subresource_keeps_basic_analysis(monkeypatch, resource_type):
    monkeypatch.setattr(browser.settings, "max_response_bytes", 100)
    page = _Page()
    response = _Response(resource_type=resource_type, frame=page.main_frame, length=101)

    details = browser._oversized_response_details(response, page)

    assert details == ("continue", "cdn.example.com", resource_type, 101)


def test_oversized_iframe_document_keeps_basic_analysis(monkeypatch):
    monkeypatch.setattr(browser.settings, "max_response_bytes", 100)
    page = _Page()
    response = _Response(resource_type="document", frame=object(), length=101)

    details = browser._oversized_response_details(response, page)

    assert details == ("continue", "cdn.example.com", "document", 101)


@pytest.mark.asyncio
async def test_route_handler_blocks_media_without_failing_page():
    state: dict[str, int] = {}
    route = _Route(_Request(url="https://example.com/hero.mp4", resource_type="media"))
    handler = browser._make_route_handler(
        "example.com", {"example.com": True}, {"count": 0}, state
    )

    await handler(route)

    assert route.aborted is True
    assert route.continued is False
    assert state == {"media": 1}


@pytest.mark.asyncio
async def test_route_handler_keeps_non_media_resources():
    state: dict[str, int] = {}
    route = _Route(_Request(url="https://example.com/app.js", resource_type="script"))
    handler = browser._make_route_handler(
        "example.com", {"example.com": True}, {"count": 0}, state
    )

    await handler(route)

    assert route.aborted is False
    assert route.continued is True
    assert state == {}


class _AxeResult:
    response = {
        "violations": [{"id": "color-contrast", "nodes": []}],
        "passes": [{"id": "document-title"}],
        "incomplete": [],
    }


@pytest.mark.asyncio
async def test_accessibility_precheck_returns_completed_result(monkeypatch):
    class _Axe:
        async def run(self, page, options):
            assert page == "page"
            assert "violations" in options["resultTypes"]
            return _AxeResult()

    monkeypatch.setattr(browser, "Axe", _Axe)
    warnings: list[str] = []

    result = await browser._run_accessibility_precheck("page", warnings)

    assert result["scan_status"] == "completed"
    assert result["violations"][0]["id"] == "color-contrast"
    assert warnings == []


@pytest.mark.asyncio
async def test_accessibility_timeout_keeps_snapshot_usable(monkeypatch):
    class _Axe:
        async def run(self, page, options):
            await asyncio.sleep(0.05)

    monkeypatch.setattr(browser, "Axe", _Axe)
    monkeypatch.setattr(browser.settings, "accessibility_scan_timeout_seconds", 0.001)
    warnings: list[str] = []

    result = await browser._run_accessibility_precheck("page", warnings)

    assert result == {
        "scan_status": "skipped",
        "violations": [],
        "passes": [],
        "incomplete": [],
    }
    assert "mevcut verilerle tamamlandi" in warnings[0]


@pytest.mark.asyncio
async def test_accessibility_runtime_error_keeps_snapshot_usable(monkeypatch):
    class _Axe:
        async def run(self, page, options):
            raise RuntimeError("axe crashed")

    monkeypatch.setattr(browser, "Axe", _Axe)
    warnings: list[str] = []

    result = await browser._run_accessibility_precheck("page", warnings)

    assert result["scan_status"] == "skipped"
    assert result["violations"] == []
    assert "tamamlanamadi" in warnings[0]


def test_empty_page_snapshot_requires_all_meaningful_signals_to_be_absent():
    empty = {
        "title": "",
        "text_stats": {"visible_text_char_count": 0},
        "controls": {"link_count": 0, "button_count": 0, "form_field_count": 0},
        "element_boxes": [],
    }
    assert browser._is_empty_page_snapshot(empty) is True

    with_button = {**empty, "controls": {**empty["controls"], "button_count": 1}}
    assert browser._is_empty_page_snapshot(with_button) is False

    with_title = {**empty, "title": "Gorsel katalog"}
    assert browser._is_empty_page_snapshot(with_title) is False


@pytest.mark.asyncio
async def test_empty_page_snapshot_is_checked_bounded_times_before_typed_failure(monkeypatch):
    empty = {
        "title": "",
        "text_stats": {"visible_text_char_count": 0},
        "controls": {"link_count": 0, "button_count": 0, "form_field_count": 0},
        "element_boxes": [],
    }

    class _Page:
        def __init__(self):
            self.evaluate_count = 0
            self.wait_count = 0

        async def evaluate(self, _script, _capture_height):
            self.evaluate_count += 1
            return empty

        async def wait_for_timeout(self, _delay):
            self.wait_count += 1

    monkeypatch.setattr(browser.settings, "empty_snapshot_max_checks", 3)
    monkeypatch.setattr(browser.settings, "empty_snapshot_retry_delay_ms", 1)
    page = _Page()

    with pytest.raises(browser.AnalysisError) as exc_info:
        await browser._extract_features_with_bounded_readiness(page, 900)

    assert exc_info.value.code == browser.EMPTY_PAGE_SNAPSHOT_CODE
    assert page.evaluate_count == 3
    assert page.wait_count == 2
