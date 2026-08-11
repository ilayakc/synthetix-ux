"""Production sertlestirme (docs/redoc/openapi kapatma, TrustedHostMiddleware,
guvenlik header'lari) icin izole testler.

ONEMLI: `app.main.create_app()` HER senaryo icin TAZE bir `Settings` ornegi
ile cagirilir - gercek `app.config.settings` singleton'i veya modul-seviyesi
`app.main.app` DEGISTIRILMEZ. Bunun nedeni: docs_url/TrustedHostMiddleware/
guvenlik-header middleware'i FastAPI app'i KURULURKEN (import zamaninda)
eklenir/eklenmez - zaten kurulmus bir app uzerinde `settings.environment`'i
sonradan monkeypatch'lemenin bu davranislari degistirmeyecegi (module-level
cache) bu yuzden HER test kendi `create_app(cfg)` cagrisini yapar; testler
ne birbirini ne de tam paketi (`app.main.app`) kirletir.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import create_access_token

pytestmark = pytest.mark.unit

_STRONG_JWT_SECRET = "a" * 64
_STRONG_ANALYZER_TOKEN = "b" * 64
_STRONG_DB_URL = "postgresql+asyncpg://synthetix:S3cur3-Rand0m-P4ssw0rd@db:5432/synthetix_ux"
_ALLOWED_HOST = "app.example.test"


def _make_production_settings(**overrides) -> Settings:
    base = {
        "environment": "production",
        "jwt_secret_key": _STRONG_JWT_SECRET,
        "analyzer_shared_token": _STRONG_ANALYZER_TOKEN,
        "database_url": _STRONG_DB_URL,
        "allowed_hosts": _ALLOWED_HOST,
    }
    base.update(overrides)
    return Settings(**base)


def _make_development_settings(**overrides) -> Settings:
    base = {"environment": "development"}
    base.update(overrides)
    return Settings(**base)


def test_development_docs_and_openapi_are_enabled():
    dev_app = create_app(_make_development_settings())
    with TestClient(dev_app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_production_docs_redoc_openapi_are_closed():
    prod_app = create_app(_make_production_settings())
    with TestClient(prod_app, base_url=f"http://{_ALLOWED_HOST}") as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/redoc").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_production_trusted_host_accepts_configured_host():
    prod_app = create_app(_make_production_settings())
    with TestClient(prod_app, base_url=f"http://{_ALLOWED_HOST}") as client:
        response = client.get("/api/health")
    assert response.status_code == 200


def test_production_trusted_host_rejects_unknown_host():
    prod_app = create_app(_make_production_settings())
    # `TestClient`'in varsayilan base_url'i ("http://testserver") ALLOWED_HOSTS
    # icinde degildir - bilinmeyen bir Host header'iyla gelen istegin
    # reddedildigini dogrular.
    with TestClient(prod_app) as client:
        response = client.get("/api/health")
    assert response.status_code == 400
    body_text = response.text
    for leaked in (_STRONG_JWT_SECRET, _STRONG_ANALYZER_TOKEN, "S3cur3-Rand0m-P4ssw0rd", "db:5432"):
        assert leaked not in body_text


def test_production_security_headers_present():
    prod_app = create_app(_make_production_settings())
    with TestClient(prod_app, base_url=f"http://{_ALLOWED_HOST}") as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "permissions-policy" in response.headers


def test_production_csp_does_not_allow_unsafe_eval():
    prod_app = create_app(_make_production_settings())
    with TestClient(prod_app, base_url=f"http://{_ALLOWED_HOST}") as client:
        response = client.get("/api/health")

    csp = response.headers["content-security-policy"]
    assert "unsafe-eval" not in csp
    # style-src icin BILINCLI/belgelenmis istisna disinda script-src'de
    # 'unsafe-inline' bulunmamalidir.
    assert "script-src 'self';" in csp


def test_development_has_no_trusted_host_restriction():
    dev_app = create_app(_make_development_settings())
    # Development'ta TrustedHostMiddleware hic eklenmez - herhangi bir Host
    # header'i (varsayilan davranis) kabul edilmeye devam eder.
    with TestClient(dev_app, base_url="http://anything.invalid.example") as client:
        response = client.get("/api/health")
    assert response.status_code == 200


def test_development_responses_have_no_production_security_headers():
    dev_app = create_app(_make_development_settings())
    with TestClient(dev_app) as client:
        response = client.get("/api/health")

    assert "content-security-policy" not in response.headers
    assert "x-frame-options" not in response.headers


def test_demo_access_token_blocks_mutating_api_requests():
    dev_app = create_app(_make_development_settings())
    token = create_access_token(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        role="owner",
        is_demo=True,
    )

    with TestClient(dev_app) as client:
        client.cookies.set("access_token", token)
        response = client.post("/api/projects", json={"name": "Degistirilemez"})

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Canli demo salt okunurdur; bu hesapta degisiklik yapilamaz."
    }


def test_production_missing_allowed_hosts_fails_closed():
    from app.config_security import ConfigSecurityError

    prod_app = create_app(_make_production_settings(allowed_hosts=""))
    with pytest.raises(ConfigSecurityError, match="ALLOWED_HOSTS"):
        with TestClient(prod_app):
            pass


def test_production_wildcard_allowed_hosts_fails_closed():
    from app.config_security import ConfigSecurityError

    prod_app = create_app(_make_production_settings(allowed_hosts="*"))
    with pytest.raises(ConfigSecurityError, match="ALLOWED_HOSTS"):
        with TestClient(prod_app):
            pass
