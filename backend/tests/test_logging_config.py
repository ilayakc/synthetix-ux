"""`app.logging_config` icin testler: JSON formatlayici seklinin dogrulugu,
ortama gore format secimi, idempotentlik (handler coklanmamasi).

Gercek kok logger'i KIRLETMEMEK icin her testte taze bir `logging.Logger`
ornegi (`logger=` parametresi) kullanilir - bkz. `configure_logging`
dokstring'i.
"""

import json
import logging

import pytest

from app.logging_config import JsonLogFormatter, PrettyLogFormatter, configure_logging, get_logger
from app.logging_context import request_context

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


def test_configure_logging_log_format_overrides_environment_default():
    logger = logging.Logger(f"test-{id(object())}")
    # `environment="production"` normalde JSON secerdi; acik `log_format`
    # bunu gecersiz kilar.
    configure_logging("production", logger=logger, log_format="pretty")

    assert isinstance(logger.handlers[0].formatter, PrettyLogFormatter)


def test_json_formatter_includes_derived_category_and_request_id():
    formatter = JsonLogFormatter()
    record = _make_record("islem basarili")
    record.name = "app.services.ai_explanation"

    with request_context("req-json-1"):
        # `RequestIdLogFilter` normalde bunu bir handler zincirinde yapar;
        # burada dogrudan formatlayiciyi test etmek icin manuel simule edilir.
        from app.logging_context import RequestIdLogFilter

        RequestIdLogFilter().filter(record)
        payload = json.loads(formatter.format(record))

    assert payload["category"] == "ai_explanation"
    assert payload["request_id"] == "req-json-1"


def test_json_formatter_masks_sensitive_extra_fields():
    formatter = JsonLogFormatter()
    record = _make_record("giris denemesi")
    record.password = "hunter2"  # type: ignore[attr-defined]
    record.duration_ms = 12.3  # type: ignore[attr-defined]

    payload = json.loads(formatter.format(record))

    assert payload["password"] == "***"
    assert payload["duration_ms"] == 12.3


def test_pretty_formatter_matches_expected_layout():
    formatter = PrettyLogFormatter(use_color=False)
    record = _make_record("verify token took 2.3 ms")
    record.category = "auth"  # type: ignore[attr-defined]

    line = formatter.format(record)

    # `SEVIYE` 8, `kategori` 10 karaktere hizalanir (bkz. PrettyLogFormatter).
    expected_tail = f"{'INFO':<8} | {'auth':<10} | verify token took 2.3 ms"
    assert line.endswith(expected_tail)
    # Basinda `HH:MM:SS` bicimli bir zaman damgasi olmali.
    assert line[:8].count(":") == 2


def test_pretty_formatter_applies_color_when_enabled():
    formatter = PrettyLogFormatter(use_color=True)
    record = _make_record("hata olustu")
    record.levelno = logging.ERROR
    record.levelname = "ERROR"

    line = formatter.format(record)

    assert line.startswith("\x1b[31m")
    assert line.endswith("\x1b[0m")


def test_get_logger_injects_category_into_every_call(caplog):
    logger = get_logger("auth", logger_name="test.get_logger.auth")
    with caplog.at_level(logging.INFO, logger="test.get_logger.auth"):
        logger.info("verify token took 2.3 ms")

    assert caplog.records[-1].category == "auth"  # type: ignore[attr-defined]
