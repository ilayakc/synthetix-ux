"""Ortam sirri tanimliysa Render sunum kullanicisini hazirlar."""

import asyncio

from app.config import settings
from app.seed import seed_demo_user


def main() -> None:
    email = (settings.bootstrap_user_email or "").strip()
    password_secret = settings.bootstrap_user_password
    if not email or password_secret is None:
        raise SystemExit("BOOTSTRAP_USER_EMAIL ve BOOTSTRAP_USER_PASSWORD gerekli")

    password = password_secret.get_secret_value()
    if len(password) < 16:
        raise SystemExit("BOOTSTRAP_USER_PASSWORD en az 16 karakter olmali")

    asyncio.run(seed_demo_user(email=email, password=password))


if __name__ == "__main__":
    main()
