"""Guvenli disposable-tenant temizligi (bkz. docs/security.md "Dev/test
ortaminda disposable (smoke) tenant temizligi" ve app.services.tenant_cleanup).

Bu testler, onceki turda yasanan `LIKE 'Smoke%'` toplu-silme olayinin bir
daha olusmamasini garanti eder: yardimci fonksiyon prefix/pattern KABUL
ETMEZ, yalnizca tam kimlik dogrulamasindan gecen exact bir organization_id
siler.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.projects import Project
from app.models.tenancy import Membership, Organization, User
from app.services import tenant_cleanup

pytestmark = pytest.mark.integration


def _has_pattern_parameter() -> bool:
    """`delete_disposable_organization_by_exact_id` imzasinin hicbir
    prefix/pattern/LIKE parametresi tasimadigini dogrular (kod-seviyesinde,
    calisma zamaninda) - isim tabanli bir 'name_prefix'/'like'/'pattern'
    argumani asla eklenmemis olmali."""

    import inspect

    params = set(inspect.signature(tenant_cleanup.delete_disposable_organization_by_exact_id).parameters)
    forbidden = {"name_prefix", "like", "pattern", "name_like", "prefix"}
    return bool(params & forbidden)


def test_cleanup_helper_signature_has_no_prefix_or_pattern_parameter():
    assert not _has_pattern_parameter()


async def _make_disposable_org(session: AsyncSession, *, name: str, email: str) -> tuple[Organization, User]:
    org = Organization(name=name, slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}")
    session.add(org)
    await session.flush()

    user = User(
        email=email,
        email_normalized=email.lower(),
        display_name="Smoke User",
        password_hash="not-a-real-hash",
    )
    session.add(user)
    await session.flush()

    membership = Membership(organization_id=org.id, user_id=user.id, role="owner")
    session.add(membership)
    await session.flush()

    return org, user


async def test_delete_disposable_organization_succeeds_with_exact_matching_identity(
    session: AsyncSession,
):
    org, user = await _make_disposable_org(
        session, name="Smoke Org exact-test", email="smoke-exact@example.com"
    )
    session.add(Project(organization_id=org.id, name="Smoke Project"))
    await session.flush()

    counts = await tenant_cleanup.delete_disposable_organization_by_exact_id(
        session,
        organization_id=org.id,
        expected_name="Smoke Org exact-test",
        expected_slug=org.slug,
        expected_owner_email="smoke-exact@example.com",
        expected_created_at=org.created_at,
    )
    await session.flush()

    assert counts.projects == 1
    assert counts.memberships == 1

    remaining = (
        await session.execute(select(Organization).where(Organization.id == org.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_delete_disposable_organization_rejects_name_mismatch(session: AsyncSession):
    org, _user = await _make_disposable_org(session, name="Smoke Org real", email="smoke-a@example.com")

    with pytest.raises(tenant_cleanup.TenantCleanupGuardError):
        await tenant_cleanup.delete_disposable_organization_by_exact_id(
            session,
            organization_id=org.id,
            expected_name="Smoke Org WRONG NAME",
            expected_slug=org.slug,
        )

    still_there = (
        await session.execute(select(Organization).where(Organization.id == org.id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_delete_disposable_organization_rejects_slug_mismatch(session: AsyncSession):
    org, _user = await _make_disposable_org(session, name="Smoke Org real2", email="smoke-b@example.com")

    with pytest.raises(tenant_cleanup.TenantCleanupGuardError):
        await tenant_cleanup.delete_disposable_organization_by_exact_id(
            session,
            organization_id=org.id,
            expected_name="Smoke Org real2",
            expected_slug="totally-wrong-slug",
        )

    still_there = (
        await session.execute(select(Organization).where(Organization.id == org.id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_delete_disposable_organization_rejects_owner_email_mismatch(session: AsyncSession):
    org, _user = await _make_disposable_org(session, name="Smoke Org real3", email="smoke-c@example.com")

    with pytest.raises(tenant_cleanup.TenantCleanupGuardError):
        await tenant_cleanup.delete_disposable_organization_by_exact_id(
            session,
            organization_id=org.id,
            expected_name="Smoke Org real3",
            expected_slug=org.slug,
            expected_owner_email="not-the-real-owner@example.com",
        )

    still_there = (
        await session.execute(select(Organization).where(Organization.id == org.id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_delete_disposable_organization_rejects_created_at_mismatch(session: AsyncSession):
    from datetime import UTC, datetime

    org, _user = await _make_disposable_org(session, name="Smoke Org real4", email="smoke-d@example.com")

    with pytest.raises(tenant_cleanup.TenantCleanupGuardError):
        await tenant_cleanup.delete_disposable_organization_by_exact_id(
            session,
            organization_id=org.id,
            expected_name="Smoke Org real4",
            expected_slug=org.slug,
            expected_created_at=datetime(2000, 1, 1, tzinfo=UTC),
        )

    still_there = (
        await session.execute(select(Organization).where(Organization.id == org.id))
    ).scalar_one_or_none()
    assert still_there is not None


async def test_delete_disposable_organization_unrelated_organizations_untouched(session: AsyncSession):
    """Onceki turdaki hatanin tam tersini dogrular: bir organizasyon exact-ID
    ile silinirken, ADI/PREFIX'I benzer BASKA hicbir organizasyon etkilenmez."""

    target, _ = await _make_disposable_org(session, name="Smoke Org target", email="smoke-target@example.com")
    sibling, _ = await _make_disposable_org(
        session, name="Smoke Org target 2", email="smoke-sibling@example.com"
    )
    unrelated, _ = await _make_disposable_org(session, name="Smoke Org", email="smoke-unrelated@example.com")

    await tenant_cleanup.delete_disposable_organization_by_exact_id(
        session,
        organization_id=target.id,
        expected_name="Smoke Org target",
        expected_slug=target.slug,
    )
    await session.flush()

    remaining_ids = {row[0] for row in (await session.execute(select(Organization.id))).all()}
    assert target.id not in remaining_ids
    assert sibling.id in remaining_ids
    assert unrelated.id in remaining_ids


async def test_count_related_rows_is_read_only_and_accurate(session: AsyncSession):
    org, _user = await _make_disposable_org(
        session, name="Smoke Org counted", email="smoke-count@example.com"
    )
    session.add(Project(organization_id=org.id, name="P1"))
    session.add(Project(organization_id=org.id, name="P2"))
    await session.flush()

    counts = await tenant_cleanup.count_related_rows(session, org.id)
    assert counts.projects == 2
    assert counts.total() >= 2

    still_there = (
        await session.execute(select(Organization).where(Organization.id == org.id))
    ).scalar_one_or_none()
    assert still_there is not None
