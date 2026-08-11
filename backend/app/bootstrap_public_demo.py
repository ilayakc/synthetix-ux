"""Ortam sirri tanimliysa herkese acik "Canli demo" hesabini hazirlar.

Bu hesap, gelistiricinin kendi demo hesabindan (bkz. app.bootstrap_user)
AYRIDIR; `POST /api/auth/demo-login` yalnizca buna oturum acar.
"""

import asyncio
import logging

from sqlalchemy import select

from app.config import settings
from app.db import async_session_maker
from app.models.ai_pipeline import AIPipelineRun, AIPipelineStatus
from app.models.tenancy import Organization
from app.seed import seed_public_demo
from app.services.ai_pipeline.mutations import retry_ai_pipeline_group

logger = logging.getLogger(__name__)


async def _retry_latest_failed_demo_ai_report() -> None:
    """Public demo'nun son basarisiz AI raporunu deploy basina degil, bir kez yeniler."""

    async with async_session_maker() as session:
        organization_id = (
            await session.execute(
                select(Organization.id).where(Organization.slug == "synthetix-ux-canli-demo")
            )
        ).scalar_one_or_none()
        if organization_id is None:
            return

        pipeline = (
            await session.execute(
                select(AIPipelineRun)
                .where(
                    AIPipelineRun.organization_id == organization_id,
                    AIPipelineRun.status == AIPipelineStatus.FAILED,
                    AIPipelineRun.manual_retry_count == 0,
                )
                .order_by(AIPipelineRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if pipeline is None:
            return

        try:
            await retry_ai_pipeline_group(
                session,
                organization_id=organization_id,
                simulation_run_id=pipeline.simulation_run_id,
            )
            await session.commit()
            logger.info("public demo: en son basarisiz AI raporu bir kez yeniden kuyruga alindi")
        except Exception:
            await session.rollback()
            logger.warning("public demo: AI raporu otomatik yeniden kuyruga alinamadi", exc_info=True)


async def _main() -> None:
    email = (settings.demo_account_email or "").strip()
    password_secret = settings.demo_account_password
    if not email or password_secret is None:
        raise SystemExit("DEMO_ACCOUNT_EMAIL ve DEMO_ACCOUNT_PASSWORD gerekli")

    password = password_secret.get_secret_value()
    if len(password) < 16:
        raise SystemExit("DEMO_ACCOUNT_PASSWORD en az 16 karakter olmali")

    await seed_public_demo(email=email, password=password)
    await _retry_latest_failed_demo_ai_report()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
