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


# --- Hafif ("lite") analiz modu ---------------------------------------------


def test_blocked_resource_types_lite_includes_font(monkeypatch):
    monkeypatch.setattr(browser.settings, "lite_mode", True)
    monkeypatch.setattr(browser.settings, "lite_blocked_resource_types", ("media", "font"))
    assert browser._blocked_resource_types() == ("media", "font")


def test_blocked_resource_types_full_only_media(monkeypatch):
    monkeypatch.setattr(browser.settings, "lite_mode", False)
    assert browser._blocked_resource_types() == ("media",)


@pytest.mark.asyncio
async def test_route_handler_blocks_font_in_lite_mode(monkeypatch):
    monkeypatch.setattr(browser.settings, "lite_mode", True)
    monkeypatch.setattr(browser.settings, "lite_blocked_resource_types", ("media", "font"))
    blocked: dict[str, int] = {}
    handler = browser._make_route_handler("example.com", {"example.com": True}, {"count": 0}, blocked)
    route = _Route(_Request(url="https://example.com/font.woff2", resource_type="font"))

    await handler(route)

    assert route.aborted is True
    assert blocked.get("font") == 1


@pytest.mark.asyncio
async def test_route_handler_allows_document_and_css_in_lite_mode(monkeypatch):
    monkeypatch.setattr(browser.settings, "lite_mode", True)
    monkeypatch.setattr(browser.settings, "lite_blocked_resource_types", ("media", "font"))
    handler = browser._make_route_handler("example.com", {"example.com": True}, {"count": 0}, {})
    for resource_type in ("document", "stylesheet", "script", "image"):
        route = _Route(_Request(url="https://example.com/x", resource_type=resource_type))
        await handler(route)
        assert route.continued is True, f"{resource_type} engellenmemeli"
        assert route.aborted is False


def test_memory_usage_fraction(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_enabled", True)
    monkeypatch.setattr(browser, "_effective_limit_bytes", lambda: 512 * 1024 * 1024)
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: 384 * 1024 * 1024)
    assert browser._memory_usage_fraction() == pytest.approx(0.75)

    monkeypatch.setattr(browser.settings, "memory_guard_enabled", False)
    assert browser._memory_usage_fraction() is None


# --- network_device_test: context kapanis dayanikliligi -----------------------
#
# Render production regresyonu: bir profil olculurken watchdog browser'i kapatir
# (bellek koruma) veya Chromium context'i geri donusturur; ardindan `finally`
# blogundaki `context.close()` "Target.disposeBrowserContext: Failed to find
# context with id" PlaywrightError'i firlatirdi. Bu cleanup hatasi hem basarili
# olcumu maskeliyor hem de except bloklariyla YAKALANMADIGI icin (finally'de
# olusuyor) tum `analyze_device_network` istegini 502'ye dusuruyordu.


class _FakeProfileAxeResult:
    response = {"violations": []}


class _FakeProfileAxe:
    async def run(self, page, options):
        return _FakeProfileAxeResult()


class _FakeProfilePage:
    def __init__(self, goto_exc=None):
        self._goto_exc = goto_exc
        self.goto_url = None

    def set_default_navigation_timeout(self, _ms):
        pass

    def set_default_timeout(self, _ms):
        pass

    async def route(self, _pattern, _handler):
        pass

    async def goto(self, url, wait_until):
        self.goto_url = url
        if self._goto_exc is not None:
            raise self._goto_exc

    async def evaluate(self, _script):
        return {
            "dom_content_loaded_ms": 100.0,
            "load_event_ms": 200.0,
            "total_navigation_ms": 200.0,
        }


class _FakeProfileContext:
    def __init__(self, *, close_exc=None, goto_exc=None):
        self._close_exc = close_exc
        self._goto_exc = goto_exc
        self.close_calls = 0

    async def new_page(self):
        return _FakeProfilePage(goto_exc=self._goto_exc)

    async def close(self):
        self.close_calls += 1
        if self._close_exc is not None:
            raise self._close_exc


