"""Paylasilan test altyapisi: izole test veritabani, her testte rollback eden
transaction, ve kiraci (tenant) factory fixture'lari.

Tasarim notlari
----------------
- Testler artik gelistirme veritabanindan (`settings.database_url`) ayri,
  ozel bir test veritabanina (`TEST_DATABASE_URL` env degiskeni veya
  `settings.database_url`'deki DB adinin `_test` sonekiyle turetilmis hali)
  karsi calisir. Bu sayede `pytest` calistirmak asla `docker compose`
  gelistirme verisini etkilemez veya silmez.
- `session` fixture'i, her test icin acilan bir baglanti + dis transaction
  + (SQLAlchemy'nin "join a session into an external transaction" deseniyle)
  bu baglantiya baglanmis bir `AsyncSession` saglar. Test icinde `session.commit()`
  cagrilsa bile (ornegin `TestClient` uzerinden gercek istekler), bu yalnizca
  ic SAVEPOINT'i kapatir; disaridaki gercek transaction test sonunda daima
  rollback edilir, hicbir satir kalici olarak yazilmaz.
- `client` fixture'i FARKLI bir mekanizma kullanir: `TestClient`, istekleri
  kendi (pytest-asyncio'nunkinden BAGIMSIZ) bir portal/event loop'unda
  calistirir; `session`'in pytest'in kendi loop'unda onceden actigi bir
  baglantiyi bu isteklere paylastirmaya calismak "attached to a different
  loop" hatasi verir. Bunun yerine `client`, `app.db.async_session_maker`'i
  (monkeypatch ile) test veritabanina isaret eden ayri bir engine'e yonlendirir
  - normal uygulama davranisi gibi her istek kendi session'ini acip commit
  eder - ve `TestClient` blogu kapandiktan (portal loop kapandiktan) SONRA,
  ayri/taze bir baglantiyla tum kiraci tablolarini `TRUNCATE ... CASCADE` ile
  temizler. Sonuc olarak testler arasi hicbir veri sizmaz; yalnizca izolasyon
  yontemi (rollback vs. truncate) `session` ve `client` icin farklidir.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import redis.asyncio as redis
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db as db_module
import app.redis_client as redis_module
from app.config import settings
from app.main import app
from app.models import Base, Membership, Organization, User
from app.security import hash_password


def _derive_test_database_url() -> str:
    import os

    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        return override

    parts = urlsplit(settings.database_url)
    base_path = parts.path.rsplit("/", 1)
    db_name = base_path[-1] if base_path else "synthetix_ux"
    test_path = "/" + f"{db_name}_test"
    return urlunsplit((parts.scheme, parts.netloc, test_path, parts.query, parts.fragment))


TEST_DATABASE_URL = _derive_test_database_url()


def _asyncpg_dsn(sqlalchemy_url: str) -> tuple[str, str]:
    """`postgresql+asyncpg://...` bicimindeki URL'i asyncpg DSN'ine ve DB adina cevirir."""

    parts = urlsplit(sqlalchemy_url)
    scheme = parts.scheme.split("+", 1)[0]
    dsn = urlunsplit((scheme, parts.netloc, "/postgres", parts.query, parts.fragment))
    db_name = parts.path.lstrip("/")
    return dsn, db_name


async def _ensure_test_database_exists(sqlalchemy_url: str) -> None:
    admin_dsn, db_name = _asyncpg_dsn(sqlalchemy_url)
    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


def _run_migrations_to_head(sqlalchemy_url: str) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parents[1]
    alembic_cfg = Config(str(backend_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_root / "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", sqlalchemy_url)

    # migrations/env.py, alembic Config'e verilen sqlalchemy.url'i degil,
    # dogrudan `app.config.settings.database_url`'i okur; test DB'sine karsi
    # migrate edebilmek icin gecici olarak bu degeri test URL'ine cevirip
    # migration sonrasi eski haline geri getiriyoruz.
    original_database_url = settings.database_url
    settings.database_url = sqlalchemy_url
    try:
        command.upgrade(alembic_cfg, "head")
    finally:
        settings.database_url = original_database_url


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database() -> None:
    """Test DB'sini (yoksa) olusturur ve migration'lari bir kez uygular."""

    import asyncio

    asyncio.run(_ensure_test_database_exists(TEST_DATABASE_URL))
    _run_migrations_to_head(TEST_DATABASE_URL)


@pytest.fixture
async def test_engine() -> AsyncIterator:
    # Fonksiyon-scope'lu: pytest-asyncio varsayilan olarak her testte yeni bir
    # event loop actigi icin (asyncio_default_fixture_loop_scope=function),
    # session-scope'lu bir async engine/connection loop'lar arasi paylasilip
    # "attached to a different loop" hatasina yol acar. NullPool zaten
    # baglanti havuzlamadigi icin fonksiyon basina yeni engine acmanin
    # performans maliyeti ihmal edilebilir.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _deterministic_random_seed() -> None:
    """Testler arasi sizinti olmayan, sabit bir rastgelelik tohumu (determinizm icin)."""

    random.seed(1234)


@pytest.fixture
async def session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Her testi kendi izole transaction'inda calistirir; test sonunda daima rollback eder.

    `session.commit()` cagrilari (dogrudan veya `TestClient` uzerinden) yalnizca
    ic SAVEPOINT'i kapatir; disaridaki gercek DB transaction'i hicbir zaman
    commit edilmez.
    """

    connection: AsyncConnection = await test_engine.connect()
    outer_transaction = await connection.begin()

    session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

    try:
        yield session
    finally:
        await session.close()
        await outer_transaction.rollback()
        await connection.close()


