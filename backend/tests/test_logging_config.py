"""`app.logging_config` icin testler: JSON formatlayici seklinin dogrulugu,
ortama gore format secimi, idempotentlik (handler coklanmamasi).

Gercek kok logger'i KIRLETMEMEK icin her testte taze bir `logging.Logger`
ornegi (`logger=` parametresi) kullanilir - bkz. `configure_logging`
dokstring'i.
"""

import json
import logging

import pytest

from app.logging_config import JsonLogFormatter, configure_logging

pytestmark = pytest.mark.unit


def _make_record(message: str = "merhaba", *, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="synthetix.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=exc_info,
    )


def test_json_formatter_produces_valid_single_line_json():
    formatter = JsonLogFormatter()
    output = formatter.format(_make_record("islem basarili"))

    assert "\n" not in output
    payload = json.loads(output)
    assert payload["message"] == "islem basarili"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "synthetix.test"
    assert "timestamp" in payload


def test_json_formatter_includes_exception_when_present():
    try:
        raise ValueError("beklenen hata")
    except ValueError:
        import sys

        record = _make_record("hata olustu", exc_info=sys.exc_info())

    formatter = JsonLogFormatter()
    payload = json.loads(formatter.format(record))
    assert "ValueError" in payload["exception"]
    assert "beklenen hata" in payload["exception"]


def test_configure_logging_uses_json_formatter_in_production():
    logger = logging.Logger(f"test-{id(object())}")
    configure_logging("production", logger=logger)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)


def test_configure_logging_uses_plain_text_formatter_outside_production():
    logger = logging.Logger(f"test-{id(object())}")
    configure_logging("development", logger=logger)

    assert len(logger.handlers) == 1
    assert not isinstance(logger.handlers[0].formatter, JsonLogFormatter)


def test_configure_logging_is_idempotent():
    logger = logging.Logger(f"test-{id(object())}")
    configure_logging("production", logger=logger)
    configure_logging("production", logger=logger)
    configure_logging("development", logger=logger)

    # Ikinci/ucuncu cagri hicbir sey yapmamali - handler coklanmamali ve
    # ilk cagrinin formatlayicisi (JSON) degismemeli.
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)