class _FakeProfileBrowser:
    def __init__(self, context):
        self._context = context

    async def new_context(self, **_options):
        return self._context


class _FakeValidated:
    hostname = "example.com"
    url = "https://example.com/"


@pytest.mark.asyncio
async def test_profile_context_close_failure_does_not_mask_success(monkeypatch):
    """`context.close()` "Failed to find context with id" ile patlasa bile basarili
    bir profil olcumu bozulmaz ve hata YUKARI SIZMAZ (tum endpoint 502 olmaz)."""

    monkeypatch.setattr(browser, "Axe", _FakeProfileAxe)
    close_error = browser.PlaywrightError(
        "Protocol error (Target.disposeBrowserContext): Failed to find context with id"
    )
    context = _FakeProfileContext(close_exc=close_error)
    fake_browser = _FakeProfileBrowser(context)
    profile = browser.DEVICE_NETWORK_PROFILES["desktop_broadband"]

    result = await browser._measure_device_network_profile(
        fake_browser, _FakeValidated(), "desktop_broadband", profile
    )

    assert result.succeeded is True
    assert result.profile_key == "desktop_broadband"
    assert result.timings is not None
    assert context.close_calls == 1


@pytest.mark.asyncio
async def test_profile_context_close_failure_does_not_mask_prior_error(monkeypatch):
    """Olcumun kendisi basarisiz olur VE ardindan `context.close()` de patlarsa,
    cleanup hatasi asil (typed) profil hatasini maskelememelidir."""

    monkeypatch.setattr(browser, "Axe", _FakeProfileAxe)
    goto_error = browser.PlaywrightError("net::ERR_CONNECTION_RESET")
    close_error = browser.PlaywrightError(
        "Protocol error (Target.disposeBrowserContext): Failed to find context with id"
    )
    context = _FakeProfileContext(close_exc=close_error, goto_exc=goto_error)
    fake_browser = _FakeProfileBrowser(context)
    profile = browser.DEVICE_NETWORK_PROFILES["desktop_broadband"]

    result = await browser._measure_device_network_profile(
        fake_browser, _FakeValidated(), "desktop_broadband", profile
    )

    assert result.succeeded is False
    assert "ERR_CONNECTION_RESET" in result.error
    assert context.close_calls == 1


@pytest.mark.asyncio
async def test_safe_close_swallows_target_not_found(caplog):
    class _Boom:
        async def close(self):
            raise browser.PlaywrightError("Failed to find context with id")

    # Hicbir sey firlatmamali; yalnizca warning loglanmali.
    await browser._safe_close(_Boom(), "context")
    await browser._safe_close(None, "context")  # idempotent: None sessizce yok sayilir


def test_build_snapshot_includes_analysis_mode_metadata():
    features = {
        "title": "Test",
        "headings": [],
        "text_stats": {
            "word_count": 3,
            "avg_sentence_word_count": 3.0,
            "visible_text_char_count": 10,
            "heading_count": 0,
        },
        "controls": {"link_count": 1, "button_count": 1, "form_count": 0, "form_field_count": 0},
        "element_boxes": [],
        "layout_regions": {},
        "performance": {
            "dom_content_loaded_ms": 10.0,
            "load_event_ms": 20.0,
            "first_contentful_paint_ms": 15.0,
            "total_navigation_ms": 20.0,
        },
        "contrast_candidates": [],
    }
    snapshot = browser._build_snapshot(
        url="https://example.com",
        final_url="https://example.com",
        redirect_count=0,
        features=features,
        screenshot_bytes=b"\x89PNG\r\n",
        axe_response={"scan_status": "completed", "violations": [], "passes": [], "incomplete": []},
        warnings=[],
        screenshot_width=1366,
        screenshot_height=900,
        analysis_mode="lite",
        analysis_limited=True,
    )
    assert snapshot.analysis_mode == "lite"
    assert snapshot.analysis_limited is True
    # Ekran goruntusu yuksekligi viewport ile uyumlu (tam-sayfa degil).
    assert snapshot.screenshot.height == 900
