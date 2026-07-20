"""Yalnizca gelistirme ortami icin kucuk, tekrar calistirilabilir seed komutu.

Kullanim: `python -m app.seed`

Üretimde (`ENVIRONMENT != "development"`) kasitli olarak calismayi reddeder ve
otomatik olarak hicbir yerden cagrilmaz.
"""

import asyncio
import logging
import sys

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
