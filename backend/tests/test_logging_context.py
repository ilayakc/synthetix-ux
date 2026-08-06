"""`app.logging_context` icin testler: request_id baglaminin dogru
baglanip/temizlendigini ve filtrenin bunu bir LogRecord'a enjekte ettigini
dogrular."""

import logging

import pytest

from app.logging_context import RequestIdLogFilter, get_request_id, request_context

pytestmark = pytest.mark.unit


def _make_record() -> logging.LogRecord:
    return logging.LogRecord("synthetix.test", logging.INFO, __file__, 1, "msg", (), None)


def test_request_context_binds_and_resets_id():
    assert get_request_id() is None
    with request_context("req-abc"):
        assert get_request_id() == "req-abc"
    assert get_request_id() is None


def test_request_context_resets_even_on_exception():
    with pytest.raises(ValueError):
        with request_context("req-err"):
            raise ValueError("boom")
    assert get_request_id() is None


def test_request_id_log_filter_injects_active_request_id():
    record = _make_record()
    log_filter = RequestIdLogFilter()

    with request_context("req-xyz"):
        assert log_filter.filter(record) is True

    assert record.request_id == "req-xyz"  # type: ignore[attr-defined]


def test_request_id_log_filter_sets_none_outside_context():
    record = _make_record()
    log_filter = RequestIdLogFilter()

    log_filter.filter(record)

    assert record.request_id is None  # type: ignore[attr-defined]
