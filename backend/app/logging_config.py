"""Uygulama genelinde tek, tutarli log yapilandirmasi.

`backend/app/main.py` (API sureci) ve `backend/app/worker.py` (arq isci
sureci) - production'da fiilen calisan iki uzun-omurlu surec - bu modulu
kullanir. Onceden yalnizca `worker.py` kendi `logging.basicConfig(level=
logging.INFO)` cagrisini yapiyordu; `main.py` (uvicorn ile calistirilan API
sureci) HICBIR sekilde kok logger'i yapilandirmiyordu - bu, `app.services.*`
icindeki `logger.info(...)` cagrilarinin (worker disinda, dogrudan API
istegi isleyen kod yollarinda) kok logger'in varsayilan seviyesi (WARNING)
nedeniyle SESSIZCE dusurulmesine yol aciyordu.

Iki format desteklenir (`log_format`):
- "json": tek satirlik, makine tarafindan ayristirilabilir JSON (production
  varsayilani - bir log toplama/parse aracina beslenebilmesi icin).
- "pretty": `HH:MM:SS | SEVIYE   | kategori   | mesaj` seklinde, gelistirme
  ortaminda terminalde okunabilir, (tty ise) renkli duz metin.

Idempotenttir - ayni surecte (ornegin testlerde `app.main` VE `app.worker`
birlikte import edildiginde) birden fazla cagri kok logger'i yeniden
yapilandirmaz/handler coglaltmaz.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any

from app.logging_context import RequestIdLogFilter

_CONFIGURED_ATTR = "_synthetix_logging_configured"

# Uvicorn kendi `uvicorn.access` logger'i uzerinden HER istek icin ayrica
# bir satir uretir (ornegin `INFO: 127.0.0.1 - "GET /api/health HTTP/1.1"
# 200 OK`); `app.main`'deki istek loglama middleware'i AYNI bilgiyi zaten
# yapilandirilmis formatta (JSON/pretty, request_id ile) urettigi icin bu
# ikinci, farkli formatli satir yalnizca gurultu/cift kayittir - burada
# susturulur (`app.services.*` gibi diger `uvicorn.*` olmayan logger'lar
# ETKILENMEZ).
_DUPLICATE_ACCESS_LOGGERS = ("uvicorn.access",)

# Alan adi hassas verileri isaret ediyorsa (bkz. app.logging_utils icindeki
# ayni liste) `extra={...}` ile gelen deger dahi JSON/pretty ciktida asla
# ham haliyle gorunmemelidir; bu iki modul kasitli olarak ayni belirteclere
# sahiptir.
_SENSITIVE_EXTRA_MARKERS = (
    "password",
    "token",
    "authorization",
    "cookie",
    "secret",
    "api_key",
    "apikey",
)

# `logging.LogRecord`'in kendi standart alanlari - `record.__dict__`
# uzerinde bunlarin DISINDA kalanlar, `logger.info(..., extra={...})` ile
# eklenmis ozel (structured) alanlardir.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("x", 0, "x", 0, "x", (), None).__dict__) | {
    "message",
    "asctime",
}


def _is_sensitive_field(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_EXTRA_MARKERS)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """`extra={...}` ile eklenmis ozel alanlari (hassaslari maskeleyerek) dondurur."""

    fields: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
            continue
        fields[key] = "***" if _is_sensitive_field(key) else value
    return fields


def _derive_category(record: logging.LogRecord) -> str:
    """`extra={"category": ...}` verilmemisse logger adindan kisa bir kategori turetir."""

    category = getattr(record, "category", None)
    if category:
        return str(category)
    name = record.name
    if name.startswith("uvicorn"):
        return "uvicorn"
    return name.rsplit(".", 1)[-1]


class JsonLogFormatter(logging.Formatter):
    """Bir log kaydini tek satirlik, makine tarafindan ayristirilabilir bir
    JSON nesnesine cevirir. Ham exception stack trace'i (varsa)
    `exception` alaninda duz metin olarak tasinir - ayrica bir yapiya
    ayristirilmaz (log toplama aracinin kendi ayristirmasina birakilir)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "category": _derive_category(record),
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


# ANSI renk kodlari - yalnizca `use_color=True` iken (bkz. `configure_logging`,
# `sys.stdout.isatty()` kontrolu) uygulanir; boylece dosyaya/pipe'a
# yonlendirilen ciktida kacis (escape) karakterleri kirletmez.
_LEVEL_COLORS = {
    logging.DEBUG: "\x1b[90m",  # gri
    logging.INFO: "\x1b[32m",  # yesil
    logging.WARNING: "\x1b[33m",  # turuncu/sari
    logging.ERROR: "\x1b[31m",  # kirmizi
    logging.CRITICAL: "\x1b[97;41m",  # beyaz-uzerine-kirmizi arka plan
}
_COLOR_RESET = "\x1b[0m"

