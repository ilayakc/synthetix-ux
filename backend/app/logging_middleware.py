"""HTTP istek/yanit loglama middleware'i.

Her istek icin (health-check gibi `settings.log_exclude_paths` icinde
belirtilen gurultulu yollar HARIC) tek bir INFO/WARNING satiri uretir:
metod, yol, durum kodu, sure. `slow_request_threshold_ms` asilirsa
seviye otomatik olarak WARNING'e yukselir. Ayrica her istege bir
`request_id` atar/yayar (bkz. app.logging_context) ve bunu `X-Request-ID`
yanit header'inda geri dondurur - boylece istemci taraflindan bildirilen
bir hata, bu ID uzerinden loglarda aranabilir.

Bu middleware, uvicorn'un kendi `uvicorn.access` logger'inin YERINE gecer
(bkz. app.logging_config.configure_logging - o logger burada susturulur);
boylece ayni istek icin iki farkli formatta cift log satiri uretilmez.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.config import Settings
from app.logging_context import request_context
from app.logging_utils import format_duration

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger("synthetix.api")


def install_request_logging(app: FastAPI, cfg: Settings) -> None:
    """`app`'e istek basina bir loglama middleware'i ekler."""

    excluded_paths = {p.strip() for p in cfg.log_exclude_paths.split(",") if p.strip()}
    slow_threshold_ms = cfg.slow_request_threshold_ms

    @app.middleware("http")
    async def _request_logging_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        method = request.method
        path = request.url.path
        skip_log = path in excluded_paths

        with request_context(request_id):
            start = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception:
                elapsed = time.perf_counter() - start
                logger.error(
                    "%s %s failed after %s",
                    method,
                    path,
                    format_duration(elapsed),
                    extra={
                        "category": "api",
                        "method": method,
                        "path": path,
                        "duration_ms": elapsed * 1000,
                    },
                    exc_info=True,
                )
                raise

            elapsed = time.perf_counter() - start
            response.headers[REQUEST_ID_HEADER] = request_id

            if not skip_log:
                duration_ms = elapsed * 1000
                duration_text = format_duration(elapsed)
                log_fields = {
                    "category": "api",
                    "method": method,
                    "path": path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                }
                if duration_ms > slow_threshold_ms:
                    logger.warning(
                        "%s %s -> %d took %s (slow, >%.2f s)",
                        method,
                        path,
                        response.status_code,
                        duration_text,
                        slow_threshold_ms / 1000,
                        extra=log_fields,
                    )
                else:
                    logger.info(
                        "%s %s -> %d took %s",
                        method,
                        path,
                        response.status_code,
                        duration_text,
                        extra=log_fields,
                    )

            return response


__all__ = ["REQUEST_ID_HEADER", "install_request_logging"]
