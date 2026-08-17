"""arq worker canlilik (liveness) heartbeat'i - Redis/Valkey uzerinden.

Render ucretsiz planinda ayri bir Background Worker instance'i YOKTUR; API
(uvicorn) ve arq worker AYNI web container'i icinde birlikte calisir (bkz.
deploy/render_free_start.py). Bu modul, worker'in gercekten CANLI ve dongusunu
donduruyor oldugunu API tarafinin (`/api/health`) gorebilmesi icin paylasilan
Redis'e periyodik bir zaman damgasi yazar.

- `record_worker_heartbeat`: worker'in sik calisan bir cron'undan cagrilir
  (bkz. app.worker.ping_redis, saniye {0,30}). Best-effort'tur - Redis gecici
  erisilemezse sessizce yutulur (kalici durum kaynagi zaten PostgreSQL'dir).
- `read_worker_heartbeat_age`: API tarafinda okunur; en son heartbeat'in kac
  saniye once yazildigini (yoksa `None`) dondurur.

Ayri (compose.prod) topolojide bile worker AYNI Redis'e yazdigi icin API
tarafi heartbeat'i gorur; birlesik (render-free) topolojide de ayni anahtar
kullanilir. Anahtar hicbir gizli veri icermez (yalnizca bir zaman damgasi)."""

from __future__ import annotations

import time

from app.redis_client import redis_client

HEARTBEAT_KEY = "synthetix:worker:heartbeat"
# Worker tamamen olurse launcher zaten container'i dusurur (bkz.
# render_free_start.py); yine de anahtarin, "canliyken takildi" (wedged)
# durumunu tespit edebilmek icin makul bir sure yasamasi gerekir.
HEARTBEAT_TTL_SECONDS = 600
# ping_redis 30 saniyede bir yazar; 150s (5x) esigi gecici gecikmelere
# toleransli, gercek bir takilmayi ise tespit eder.
STALE_THRESHOLD_SECONDS = 150


async def record_worker_heartbeat() -> None:
    """Guncel zaman damgasini heartbeat anahtarina yazar (best-effort)."""

    try:
        await redis_client.set(HEARTBEAT_KEY, repr(time.time()), ex=HEARTBEAT_TTL_SECONDS)
    except Exception:
        # Kalici durum kaynagi PostgreSQL; Redis kesintisi worker dongusunu
        # bloklamamalidir (bkz. app.services.simulation_progress ayni desen).
        pass


async def read_worker_heartbeat_age() -> float | None:
    """En son heartbeat'in yasini (saniye) dondurur; anahtar yok/erisilemez/
    bozuksa `None`."""

    try:
        raw = await redis_client.get(HEARTBEAT_KEY)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, time.time() - float(raw))
    except (TypeError, ValueError):
        return None


__all__ = [
    "HEARTBEAT_KEY",
    "HEARTBEAT_TTL_SECONDS",
    "STALE_THRESHOLD_SECONDS",
    "record_worker_heartbeat",
    "read_worker_heartbeat_age",
]
