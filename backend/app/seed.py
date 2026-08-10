"""Yalnizca gelistirme ortami icin kucuk, tekrar calistirilabilir seed komutu.

Kullanim: `python -m app.seed`

Üretimde (`ENVIRONMENT != "development"`) kasitli olarak calismayi reddeder ve
otomatik olarak hicbir yerden cagrilmaz.
"""

import asyncio
import logging
import sys
from typing import TypedDict

from sqlalchemy import select

from app.config import settings
from app.db import async_session_maker
from app.models import Membership, Organization, User
from app.security import hash_password
from app.services.entitlements import list_free_entitlements

logger = logging.getLogger("synthetix.seed")
logging.basicConfig(level=logging.INFO)

DEV_ORG_SLUG = "dev-organization"
DEV_USER_EMAIL = "dev@synthetix.local"
# Yalnizca yerel gelistirme icin sabit bir parola; uretimde bu komut zaten
# calismayi reddeder (bkz. `main()`).
DEV_USER_PASSWORD = "DevPassword123!"

DEMO_PASSWORD = "DemoSynthetix123!"


class DemoAccount(TypedDict):
    email: str
    display_name: str
    organization_name: str
    organization_slug: str
    is_platform_admin: bool


DEMO_ACCOUNTS: tuple[DemoAccount, ...] = (
    {
        "email": "synthetix.demo.admin@example.com",
        "display_name": "Demo Yönetici",
        "organization_name": "Synthetix UX Yönetim Demo",
        "organization_slug": "synthetix-ux-yonetim-demo",
        "is_platform_admin": True,
    },
    {
        "email": "synthetix.demo.user@example.com",
        "display_name": "Demo Kullanıcı",
        "organization_name": "Synthetix UX Demo Şirketi",
        "organization_slug": "synthetix-ux-demo-sirketi",
        "is_platform_admin": False,
    },
)


async def _seed_demo_account(session, account: DemoAccount, *, password: str = DEMO_PASSWORD) -> None:
    organization = (
        await session.execute(select(Organization).where(Organization.slug == account["organization_slug"]))
    ).scalar_one_or_none()
    if organization is None:
        organization = Organization(
            name=account["organization_name"],
            slug=account["organization_slug"],
        )
        session.add(organization)
        await session.flush()

    user = (
        await session.execute(select(User).where(User.email_normalized == account["email"]))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            email=account["email"],
            email_normalized=account["email"],
            display_name=account["display_name"],
            password_hash=hash_password(password),
            is_platform_admin=account["is_platform_admin"],
        )
        session.add(user)
        await session.flush()
    else:
        user.display_name = account["display_name"]
        user.password_hash = hash_password(password)
        user.is_platform_admin = account["is_platform_admin"]

    membership = (
        await session.execute(
            select(Membership).where(
                Membership.organization_id == organization.id,
                Membership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        session.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))

    await list_free_entitlements(session, organization.id)
    logger.info(
        "demo account: '%s' hazirlandi (platform_admin=%s)",
        account["email"],
        account["is_platform_admin"],
    )


async def seed_platform_admin(*, email: str, password: str) -> None:
    """Ortam sirriyla verilen tek sunum yoneticisini idempotent hazirlar."""
    account: DemoAccount = {
        "email": email.strip().lower(),
        "display_name": "Sunum Yoneticisi",
        "organization_name": "Synthetix UX Yonetim",
        "organization_slug": "synthetix-ux-render-yonetim",
        "is_platform_admin": True,
    }
    async with async_session_maker() as session:
        await _seed_demo_account(session, account, password=password)
        await session.commit()


async def seed() -> None:
    async with async_session_maker() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == DEV_ORG_SLUG))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(name="Dev Organization", slug=DEV_ORG_SLUG)
            session.add(org)
            await session.flush()
            logger.info("organizations: '%s' olusturuldu", DEV_ORG_SLUG)
        else:
            logger.info("organizations: '%s' zaten mevcut", DEV_ORG_SLUG)

        user = (
            await session.execute(select(User).where(User.email_normalized == DEV_USER_EMAIL))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=DEV_USER_EMAIL,
                email_normalized=DEV_USER_EMAIL,
                display_name="Dev User",
                password_hash=hash_password(DEV_USER_PASSWORD),
            )
            session.add(user)
            await session.flush()
            logger.info(
                "users: '%s' olusturuldu (gelistirme parolasi: '%s')",
                DEV_USER_EMAIL,
                DEV_USER_PASSWORD,
            )
        else:
            logger.info("users: '%s' zaten mevcut", DEV_USER_EMAIL)

        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.organization_id == org.id,
                    Membership.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            session.add(Membership(organization_id=org.id, user_id=user.id, role="owner"))
            logger.info("memberships: '%s' -> '%s' (owner) olusturuldu", DEV_USER_EMAIL, DEV_ORG_SLUG)
        else:
            logger.info("memberships: '%s' -> '%s' zaten mevcut", DEV_USER_EMAIL, DEV_ORG_SLUG)

        # Yeni hesap ornek gorunumu: 0 Chip bakiyesi (kasitli olarak kredi
        # eklenmez), 1/1 ucretsiz temel UX testi ve 1/1 ucretsiz
        # erisilebilirlik on kontrolu hakki (available durumunda).
        free_entitlements = await list_free_entitlements(session, org.id)
        for entitlement in free_entitlements:
            logger.info(
                "entitlements: '%s' -> '%s' (%s, quantity=%d)",
                DEV_ORG_SLUG,
                entitlement.feature_key,
                entitlement.status.value,
                entitlement.quantity,
            )

        for demo_account in DEMO_ACCOUNTS:
            await _seed_demo_account(session, demo_account)

        await session.commit()


def main() -> None:
    if settings.environment != "development":
        logger.error(
            "seed komutu yalnizca 'development' ortaminda calisir (mevcut: '%s')",
            settings.environment,
        )
        sys.exit(1)

    asyncio.run(seed())


if __name__ == "__main__":
    main()
