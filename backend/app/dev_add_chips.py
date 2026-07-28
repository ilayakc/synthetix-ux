"""Yalnizca gelistirme ortami icin: bir kullanicinin organizasyonuna manuel Chip ekler.

Gercek bir odeme/onay akisi olmadigindan (bkz. `app.models.billing.ChipTopUpRequest`
dokstring'i), `chip_topup_requests` satirini "approved" yapmak bakiyeyi ETKILEMEZ -
bakiye yalnizca `chip_ledger_entries` uzerinden hesaplanir (bkz. `app.services.chip_ledger`).
Bu script, o defterin "seed/admin komutu" girdi noktasini kullanarak dogrudan bir
CREDIT satiri yazar.

Kullanim:
    python -m app.dev_add_chips --email kullanici@ornek.com --amount 10000

Uretimde (`ENVIRONMENT != "development"`) kasitli olarak calismayi reddeder.
"""

import argparse
import asyncio
import logging
import sys

from sqlalchemy import select

from app.config import settings
from app.db import async_session_maker
from app.models import Membership, Organization, User
from app.services.auth import normalize_email
from app.services.chip_ledger import credit, get_chip_balance

logger = logging.getLogger("synthetix.dev_add_chips")
logging.basicConfig(level=logging.INFO)


async def add_chips(email: str, amount: int, org_slug: str | None) -> None:
    async with async_session_maker() as session:
        email_normalized = normalize_email(email)
        user = (
            await session.execute(select(User).where(User.email_normalized == email_normalized))
        ).scalar_one_or_none()
        if user is None:
            logger.error("kullanici bulunamadi: '%s'", email)
            sys.exit(1)

        membership_query = select(Membership, Organization).join(
            Organization, Membership.organization_id == Organization.id
        ).where(Membership.user_id == user.id)
        if org_slug is not None:
            membership_query = membership_query.where(Organization.slug == org_slug)
        rows = (await session.execute(membership_query)).all()

        if not rows:
            logger.error(
                "kullanicinin '%s' bir organizasyon uyeligi bulunamadi (org_slug=%s)",
                email,
                org_slug,
            )
            sys.exit(1)
        if len(rows) > 1:
            slugs = ", ".join(sorted(org.slug for _membership, org in rows))
            logger.error(
                "kullanici birden fazla organizasyona uye; --org-slug ile belirtin (secenekler: %s)",
                slugs,
            )
            sys.exit(1)

        _membership, org = rows[0]

        old_balance = await get_chip_balance(session, org.id)
        await credit(
            session,
            org.id,
            amount,
            reason="dev/admin manuel chip yuklemesi (yerel gelistirme)",
        )
        new_balance = await get_chip_balance(session, org.id)
        await session.commit()

        logger.info("kullanici: %s | organizasyon: %s (%s)", email, org.name, org.slug)
        logger.info("eski bakiye: %d", old_balance)
        logger.info("eklenen: %d", amount)
        logger.info("yeni bakiye: %d", new_balance)


def main() -> None:
    if settings.environment != "development":
        logger.error(
            "dev_add_chips yalnizca 'development' ortaminda calisir (mevcut: '%s')",
            settings.environment,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="Chip eklenecek kullanicinin e-posta adresi")
    parser.add_argument("--amount", required=True, type=int, help="Eklenecek Chip miktari (pozitif tam sayi)")
    parser.add_argument(
        "--org-slug",
        default=None,
        help="Kullanici birden fazla organizasyona uyeyse hangi organizasyonun secilecegini belirtir",
    )
    args = parser.parse_args()

    if args.amount <= 0:
        logger.error("amount pozitif olmalidir (verilen: %d)", args.amount)
        sys.exit(1)

    asyncio.run(add_chips(args.email, args.amount, args.org_slug))


if __name__ == "__main__":
    main()
