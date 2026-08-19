"""Container bellek koruma (admission + watchdog) birim testleri.

Gercek Chromium/cgroup GEREKTIRMEZ: cgroup okuyuculari ve browser sahte
(fake) nesnelerle degistirilir. Amac, agir bir sayfanin TUM container'i
OOM-kill ettirmesini onleyen iki katmanli korumanin (Chromium acilmadan
ADMISSION reddi + calisma-ani WATCHDOG iptali) davranisini kanitlamaktir.
"""

import asyncio

import pytest

from app import browser


class _FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def test_admission_rejects_when_free_below_threshold(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_enabled", True)
    monkeypatch.setattr(browser.settings, "memory_admission_min_free_mb", 110)
    monkeypatch.setattr(browser, "_effective_limit_bytes", lambda: 512 * 1024 * 1024)
    # 450MB kullanim -> 62MB bos < 110MB esik -> reddedilmeli.
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: 450 * 1024 * 1024)

    with pytest.raises(browser.AnalysisError) as exc:
        browser._enforce_memory_admission()
    assert exc.value.code == browser.ANALYZER_MEMORY_PRESSURE_CODE


def test_admission_allows_when_enough_free(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_enabled", True)
    monkeypatch.setattr(browser.settings, "memory_admission_min_free_mb", 110)
    monkeypatch.setattr(browser, "_effective_limit_bytes", lambda: 512 * 1024 * 1024)
    # 300MB kullanim -> 212MB bos >= 110MB -> gecmeli (istisna yok).
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: 300 * 1024 * 1024)
    browser._enforce_memory_admission()  # raise etmemeli


def test_admission_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_enabled", False)
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: 511 * 1024 * 1024)
    browser._enforce_memory_admission()  # kapali -> hicbir sey yapmaz


def test_admission_noop_when_cgroup_unreadable(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_enabled", True)
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: None)
    browser._enforce_memory_admission()  # cgroup okunamiyor -> guard'i bypass etmez


@pytest.mark.asyncio
async def test_watchdog_trips_and_closes_browser(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_trip_pct", 0.88)
    monkeypatch.setattr(browser.settings, "memory_guard_poll_ms", 5)
    monkeypatch.setattr(browser, "_effective_limit_bytes", lambda: 512 * 1024 * 1024)
    # trip esigi = 512*0.88 = ~450MB; 500MB kullanim -> tripler.
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: 500 * 1024 * 1024)

    fake = _FakeBrowser()
    state: dict[str, bool] = {"tripped": False}
    await asyncio.wait_for(browser._memory_watchdog(fake, state), timeout=2)
    assert state["tripped"] is True
    assert fake.closed is True


@pytest.mark.asyncio
async def test_watchdog_does_not_trip_below_threshold(monkeypatch):
    monkeypatch.setattr(browser.settings, "memory_guard_trip_pct", 0.88)
    monkeypatch.setattr(browser.settings, "memory_guard_poll_ms", 5)
    monkeypatch.setattr(browser, "_effective_limit_bytes", lambda: 512 * 1024 * 1024)
    monkeypatch.setattr(browser, "_cgroup_working_set_bytes", lambda: 300 * 1024 * 1024)

    fake = _FakeBrowser()
    state: dict[str, bool] = {"tripped": False}
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(browser._memory_watchdog(fake, state), timeout=0.2)
    assert state["tripped"] is False
    assert fake.closed is False


def test_cgroup_limit_ignores_v1_unlimited_sentinel(monkeypatch, tmp_path):
    # cgroup v1 sinirsizi ~2^63 sentinelle temsil eder -> None donmeli.
    huge = tmp_path / "limit"
    huge.write_text(str(1 << 63))
    monkeypatch.setattr(
        browser,
        "_read_cgroup_int",
        lambda p: (1 << 63) if p.endswith("limit_in_bytes") or p.endswith("memory.max") else None,
    )
    assert browser._cgroup_limit_bytes() is None


def test_effective_limit_falls_back_to_setting(monkeypatch):
    monkeypatch.setattr(browser, "_cgroup_limit_bytes", lambda: None)
    monkeypatch.setattr(browser.settings, "container_memory_limit_mb", 512)
    assert browser._effective_limit_bytes() == 512 * 1024 * 1024
