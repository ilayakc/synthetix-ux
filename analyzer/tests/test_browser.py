import asyncio

import pytest

from app import browser


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
