"""`app.config_security.validate_production_secrets` icin izole unit testleri.

Bu testler gercek `app.config.settings` singleton'ini DEGISTIRMEZ - her
senaryo icin ayri bir `Settings(...)` ornegi olusturulur ve dogrulayiciya
acikca verilir, boylece testler birbirini kirletmez ve surec ortamini
(env var) hic dokunmadan bırakır.
"""

import pytest

from app.config import Settings
from app.config_security import ConfigSecurityError, validate_production_secrets

pytestmark = pytest.mark.unit

_STRONG_JWT_SECRET = "a" * 64
_STRONG_ANALYZER_TOKEN = "b" * 64
_STRONG_DB_URL = "postgresql+asyncpg://synthetix:S3cur3-Rand0m-P4ssw0rd@db:5432/synthetix_ux"


def _make_settings(**overrides) -> Settings:
    base = {
        "environment": "production",
        "jwt_secret_key": _STRONG_JWT_SECRET,
        "analyzer_shared_token": _STRONG_ANALYZER_TOKEN,
        "database_url": _STRONG_DB_URL,
    }
    base.update(overrides)
    return Settings(**base)


def test_production_missing_jwt_secret_is_rejected():
    cfg = _make_settings(jwt_secret_key="")
    with pytest.raises(ConfigSecurityError) as exc_info:
        validate_production_secrets(cfg)
    assert "JWT_SECRET_KEY" in str(exc_info.value)
    assert _STRONG_JWT_SECRET not in str(exc_info.value)


def test_production_whitespace_only_jwt_secret_is_rejected():
    cfg = _make_settings(jwt_secret_key="   ")
    with pytest.raises(ConfigSecurityError, match="JWT_SECRET_KEY"):
        validate_production_secrets(cfg)


def test_production_dev_default_jwt_secret_is_rejected():
    cfg = _make_settings(jwt_secret_key="dev-insecure-secret-key-change-me")
    with pytest.raises(ConfigSecurityError, match="JWT_SECRET_KEY"):
        validate_production_secrets(cfg)


def test_production_default_postgres_password_is_rejected():
    cfg = _make_settings(
        database_url="postgresql+asyncpg://synthetix:devpassword@db:5432/synthetix_ux"
    )
    with pytest.raises(ConfigSecurityError) as exc_info:
        validate_production_secrets(cfg)
    assert "DATABASE_URL" in str(exc_info.value) or "POSTGRES_PASSWORD" in str(exc_info.value)
    assert "devpassword" not in str(exc_info.value)


def test_production_missing_postgres_password_is_rejected():
    cfg = _make_settings(database_url="postgresql+asyncpg://synthetix@db:5432/synthetix_ux")
    with pytest.raises(ConfigSecurityError):
        validate_production_secrets(cfg)


def test_production_default_analyzer_token_is_rejected():
    cfg = _make_settings(analyzer_shared_token="dev-insecure-analyzer-token-change-me")
    with pytest.raises(ConfigSecurityError, match="ANALYZER_SHARED_TOKEN"):
        validate_production_secrets(cfg)


def test_production_short_analyzer_token_is_rejected():
    cfg = _make_settings(analyzer_shared_token="short-token")
    with pytest.raises(ConfigSecurityError, match="ANALYZER_SHARED_TOKEN"):
        validate_production_secrets(cfg)


def test_production_short_jwt_secret_is_rejected():
    cfg = _make_settings(jwt_secret_key="short-secret")
    with pytest.raises(ConfigSecurityError, match="JWT_SECRET_KEY"):
        validate_production_secrets(cfg)


def test_production_equal_jwt_and_analyzer_token_is_rejected():
    same_value = "z" * 64
    cfg = _make_settings(jwt_secret_key=same_value, analyzer_shared_token=same_value)
    with pytest.raises(ConfigSecurityError) as exc_info:
        validate_production_secrets(cfg)
    assert "JWT_SECRET_KEY" in str(exc_info.value)
    assert "ANALYZER_SHARED_TOKEN" in str(exc_info.value)


def test_production_strong_distinct_values_pass():
    cfg = _make_settings()
    validate_production_secrets(cfg)  # raise etmemeli


def test_development_environment_keeps_dev_defaults_working():
    cfg = Settings(
        environment="development",
        jwt_secret_key="dev-insecure-secret-key-change-me",
        analyzer_shared_token="dev-insecure-analyzer-token-change-me",
        database_url="postgresql+asyncpg://synthetix:devpassword@db:5432/synthetix_ux",
    )
    validate_production_secrets(cfg)  # raise etmemeli


def test_test_environment_is_unaffected():
    cfg = Settings(
        environment="test",
        jwt_secret_key="dev-insecure-secret-key-change-me",
        analyzer_shared_token="dev-insecure-analyzer-token-change-me",
        database_url="postgresql+asyncpg://synthetix:devpassword@db:5432/synthetix_ux_test",
    )
    validate_production_secrets(cfg)  # raise etmemeli


def test_error_messages_never_contain_secret_values():
    secret_value = "TopSecretValueThatMustNeverLeak123456"
    cfg = _make_settings(jwt_secret_key=secret_value[:10])  # kisa -> reddedilir
    with pytest.raises(ConfigSecurityError) as exc_info:
        validate_production_secrets(cfg)
    assert secret_value not in str(exc_info.value)
    assert secret_value[:10] not in str(exc_info.value)


def test_default_settings_argument_uses_real_app_settings(monkeypatch):
    """Argumansiz cagri, gercek `app.config.settings` singleton'ini kullanmalidir."""

    import app.config as config_module

    monkeypatch.setattr(config_module.settings, "environment", "development")
    from app.config_security import validate_production_secrets as validate

    validate()  # development'ta hicbir kontrol yapilmadan gecmeli
