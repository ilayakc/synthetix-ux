"""`app.seed` production guard testi.

`seed.main()` herhangi bir DB baglantisi/yazimi denemeden ONCE
`settings.environment` kontrol eder ve production'da `sys.exit(1)` ile
reddeder (bkz. app/seed.py). Bu test gercek gelistirme veritabanina
baglanmadan, yalnizca guard'in DB'ye ulasmadan devreye girdigini kanitlar.
"""

from unittest.mock import patch

import pytest

from app import seed

pytestmark = pytest.mark.unit


def test_seed_refuses_to_run_in_production(monkeypatch):
    monkeypatch.setattr(seed.settings, "environment", "production")

    with patch.object(seed, "seed") as mock_seed_coro:
        with pytest.raises(SystemExit) as exc_info:
            seed.main()

    assert exc_info.value.code == 1
    mock_seed_coro.assert_not_called()


def test_seed_runs_in_development(monkeypatch):
    monkeypatch.setattr(seed.settings, "environment", "development")

    with patch.object(seed, "seed"), patch("asyncio.run") as mock_run:
        seed.main()

    mock_run.assert_called_once()
