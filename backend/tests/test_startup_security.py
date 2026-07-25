"""`app.main` gercek uygulama baslangicinin (lifespan) production'da bilinen
guvensiz secret'larla servis vermeyi reddettigini kanitlayan smoke testi.

Gercek bir veritabanina/production ortamina baglanmaz: yalnizca
`settings.environment`'i gecici olarak "production" yapip `TestClient(app)`'in
lifespan baslangicinda `ConfigSecurityError` firlatildigini dogrular, sonra
`monkeypatch` ortami otomatik olarak eski haline getirir.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.config_security import ConfigSecurityError
from app.main import app

pytestmark = pytest.mark.integration


def test_app_refuses_to_start_in_production_with_known_insecure_defaults(monkeypatch):
    # Diger alanlara dokunmadan yalnizca environment'i degistiriyoruz: repodaki
    # varsayilan jwt/analyzer/db secret'lari zaten bilinen gelistirme
    # degerleridir, bu yuzden tek basina bu degisiklik reddi tetiklemelidir.
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(ConfigSecurityError):
        with TestClient(app):
            pass
