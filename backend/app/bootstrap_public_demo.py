"""Ortam sirri tanimliysa herkese acik "Canli demo" hesabini hazirlar.

Bu hesap, gelistiricinin kendi demo hesabindan (bkz. app.bootstrap_user)
AYRIDIR; `POST /api/auth/demo-login` yalnizca buna oturum acar.
"""

import asyncio

from app.config import settings
from app.seed import seed_public_demo


def main() -> None:
    email = (settings.demo_account_email or "").strip()
    password_secret = settings.demo_account_password
    if not email or password_secret is None:
        raise SystemExit("DEMO_ACCOUNT_EMAIL ve DEMO_ACCOUNT_PASSWORD gerekli")

    password = password_secret.get_secret_value()
    if len(password) < 16:
        raise SystemExit("DEMO_ACCOUNT_PASSWORD en az 16 karakter olmali")

    asyncio.run(seed_public_demo(email=email, password=password))


if __name__ == "__main__":
    main()