async def _truncate_all_tables(sqlalchemy_url: str) -> None:
    """Test DB'sindeki tum tablolari (taze, ayri bir baglantiyla) bosaltir."""

    engine = create_async_engine(sqlalchemy_url, poolclass=NullPool)
    try:
        table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
        if not table_names:
            return
        async with engine.begin() as connection:
            from sqlalchemy import text

            await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Test veritabanina yonlendirilmis, gercek (commit eden) bir `TestClient`.

    Her test kendi `TestClient` ornegini alir; test sonunda tum kiraci
    tablolari `TRUNCATE` ile temizlenir, boylece testler arasi veri sizmaz.
    """

    test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    test_session_maker = async_sessionmaker(test_engine, expire_on_commit=False)

    # `redis_client` (bkz. app/redis_client.py) her modulde `from app.redis_client
    # import redis_client` ile ayrica import edildigi icin (ornegin
    # app.routers.auth, app.services.simulation_progress), tek basina modul
    # niteligini degistirmek (`redis_module.redis_client = ...`) diger
    # modullerdeki eski referanslari etkilemez. Bunun yerine, PAYLASILAN
    # nesnenin (`redis_module.redis_client`) `connection_pool`'unu taze bir
    # havuzla degistiriyoruz; boylece nesne kimligi ayni kalir (tum
    # import'lar ayni istemciyi gorur) ama onceki (kapanmis bir event loop'a
    # bagli) soketler yeniden kullanilmaya calisilmaz.
    redis_module.redis_client.connection_pool = redis.ConnectionPool.from_url(
        settings.redis_url, decode_responses=True
    )

    original_session_maker = db_module.async_session_maker
    db_module.async_session_maker = test_session_maker
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        db_module.async_session_maker = original_session_maker
        asyncio.run(test_engine.dispose())
        asyncio.run(_truncate_all_tables(TEST_DATABASE_URL))


@pytest.fixture
async def organization(session: AsyncSession) -> Organization:
    org = Organization(name="Test Org", slug=f"test-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    return org


@pytest.fixture
def make_organization(session: AsyncSession):
    """Bir testin birden fazla izole organizasyona ihtiyaci oldugunda kullanilir."""

    async def _make(name: str = "Test Org", **overrides) -> Organization:
        org = Organization(name=name, slug=f"org-{uuid.uuid4().hex[:8]}", **overrides)
        session.add(org)
        await session.flush()
        return org

    return _make


@pytest.fixture
def make_user(session: AsyncSession):
    """Verilen organizasyon icin, varsayilan olarak `owner` rolunde bir kullanici uretir."""

    async def _make(
        organization: Organization,
        *,
        email: str | None = None,
        password: str = "CorrectHorse123!",
        role: str = "owner",
        display_name: str = "Test User",
    ) -> User:
        email = email or f"user-{uuid.uuid4().hex[:12]}@example.com"
        user = User(
            email=email,
            email_normalized=email.lower(),
            password_hash=hash_password(password),
            display_name=display_name,
        )
        session.add(user)
        await session.flush()

        membership = Membership(organization_id=organization.id, user_id=user.id, role=role)
        session.add(membership)
        await session.flush()
        return user

    return _make