# Ornek formatla ayni hizalama: `CRITICAL` (8 karakter) en uzun seviye adi.
_LEVEL_WIDTH = 8
_CATEGORY_WIDTH = 10

_PRETTY_REDUNDANT_EXTRA_KEYS = frozenset(
    {"duration_ms", "method", "path", "status_code", "request_id", "category"}
)


class PrettyLogFormatter(logging.Formatter):
    """`HH:MM:SS | SEVIYE   | kategori   | mesaj` bicimli, okunabilir duz metin.

    `use_color=True` ise (gelistirme ortaminda, tty algilanirsa) tum satir
    log seviyesine gore renklendirilir.
    """

    def __init__(self, *, use_color: bool = False) -> None:
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        time_str = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        category = _derive_category(record)
        message = record.getMessage()
        extra = _extra_fields(record)
        if extra:
            # `duration_ms`/`method`/`path`/`status_code`/`request_id` mesaj
            # metninde zaten insan-okunabilir sekilde yer alir (ornegin
            # "POST /api/x -> 200 took 413.0 ms"); burada TEKRAR
            # gosterilirse gurultu olur - yalnizca bunlarin DISINDAKI ek
            # alanlar (ornegin `model_name`, `attempt`) parantez icinde eklenir.
            extra_str = " ".join(
                f"{k}={v}" for k, v in extra.items() if k not in _PRETTY_REDUNDANT_EXTRA_KEYS
            )
            if extra_str:
                message = f"{message} ({extra_str})"

        line = f"{time_str} | {record.levelname:<{_LEVEL_WIDTH}} | {category:<{_CATEGORY_WIDTH}} | {message}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"

        if self.use_color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            if color:
                return f"{color}{line}{_COLOR_RESET}"
        return line


def configure_logging(
    environment: str,
    *,
    logger: logging.Logger | None = None,
    log_level: str | int = "INFO",
    log_format: str | None = None,
) -> None:
    """Verilen logger'a (varsayilan: kok logger) tek bir `StreamHandler`
    (stdout) baglar.

    `log_format`: "json" | "pretty" | None. `None` iken `environment`'a gore
    otomatik secilir (production -> json, aksi halde pretty) - onceki
    davranisla geriye donuk uyumludur. Zaten yapilandirilmissa (bu logger
    icin daha once cagrilmissa) hicbir sey yapmaz - boylece `app.main` VE
    `app.worker` ayni surecte (ornegin testlerde) import edilse bile handler
    coklanmaz/mukerrer log satiri uretilmez. `logger` parametresi yalnizca
    testler icindir (gercek kok logger'i kirletmeden davranisi dogrulamak
    icin); uygulama kodu bunu HICBIR ZAMAN gecmemelidir.
    """

    target = logger if logger is not None else logging.getLogger()
    if getattr(target, _CONFIGURED_ATTR, False):
        return

    resolved_format = log_format or ("json" if environment == "production" else "pretty")

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdLogFilter())
    if resolved_format == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        use_color = environment != "production" and sys.stdout.isatty()
        handler.setFormatter(PrettyLogFormatter(use_color=use_color))

    target.setLevel(log_level)
    target.addHandler(handler)
    setattr(target, _CONFIGURED_ATTR, True)

    if target is logging.getLogger():
        for noisy_name in _DUPLICATE_ACCESS_LOGGERS:
            noisy_logger = logging.getLogger(noisy_name)
            noisy_logger.handlers.clear()
            noisy_logger.propagate = False


class CategoryLoggerAdapter(logging.LoggerAdapter):
    """Her cagriya otomatik olarak `extra={"category": ...}` ekleyen adapter.

    `get_logger("auth")` ile alinan bir logger'da `logger.info("verify
    token")` yazmak yeterlidir - kategori her satirda tekrar belirtilmez.
    """

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("category", self.extra["category"] if self.extra else None)
        return msg, kwargs


def get_logger(category: str, *, logger_name: str | None = None) -> CategoryLoggerAdapter:
    """Loglarina otomatik olarak `category` alani eklenen bir logger dondurur."""

    base = logging.getLogger(logger_name or f"synthetix.{category}")
    return CategoryLoggerAdapter(base, {"category": category})


__all__ = [
    "CategoryLoggerAdapter",
    "JsonLogFormatter",
    "PrettyLogFormatter",
    "configure_logging",
    "get_logger",
]
