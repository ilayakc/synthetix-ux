"""`app.logging_utils` icin testler: sure formatlama, `log_duration`/`timed`
davranisi (basarili/yavas/hatali), hassas veri maskeleme."""

import logging

import pytest

from app.logging_utils import format_duration, log_duration, mask_sensitive, timed

pytestmark = pytest.mark.unit


def test_format_duration_uses_milliseconds_under_one_second():
    assert format_duration(0.0023) == "2.3 ms"


def test_format_duration_uses_seconds_at_or_above_one_second():
    assert format_duration(1.35) == "1.35 s"


def test_mask_sensitive_masks_known_sensitive_keys():
    data = {"password": "hunter2", "email": "a@b.com"}
    masked = mask_sensitive(data)
    assert masked["password"] == "***"
    assert masked["email"] == "a@b.com"


def test_mask_sensitive_masks_recursively_and_case_insensitively():
    data = {"Authorization": "Bearer xyz", "nested": {"api_key": "abc", "note": "ok"}}
    masked = mask_sensitive(data)
    assert masked["Authorization"] == "***"
    assert masked["nested"]["api_key"] == "***"
    assert masked["nested"]["note"] == "ok"


def test_log_duration_logs_info_for_fast_operation(caplog):
    logger = logging.getLogger("test.logging_utils.fast")
    with caplog.at_level(logging.INFO, logger=logger.name):
        with log_duration(logger, "test", "op", slow_threshold_ms=10_000):
            pass

    record = caplog.records[-1]
    assert record.levelname == "INFO"
    assert "took" in record.getMessage()


def test_log_duration_logs_warning_when_over_threshold(caplog):
    logger = logging.getLogger("test.logging_utils.slow")
    with caplog.at_level(logging.INFO, logger=logger.name):
        with log_duration(logger, "test", "op", slow_threshold_ms=-1):
            pass

    record = caplog.records[-1]
    assert record.levelname == "WARNING"
    assert "slow" in record.getMessage()


def test_log_duration_logs_error_and_reraises_on_exception(caplog):
    logger = logging.getLogger("test.logging_utils.err")
    with caplog.at_level(logging.INFO, logger=logger.name), pytest.raises(ValueError, match="boom"):
        with log_duration(logger, "test", "op"):
            raise ValueError("boom")

    record = caplog.records[-1]
    assert record.levelname == "ERROR"
    assert record.exc_info is not None


async def test_timed_decorator_measures_async_function(caplog):
    logger = logging.getLogger("test.logging_utils.timed")

    @timed(logger, "test", name="my_op")
    async def do_work() -> int:
        return 42

    with caplog.at_level(logging.INFO, logger=logger.name):
        result = await do_work()

    assert result == 42
    assert "my_op took" in caplog.records[-1].getMessage()
