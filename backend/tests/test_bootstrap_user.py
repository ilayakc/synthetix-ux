"""Ortam sirriyla normal kullanici bootstrap komutunun guvenlik kontrolleri."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from app import bootstrap_user

pytestmark = pytest.mark.unit


def test_bootstrap_user_refuses_missing_credentials(monkeypatch):
    monkeypatch.setattr(bootstrap_user.settings, "bootstrap_user_email", None)
    monkeypatch.setattr(bootstrap_user.settings, "bootstrap_user_password", None)

    with patch.object(bootstrap_user, "seed_demo_user") as mock_seed:
        with pytest.raises(SystemExit):
            bootstrap_user.main()

    mock_seed.assert_not_called()


def test_bootstrap_user_refuses_short_password(monkeypatch):
    monkeypatch.setattr(bootstrap_user.settings, "bootstrap_user_email", "user@example.com")
    monkeypatch.setattr(bootstrap_user.settings, "bootstrap_user_password", SecretStr("too-short"))

    with patch.object(bootstrap_user, "seed_demo_user") as mock_seed:
        with pytest.raises(SystemExit):
            bootstrap_user.main()

    mock_seed.assert_not_called()


def test_bootstrap_user_runs_with_generated_secret(monkeypatch):
    monkeypatch.setattr(bootstrap_user.settings, "bootstrap_user_email", "user@example.com")
    monkeypatch.setattr(
        bootstrap_user.settings,
        "bootstrap_user_password",
        SecretStr("render-generated-user-secret-123"),
    )

    with patch.object(bootstrap_user, "seed_demo_user", new_callable=MagicMock), patch(
        "asyncio.run"
    ) as mock_run:
        bootstrap_user.main()

    mock_run.assert_called_once()
