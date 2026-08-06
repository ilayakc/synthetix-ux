"""Istek loglama middleware'i icin testler (bkz. app.logging_middleware):
`X-Request-ID` uretimi/yayilimi, gurultulu yol filtresi, yavaslik esigi."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.logging_middleware import REQUEST_ID_HEADER
from app.main import create_app

pytestmark = pytest.mark.unit


def _make_settings(**overrides) -> Settings:
    base = {"environment": "development"}
    base.update(overrides)
    return Settings(**base)


def test_request_id_from_header_is_echoed_back():
    app = create_app(_make_settings())
    with TestClient(app) as client:
        response = client.get("/api/health", headers={REQUEST_ID_HEADER: "req-fixed"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "req-fixed"


def test_request_id_is_generated_when_absent():
    app = create_app(_make_settings())
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.headers[REQUEST_ID_HEADER]


def test_excluded_path_produces_no_api_log(caplog):
    app = create_app(_make_settings(log_exclude_paths="/api/health"))
    with caplog.at_level(logging.INFO, logger="synthetix.api"):
        with TestClient(app) as client:
            client.get("/api/health")

    assert not any(record.name == "synthetix.api" for record in caplog.records)


def test_non_excluded_path_logs_method_path_status_and_duration(caplog):
    app = create_app(_make_settings(log_exclude_paths=""))
    with caplog.at_level(logging.INFO, logger="synthetix.api"):
        with TestClient(app) as client:
            client.get("/api/health")

    api_records = [r for r in caplog.records if r.name == "synthetix.api"]
    assert len(api_records) == 1
    record = api_records[0]
    assert record.levelname == "INFO"
    assert record.method == "GET"  # type: ignore[attr-defined]
    assert record.path == "/api/health"  # type: ignore[attr-defined]
    assert record.status_code == 200  # type: ignore[attr-defined]


def test_slow_request_is_logged_as_warning(caplog):
    app = create_app(_make_settings(log_exclude_paths="", slow_request_threshold_ms=-1))
    with caplog.at_level(logging.INFO, logger="synthetix.api"):
        with TestClient(app) as client:
            client.get("/api/health")

    api_records = [r for r in caplog.records if r.name == "synthetix.api"]
    assert api_records[-1].levelname == "WARNING"
    assert "slow" in api_records[-1].getMessage()
