"""Platform yoneticiligi (`users.is_platform_admin`) icin komut satiri araci.

Kullanim:
    python -m app.admin_cli promote --email user@example.com
    python -m app.admin_cli demote  --email user@example.com

Bu komut, `app.dependencies.require_platform_admin`'in tek guvendigi kaynak
olan `users.is_platform_admin` kolonunu DOGRUDAN gunceller. Hicbir kullanici/
organizasyon OLUSTURMAZ - yalnizca zaten var olan bir kullanici uzerinde
UPDATE yapar. Komut satiri erisiminin sunucu yoneticisi yetkisiyle
calistirildigi varsayilir (bkz. docs); bu yuzden `app.seed`'in aksine
`ENVIRONMENT` kisitlamasi YOKTUR - production'da da kullanilabilir.
"""

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select

import app.db as db_module
from app.models.tenancy import User
from app.services.auth import normalize_email

logger = logging.getLogger("synthetix.admin_cli")
logging.basicConfig(level=logging.INFO, format="%(message)s")


class UserNotFoundError(Exception):
    """Verilen (normalize edilmis) e-postaya sahip bir kullanici bulunamadi."""


async def _set_platform_admin(email: str, *, is_platform_admin: bool) -> str:
    """Kullanicinin `is_platform_admin` degerini gunceller ve normalize e-postayi dondurur.

    Tek bir transaction icinde calisir: kullanici bulunamazsa hicbir yazma
    yapilmadan `UserNotFoundError` firlatilir; herhangi bir DB hatasinda
    context manager transaction'i geri alir (rollback), kismi bir degisiklik
    kalmaz. Deger zaten istenen durumdaysa (idempotent cagri) yine de
    basariyla doner, ikinci bir yan etki uretmez.

    `db_module.async_session_maker` MODUL ATTRIBUTE'U uzerinden (dogrudan
    `from app.db import async_session_maker` ile DEGIL) cagrilir; boylece
    testler (bkz. tests/conftest.py `client` fixture) bu degeri test
    veritabanina yonlendirmek icin monkeypatch yapabilir.
    """

    email_normalized = normalize_email(email)

    async with db_module.async_session_maker() as session:
        result = await session.execute(select(User).where(User.email_normalized == email_normalized))
        user = result.scalar_one_or_none()
        if user is None:
            raise UserNotFoundError(email_normalized)

        user.is_platform_admin = is_platform_admin
        await session.commit()

    return email_normalized


def _run(email: str, *, is_platform_admin: bool) -> int:
    try:
        email_normalized = asyncio.run(_set_platform_admin(email, is_platform_admin=is_platform_admin))
    except UserNotFoundError as exc:
        logger.error("Kullanici bulunamadi: %s", exc)
        return 1

    logger.info("%s: is_platform_admin = %s", email_normalized, is_platform_admin)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.admin_cli",
        description="Bir kullaniciyi platform yoneticisi yapar veya yoneticilikten cikarir.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    promote_parser = subparsers.add_parser("promote", help="Kullaniciyi platform yoneticisi yapar")
    promote_parser.add_argument("--email", required=True)

    demote_parser = subparsers.add_parser("demote", help="Kullaniciyi platform yoneticiliginden cikarir")
    demote_parser.add_argument("--email", required=True)

    args = parser.parse_args(argv)
    return _run(args.email, is_platform_admin=args.command == "promote")


if __name__ == "__main__":
    sys.exit(main())
