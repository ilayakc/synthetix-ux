"""Ortam sirri tanimliysa Render sunum yoneticisini hazirlar."""

import asyncio
import logging

from app.config import settings
from app.seed import seed_platform_admin

logger = logging.getLogger("synthetix.bootstrap_admin")
logging.basicConfig(level=logging.INFO)


def main() -> None:
    email = (settings.bootstrap_admin_email or "").strip()
    password_secret = settings.bootstrap_admin_password
    if not email or password_secret is None:
        raise SystemExit("BOOTSTRAP_ADMIN_EMAIL ve BOOTSTRAP_ADMIN_PASSWORD gerekli")

    password = password_secret.get_secret_value()
    if len(password) < 16:
        raise SystemExit("BOOTSTRAP_ADMIN_PASSWORD en az 16 karakter olmali")

    asyncio.run(seed_platform_admin(email=email, password=password))


if __name__ == "__main__":
    main()
