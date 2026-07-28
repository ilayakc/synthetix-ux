"""bkz. backend/tests/test_logging_config.py - ayni davranisin analyzer
kopyasi icin ayni testler."""

import json
import logging

from app.logging_config import JsonLogFormatter, configure_logging


def _make_record(message: str = "merhaba") -> logging.LogRecord:
    return logging.LogRecord(
        name="analyzer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_produces_valid_single_line_json():
    formatter = JsonLogFormatter()
    output = formatter.format(_make_record("islem basarili"))

    assert "\n" not in output
    payload = json.loads(output)
    assert payload["message"] == "islem basarili"
    assert payload["logger"] == "analyzer.test"


def test_configure_logging_uses_json_formatter_in_production():
    logger = logging.Logger(f"test-{id(object())}")
    configure_logging("production", logger=logger)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)


def test_configure_logging_is_idempotent():
    logger = logging.Logger(f"test-{id(object())}")
    configure_logging("production", logger=logger)
    configure_logging("development", logger=logger)

    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, JsonLogFormatter)
