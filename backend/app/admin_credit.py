"""Yalnizca gelistirme ortami icin test Chip kredisi eklemek amacli komut satiri araci.

Halka acik bir "bedava Chip ekle" API endpointi kasitli olarak YOKTUR;
Chip kredisi eklemenin tek yolu bu komuttur (veya ileride eklenecek gercek
satin alma entegrasyonudur). Uretimde (`ENVIRONMENT != "development"`)
calismayi reddeder.

Kullanim:
    python -m app.admin_credit <organization-slug> <amount> [reason]
"""

import asyncio
import logging
import sys
import uuid

from sqlalchemy import select

from app.config import settings
from app.db import async_session_maker
from app.models import Organization
from app.services import chip_ledger

logger = logging.getLogger("synthetix.admin_credit")
logging.basicConfig(level=logging.INFO)


async def add_credit(organization_slug: str, amount: int, reason: str) -> None:
    async with async_session_maker() as session:
        organization = (
            await session.execute(select(Organization).where(Organization.slug == organization_slug))
        ).scalar_one_or_none()
        if organization is None:
            logger.error("organizations: '%s' bulunamadi", organization_slug)
            sys.exit(1)

        idempotency_key = f"admin_credit:{uuid.uuid4()}"
        entry = await chip_ledger.credit(
            session,
            organization.id,
            amount,
            reason,
            idempotency_key=idempotency_key,
        )
        await session.commit()
        logger.info(
            "chip_ledger_entries: '%s' organizasyonuna %d Chip eklendi (entry_id=%s)",
            organization_slug,
            amount,
            entry.id,
        )


def main() -> None:
    if settings.environment != "development":
        logger.error(
            "admin_credit komutu yalnizca 'development' ortaminda calisir (mevcut: '%s')",
            settings.environment,
        )
        sys.exit(1)

    if len(sys.argv) < 3:
        logger.error("kullanim: python -m app.admin_credit <organization-slug> <amount> [reason]")
        sys.exit(1)

    organization_slug = sys.argv[1]
    try:
        amount = int(sys.argv[2])
    except ValueError:
        logger.error("amount bir tam sayi olmalidir")
        sys.exit(1)
    reason = sys.argv[3] if len(sys.argv) > 3 else "dev/admin test kredisi"

    asyncio.run(add_credit(organization_slug, amount, reason))


if __name__ == "__main__":
    main()
