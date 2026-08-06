"""Yeniden kullanilabilir loglama yardimcilari: sure olcumu ve hassas veri maskeleme.

Her is fonksiyonuna elle "basladi/bitti" logu yazmak yerine `log_duration`
(context manager) veya `timed` (decorator) kullanilir; ikisi de ayni
formati ("<mesaj> took <sure>") ve yavaslik esigini (`slow_threshold_ms`)
paylasir. Loglama HICBIR ZAMAN sarilan islemin basarisini etkilemez -
`log_duration` bir istisnayi yutmaz, yalniz suresini ERROR seviyesinde
kaydedip yeniden yukseltir (re-raise).
"""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

_T = TypeVar("_T")

# `log_duration`/`timed`, `logging.Logger`'in yaninda `app.logging_config.
# get_logger(...)`'in dondurdugu `CategoryLoggerAdapter`'i da kabul eder -
# ikisi de ayni `.info`/`.warning`/`.error(..., exc_info=...)` arayuzune sahiptir.
LoggerLike = logging.Logger | logging.LoggerAdapter

# Log ciktisinda/hata mesajlarinda ASLA gorunmemesi gereken alan adlari
# (case-insensitive, kismi eslesme). `mask_sensitive_dict` bu anahtarlarin
# degerini maskeler; iceriklerinden herhangi biri log mesajina KATIL
# edilmeden once mutlaka bu fonksiyondan gecirilmelidir.
_SENSITIVE_KEY_MARKERS = (
    "password",
    "parola",
    "token",
    "authorization",
    "cookie",
    "secret",
    "api_key",
    "apikey",
    "access_key",
)

_MASK = "***"


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def mask_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Hassas alan adlarina sahip degerleri maskeleyen SIG (shallow) bir kopya dondurur.

    Ic ice sozlukler recursive olarak islenir; listeler/diger tipler
    oldugu gibi birakilir (bu yardimci genel amacli bir "derin temizleme"
    degil, log/hata mesajlarina eklenmeden once son bir guvenlik agi'dir).
    """

    result: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_key(key):
            result[key] = _MASK
        elif isinstance(value, dict):
            result[key] = mask_sensitive(value)
        else:
            result[key] = value
    return result


def format_duration(seconds: float) -> str:
    """`2.3 ms` / `1.35 s` gibi okunabilir bir sure metni uretir.

    1 saniyenin altindaki sureler milisaniye, ustundekiler saniye olarak
    gosterilir - ornekteki log formatiyla (bkz. proje talimati) birebir
    ayni esik.
    """

    if seconds < 1:
        return f"{seconds * 1000:.1f} ms"
    return f"{seconds:.2f} s"


@contextmanager
def log_duration(
    logger: LoggerLike,
    category: str,
    message: str,
    *,
    slow_threshold_ms: float | None = None,
) -> Iterator[None]:
    """`message`'in calisma suresini olcup INFO (veya esik asilirsa WARNING) loglar.

    Istisna olustugunda sure ERROR seviyesinde kaydedilir ve istisna
    OLDUGU GIBI yeniden yukseltilir - bu yardimci hicbir zaman hatayi
    yutmaz/sonucu degistirmez.
    """

    start = time.perf_counter()
    try:
        yield
    except Exception:
        elapsed = time.perf_counter() - start
        logger.error(
            "%s failed after %s",
            message,
            format_duration(elapsed),
            extra={"category": category, "duration_ms": elapsed * 1000},
            exc_info=True,
        )
        raise
    else:
        elapsed = time.perf_counter() - start
        duration_text = format_duration(elapsed)
        if slow_threshold_ms is not None and elapsed * 1000 > slow_threshold_ms:
            logger.warning(
                "%s took %s (slow, >%.2f s)",
                message,
                duration_text,
                slow_threshold_ms / 1000,
                extra={"category": category, "duration_ms": elapsed * 1000},
            )
        else:
            logger.info(
                "%s took %s",
                message,
                duration_text,
                extra={"category": category, "duration_ms": elapsed * 1000},
            )


def timed(
    logger: LoggerLike,
    category: str,
    *,
    name: str | None = None,
    slow_threshold_ms: float | None = None,
) -> Callable[[Callable[..., Awaitable[_T]]], Callable[..., Awaitable[_T]]]:
    """`log_duration`'in async fonksiyonlar icin decorator hali."""

    def decorator(func: Callable[..., Awaitable[_T]]) -> Callable[..., Awaitable[_T]]:
        label = name or func.__name__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> _T:
            with log_duration(logger, category, label, slow_threshold_ms=slow_threshold_ms):
                return await func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = ["format_duration", "log_duration", "mask_sensitive", "timed"]
