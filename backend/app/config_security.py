"""Production baslangicinda fail-closed secret dogrulamasi.

Bu modul YALNIZCA acikca cagrildiginda (bkz. app.main._lifespan,
app.worker.on_startup) dogrulama yapar; import edilmesinin kendisi hicbir
yan etki uretmez ve development/test ortamlarini etkilemez.
"""

from urllib.parse import urlsplit

from app.config import Settings
from app.config import settings as _default_settings


class ConfigSecurityError(RuntimeError):
    """`ENVIRONMENT=production` icin fail-closed dogrulama basarisiz oldugunda firlatilir."""


_MIN_SECRET_LENGTH = 32

# Repodaki bilinen gelistirme/e2e varsayilanlari (bkz. .env.example, compose.e2e.env).
_KNOWN_INSECURE_VALUES = {
    "dev-insecure-secret-key-change-me",
    "e2e-insecure-secret-key-change-me",
    "dev-insecure-analyzer-token-change-me",
    "e2e-insecure-analyzer-token-change-me",
    "devpassword",
}

# Bilinen tam degerlerin disinda, genel placeholder/ornek isaretleri.
_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "devpassword",
    "dev-insecure",
    "insecure",
    "placeholder",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    if lowered in _KNOWN_INSECURE_VALUES:
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _require_secret(name: str, value: str | None) -> None:
    if value is None or not value.strip():
        raise ConfigSecurityError(
            f"{name} ortam degiskeni production'da zorunludur ve bos/whitespace olamaz. "
            "Guvenli, rastgele uretilmis bir deger ayarlayin (bkz. .env.production.example)."
        )
    if _looks_like_placeholder(value):
        raise ConfigSecurityError(
            f"{name} bilinen bir gelistirme/placeholder degeri iceriyor. "
            "Production icin benzersiz, rastgele uretilmis bir degerle degistirin."
        )
    if len(value) < _MIN_SECRET_LENGTH:
        raise ConfigSecurityError(
            f"{name} production icin yeterince uzun degil (en az {_MIN_SECRET_LENGTH} karakter gereklidir)."
        )


def validate_production_secrets(settings: Settings | None = None) -> None:
    """`ENVIRONMENT=production` iken kritik secret'lari fail-closed dogrular.

    `settings` verilmezse gercek uygulama ayarlari (`app.config.settings`)
    kullanilir. Development/test ortaminda (environment != "production")
    hicbir kontrol yapmadan hemen doner.
    """

    cfg = settings if settings is not None else _default_settings

    if cfg.environment != "production":
        return

    _require_secret("JWT_SECRET_KEY", cfg.jwt_secret_key)
    _require_secret("ANALYZER_SHARED_TOKEN", cfg.analyzer_shared_token)

    db_password = urlsplit(cfg.database_url).password
    if db_password is None or not db_password.strip():
        raise ConfigSecurityError(
            "DATABASE_URL (POSTGRES_PASSWORD) production ortaminda zorunludur ve bos olamaz. "
            "Guvenli, rastgele uretilmis bir parola ayarlayin (bkz. .env.production.example)."
        )
    if _looks_like_placeholder(db_password):
        raise ConfigSecurityError(
            "DATABASE_URL (POSTGRES_PASSWORD) bilinen bir gelistirme/placeholder degeri iceriyor. "
            "Production icin benzersiz, rastgele uretilmis bir parolayla degistirin."
        )

    if cfg.jwt_secret_key == cfg.analyzer_shared_token:
        raise ConfigSecurityError(
            "JWT_SECRET_KEY ve ANALYZER_SHARED_TOKEN ayni degere sahip olamaz. "
            "Her biri icin ayri, benzersiz bir deger uretin."
        )
