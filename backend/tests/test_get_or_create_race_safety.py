"""TOCTOU (time-of-check-to-time-of-use) guvenligi: `get_or_create_*` yardimci
fonksiyonlari, ayni satiri AYNI ANDA ilk kez olusturmaya calisan iki es
zamanli istek altinda 500'e (IntegrityError sizintisi) DUSMEMELIDIR.

Arka plan (gercek olay): production'da, React StrictMode'un (dev) effect'leri
iki kez calistirmasi sonucu iki es zamanli `POST /api/tests/drafts` istegi
ayni organizasyon icin `organization_settings` satirini ayni anda olusturmaya
calisti; ikinci INSERT birincil anahtar cakismasiyla basarisiz oldu ve
yakalanmadigi icin 500 Internal Server Error olarak kullaniciya sizdi (bkz.
docs/security.md "Launch akisinda TOCTOU race" ve sonuc raporu). Ayni desen
`app.services.entitlements.get_or_create_entitlement`de de vardi - bu,
`launch_draft`in TAM ICINDE (`reserve_entitlement` uzerinden) cagirilir, bu
yuzden bir kullanicinin GERCEK "test baslat" tiklamasini 500'e dusurebilirdi.

Bu testler, iki BAGIMSIZ DB oturumunu bir `asyncio.Event` bariyeriyle
senkronize ederek GERCEK bir es zamanli INSERT yarisini (asyncio'nun
zamanlama sansina birakmadan) deterministik olarak zorlar.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.billing import Entitlement
from app.models.settings import OrganizationSettings
from app.models.tenancy import Organization
from app.services import entitlements, settings as settings_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


async def _make_org(session: AsyncSession) -> Organization:
    org = Organization(name=f"Race Org {uuid.uuid4().hex[:8]}", slug=f"race-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()
    await session.commit()
    return org


@pytest.fixture
async def session_factory():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def test_get_or_create_organization_settings_survives_concurrent_first_access(session_factory):
    async with session_factory() as setup_session:
        org = await _make_org(setup_session)
    org_id = org.id

    barrier = asyncio.Event()
    started = 0
    started_lock = asyncio.Lock()

    async def attempt():
        nonlocal started
        async with session_factory() as session:
            async with session.begin():
                existing = await session.get(OrganizationSettings, org_id)
                assert existing is None
                async with started_lock:
                    started += 1
                    if started == 2:
                        barrier.set()
                await barrier.wait()
                return await settings_service.get_or_create_organization_settings(session, org_id)

    results = await asyncio.gather(attempt(), attempt())
    assert all(r.organization_id == org_id for r in results)

    async with session_factory() as verify:
        rows = (
            await verify.execute(
                select(OrganizationSettings).where(OrganizationSettings.organization_id == org_id)
            )
        ).scalars().all()
        assert len(rows) == 1

    async with session_factory() as cleanup:
        o = await cleanup.get(Organization, org_id)
        if o is not None:
            await cleanup.delete(o)
            await cleanup.commit()


async def test_get_or_create_entitlement_survives_concurrent_first_access(session_factory):
    async with session_factory() as setup_session:
        org = await _make_org(setup_session)
    org_id = org.id
    feature_key = "existing_site_basic_ux"

    barrier = asyncio.Event()
    started = 0
    started_lock = asyncio.Lock()

    async def attempt():
        nonlocal started
        async with session_factory() as session:
            async with session.begin():
                async with started_lock:
                    started += 1
                    if started == 2:
                        barrier.set()
                await barrier.wait()
                return await entitlements.get_or_create_entitlement(session, org_id, feature_key)

    results = await asyncio.gather(attempt(), attempt())
    assert all(r.organization_id == org_id and r.feature_key == feature_key for r in results)

    async with session_factory() as verify:
        rows = (
            await verify.execute(
                select(Entitlement).where(
                    Entitlement.organization_id == org_id, Entitlement.feature_key == feature_key
                )
            )
        ).scalars().all()
        assert len(rows) == 1

    async with session_factory() as cleanup:
        o = await cleanup.get(Organization, org_id)
        if o is not None:
            await cleanup.delete(o)
            await cleanup.commit()
