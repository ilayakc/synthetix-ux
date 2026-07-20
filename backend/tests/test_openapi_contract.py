"""OpenAPI semasinin hatasiz uretildigini ve kritik uc noktalarin
kaybolmadigini dogrulayan hafif bir sozlesme (contract) testi.

Bu, `scripts/contract_check.py`'nin (frontend `api/client.ts` ile semayi
karsilastiran) dayandigi temel varsayimi -sema her zaman gecerli ve
eksiksiz uretilir- erken ve ucuz bicimde kanitlar; bir router yanlislikla
kaldirilir/kirilirsa burada hemen yakalanir.
"""

import pytest

from app.main import app

pytestmark = pytest.mark.unit

CRITICAL_PATH_PREFIXES = (
    "/api/auth/register",
    "/api/auth/login",
    "/api/projects",
    "/api/tests/drafts",
    "/api/simulations",
    "/api/reports",
    "/api/billing",
)


def test_openapi_schema_generates_without_error():
    schema = app.openapi()
    assert schema["openapi"]
    assert "paths" in schema and len(schema["paths"]) > 0


def test_openapi_schema_contains_critical_paths():
    schema = app.openapi()
    paths = set(schema["paths"].keys())

    for prefix in CRITICAL_PATH_PREFIXES:
        assert any(
            path.startswith(prefix) for path in paths
        ), f"Kritik path onegi semada bulunamadi: {prefix}"
