"""Ortam sirriyla yonetici bootstrap komutunun guvenlik kontrolleri."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from app import bootstrap_admin

pytestmark = pytest.mark.unit


def test_bootstrap_admin_refuses_missing_credentials(monkeypatch):
    monkeypatch.setattr(bootstrap_admin.settings, "bootstrap_admin_email", None)
    monkeypatch.setattr(bootstrap_admin.settings, "bootstrap_admin_password", None)

    with patch.object(bootstrap_admin, "seed_platform_admin") as mock_seed:
        with pytest.raises(SystemExit):
            bootstrap_admin.main()

    mock_seed.assert_not_called()


def test_bootstrap_admin_refuses_short_password(monkeypatch):
    monkeypatch.setattr(bootstrap_admin.settings, "bootstrap_admin_email", "admin@example.com")
    monkeypatch.setattr(bootstrap_admin.settings, "bootstrap_admin_password", SecretStr("too-short"))

    with patch.object(bootstrap_admin, "seed_platform_admin") as mock_seed:
        with pytest.raises(SystemExit):
            bootstrap_admin.main()

    mock_seed.assert_not_called()


def test_bootstrap_admin_runs_with_generated_secret(monkeypatch):
    monkeypatch.setattr(bootstrap_admin.settings, "bootstrap_admin_email", "admin@example.com")
    monkeypatch.setattr(
        bootstrap_admin.settings,
        "bootstrap_admin_password",
        SecretStr("render-generated-secret-123"),
    )

    with patch.object(bootstrap_admin, "seed_platform_admin", new_callable=MagicMock), patch(
        "asyncio.run"
    ) as mock_run:
        bootstrap_admin.main()

    mock_run.assert_called_once()
