"""Analyzer icin `backend/app/logging_config.py` ile ayni desen/davranis.

Backend ve analyzer ayri, bagimsiz deploy edilen servisler oldugu icin kod
paylasilmaz (ayri container/bagimlilik agaci); bu, kasitli, kucuk bir
kopyadir - iki servis de tek satirlik JSON (production) / duz metin
(development) log ciktisi uretir.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_CONFIGURED_ATTR = "_synthetix_logging_configured"


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_PLAIN_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(environment: str, *, logger: logging.Logger | None = None) -> None:
    """bkz. backend/app/logging_config.py::configure_logging (ayni davranis)."""

    target = logger if logger is not None else logging.getLogger()
    if getattr(target, _CONFIGURED_ATTR, False):
        return

    handler = logging.StreamHandler(sys.stdout)
    if environment == "production":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(_PLAIN_TEXT_FORMAT))

    target.setLevel(logging.INFO)
    target.addHandler(handler)
    setattr(target, _CONFIGURED_ATTR, True)


__all__ = ["JsonLogFormatter", "configure_logging"]
