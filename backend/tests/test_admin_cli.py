"""`python -m app.admin_cli promote/demote` icin testler.

`client` fixture (bkz. tests/conftest.py), suresi boyunca `app.db.
async_session_maker`i test veritabanina yonlendirir; `app.admin_cli` bu
degeri MODUL ATTRIBUTE'U uzerinden (gec-baglanma ile) okudugu icin bu
fixture'i kullanan testler CLI'yi guvenle test veritabanina karsi calistirir.

Dogrulama sorgulari kasitli olarak `session` fixture'ini KULLANMAZ: o fixture
pytest-asyncio'nun kendi (fonksiyon-scope'lu) event loop'una bagli bir
baglanti tutar, `admin_cli.main()` ise kendi `asyncio.run()` cagrisiyla
TAMAMEN AYRI bir loop acar - ikisini ayni testte karistirmak
"attached to a different loop" hatasina yol acar (bkz. conftest.py notlari).
Bunun yerine, `client`'in de kullandigina benzer, her cagrida taze acilip
kapanan bagimsiz bir baglanti kullanilir.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import admin_cli
from app.services.auth import normalize_email
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


def _unique_email() -> str:
    return f"cli-test-{uuid.uuid4().hex[:12]}@example.com"


def _register(client, email: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201


async def _fetch_is_platform_admin(email: str) -> bool | None:
    """Kullanici varsa `is_platform_admin` degerini, yoksa `None` dondurur."""

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT is_platform_admin FROM users WHERE email_normalized = :email"),
                {"email": normalize_email(email)},
            )
            row = result.first()
            return bool(row[0]) if row is not None else None
    finally:
        await engine.dispose()


async def _count_users(email: str) -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT count(*) FROM users WHERE email_normalized = :email"),
                {"email": normalize_email(email)},
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


def test_promote_sets_is_platform_admin_true(client):
    email = _unique_email()
    _register(client, email)

    # Buyuk/kucuk harf farki normalize_email ile giderilmeli.
    exit_code = admin_cli.main(["promote", "--email", email.upper()])
    assert exit_code == 0

    assert asyncio.run(_fetch_is_platform_admin(email)) is True


def test_demote_sets_is_platform_admin_false(client):
    email = _unique_email()
    _register(client, email)
    assert admin_cli.main(["promote", "--email", email]) == 0
    assert asyncio.run(_fetch_is_platform_admin(email)) is True

    exit_code = admin_cli.main(["demote", "--email", email])
    assert exit_code == 0
    assert asyncio.run(_fetch_is_platform_admin(email)) is False


def test_promote_unknown_email_returns_exit_code_1_and_creates_nothing(client):
    email = _unique_email()

    exit_code = admin_cli.main(["promote", "--email", email])
    assert exit_code == 1
    assert asyncio.run(_count_users(email)) == 0


def test_promote_is_idempotent_when_run_twice(client):
    email = _unique_email()
    _register(client, email)

    assert admin_cli.main(["promote", "--email", email]) == 0
    assert admin_cli.main(["promote", "--email", email]) == 0

    assert asyncio.run(_fetch_is_platform_admin(email)) is True
    assert asyncio.run(_count_users(email)) == 1
