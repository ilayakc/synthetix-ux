"""Istek bazli (request-scoped) baglam: correlation/request ID.

`contextvars` kullanilir, boylece asyncio taskleri arasinda (FastAPI'nin
her istegi kendi task'inda isledigi durumda) sizinti olmadan, thread-safe
bir sekilde "hangi log satiri hangi HTTP istegine ait" bilgisi tasinabilir.
Istek tamamlandiginda `request_context` context manager'i degeri MUTLAKA
eski haline (`None`) dondurur - bir sonraki isteğin (ayni worker/thread
uzerinde calisabilecek) yanlislikla onceki isteğin ID'sini miras almasini
engeller.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return _request_id_var.get()


@contextmanager
def request_context(request_id: str) -> Iterator[None]:
    """`request_id`'yi bu context ve alt-cagrilari icin baglar; cikista temizler."""

    token = _request_id_var.set(request_id)
    try:
        yield
    finally:
        _request_id_var.reset(token)


class RequestIdLogFilter(logging.Filter):
    """Her log kaydina, o an aktif olan `request_id`'yi (varsa) ekler.

    Bir istek disinda uretilen loglarda (ornegin worker gorevleri, uygulama
    baslangici) `record.request_id` `None` olur - formatlayicilar bunu
    JSON'da `null`, duz metinde ise sessizce yok sayar.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


__all__ = ["RequestIdLogFilter", "get_request_id", "request_context"]
