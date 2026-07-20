"""Redis uzerinden gecici (ephemeral) simulasyon ilerleme yayini.

Kalici sonucun (ve kalici durumun) tek kaynagi PostgreSQL'dir
(`SimulationRun.status`/`progress_percent`/`progress_message`); bu modul
yalnizca worker calisirken daha sik/dusuk maliyetli okunabilecek gecici bir
"su anki ilerleme" anlik goruntusunu Redis'te tutar. Redis anahtari bir TTL
ile birlikte yazilir; TTL dolduysa veya Redis erisilemezse okuyucu taraf
(bkz. `app.routers.simulations`) DB'deki kalici degerlere geri doner.
"""

from __future__ import annotations

import json
import uuid

from app.redis_client import redis_client

PROGRESS_KEY_PREFIX = "synthetix:sim:progress:"
PROGRESS_TTL_SECONDS = 300


def _key(run_id: uuid.UUID) -> str:
    return f"{PROGRESS_KEY_PREFIX}{run_id}"


async def publish_progress(run_id: uuid.UUID, *, status: str, percent: int, message: str | None) -> None:
    """Gecici ilerleme anlik goruntusunu Redis'e yazar (best-effort).

    Redis gecici olarak erisilemezse (baglanti hatasi) sessizce yutulur;
    ilerleme bilgisinin kalici kaynagi zaten PostgreSQL'dir, bu yuzden bir
    Redis kesintisi is akisini durdurmamalidir.
    """

    payload = json.dumps({"status": status, "percent": percent, "message": message})
    try:
        await redis_client.set(_key(run_id), payload, ex=PROGRESS_TTL_SECONDS)
    except Exception:
        pass


async def read_progress(run_id: uuid.UUID) -> dict | None:
    """Redis'teki gecici ilerleme anlik goruntusunu okur; yoksa/erisilemezse `None`."""

    try:
        raw = await redis_client.get(_key(run_id))
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


async def clear_progress(run_id: uuid.UUID) -> None:
    try:
        await redis_client.delete(_key(run_id))
    except Exception:
        pass


__all__ = ["publish_progress", "read_progress", "clear_progress"]
