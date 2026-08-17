"""Worker canlilik/kayit testleri: kritik simulasyon gorevlerinin kayitli
oldugu, heartbeat'in yazilip okundugu ve `/api/health`in worker canliligini
raporladigi dogrulanir (Render ucretsiz: API + worker AYNI container).
"""

from __future__ import annotations

import time

import anyio
import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient

import app.worker_heartbeat as hb
from app.config import settings

pytestmark = pytest.mark.integration


# --- Kritik gorev kaydi (madde 8) --------------------------------------------


def test_critical_simulation_tasks_are_registered_and_scheduled():
    from app.worker import WorkerSettings

    fn_names = {f.__name__ for f in WorkerSettings.functions}
    assert {"process_queued_simulations", "reap_stale_simulations"} <= fn_names

    cron_coros = {getattr(c, "coroutine", None) for c in WorkerSettings.cron_jobs}
    cron_names = {getattr(fn, "__name__", "") for fn in cron_coros if fn is not None}
    assert {"process_queued_simulations", "reap_stale_simulations"} <= cron_names


def test_assert_critical_tasks_registered_passes_by_default():
    from app.worker import _assert_critical_tasks_registered

    _assert_critical_tasks_registered()  # firlatmamali


def test_assert_critical_tasks_registered_raises_when_missing(monkeypatch):
    from app import worker as worker_module

    # Simulasyon islemcisi kaldirilirsa fail-fast (sessizce "consumer yok" olmaz).
    monkeypatch.setattr(worker_module.WorkerSettings, "functions", [worker_module.ping_redis])
    with pytest.raises(RuntimeError):
        worker_module._assert_critical_tasks_registered()


# --- Heartbeat yaz/oku (madde 6/14) - sahte redis ile (loop-guvenli) ---------


async def test_record_worker_heartbeat_writes_key_with_ttl(monkeypatch):
    writes: dict[str, tuple[str, int | None]] = {}

    class FakeRedis:
        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            writes[key] = (value, ex)

    monkeypatch.setattr(hb, "redis_client", FakeRedis())
    await hb.record_worker_heartbeat()

    assert hb.HEARTBEAT_KEY in writes
    assert writes[hb.HEARTBEAT_KEY][1] == hb.HEARTBEAT_TTL_SECONDS
    assert float(writes[hb.HEARTBEAT_KEY][0]) <= time.time()


async def test_read_worker_heartbeat_age_returns_recent_age(monkeypatch):
    class FakeRedis:
        async def get(self, key: str) -> str:
            return repr(time.time() - 5)

    monkeypatch.setattr(hb, "redis_client", FakeRedis())
    age = await hb.read_worker_heartbeat_age()
    assert age is not None and 4 <= age < 30


async def test_read_worker_heartbeat_age_none_when_absent(monkeypatch):
    class FakeRedis:
        async def get(self, key: str) -> None:
            return None

    monkeypatch.setattr(hb, "redis_client", FakeRedis())
    assert await hb.read_worker_heartbeat_age() is None


# --- /api/health worker canliligi (madde 6) - gercek redis uzerinden ---------


async def _set_heartbeat(value: str) -> None:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.set(hb.HEARTBEAT_KEY, value)
    finally:
        await client.aclose()


async def _clear_heartbeat() -> None:
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.delete(hb.HEARTBEAT_KEY)
    finally:
        await client.aclose()


def test_health_reports_worker_ok_when_heartbeat_fresh(client: TestClient):
    try:
        anyio.run(_set_heartbeat, repr(time.time()))
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["worker"] == "ok"
    finally:
        anyio.run(_clear_heartbeat)


def test_health_returns_503_when_worker_heartbeat_stale(client: TestClient):
    try:
        # STALE_THRESHOLD'un cok otesinde bir zaman damgasi -> worker takilmis.
        anyio.run(_set_heartbeat, repr(time.time() - (hb.STALE_THRESHOLD_SECONDS + 10_000)))
        response = client.get("/api/health")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["worker"] == "stale"
    finally:
        anyio.run(_clear_heartbeat)


def test_health_worker_unknown_when_no_heartbeat_does_not_fail_liveness(client: TestClient):
    anyio.run(_clear_heartbeat)
    response = client.get("/api/health")
    assert response.status_code == 200  # taze baslangicta yanlis-pozitif restart olmaz
    assert response.json()["worker"] == "unknown"
