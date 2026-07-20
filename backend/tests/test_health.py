import pytest
from fastapi.testclient import TestClient

# `client` fixture'i artik tests/conftest.py'den gelir (her test icin taze,
# izole test veritabanina yonlendirilmis bir TestClient).

pytestmark = pytest.mark.integration


def test_health_returns_ok(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_dependencies_independently(client: TestClient):
    response = client.get("/api/ready")

    # DB/Redis bu ortamda calismiyor olabilir; onemli olan her iki
    # bagimliligin da ayri ayri raporlanmasi.
    assert response.status_code in (200, 503)
    body = response.json()
    assert set(body.keys()) == {"ready", "database", "redis", "environment"}
    assert body["database"] in ("ok", "unavailable")
    assert body["redis"] in ("ok", "unavailable")
