"""Herkese acik, salt-okunur canli demo hesabini ve tek birlesik testi hazirlar.

Bu hesap, gelistiricinin ``BOOTSTRAP_USER_EMAIL`` hesabindan tamamen ayridir.
Bakim islemleri yalnizca ``synthetix-ux-canli-demo`` organizasyonuna uygulanir.
"""

import asyncio
import logging
import uuid

from sqlalchemy import delete, func, select

from app.config import settings
from app.db import async_session_maker
from app.models.page_analysis import PageAnalysis
from app.models.projects import Project
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization, User
from app.models.test_wizard import TestWizardDraft, TestWizardDraftStatus
from app.models.tests import TestDefinition, TestVariant
from app.seed import seed_public_demo
from app.services import chip_ledger
from app.services import test_wizard as wizard_service
from app.services.pricing import (
    AI_INTERACTION_HEATMAP_CHIP_COST,
    AI_INTERACTION_HEATMAP_MODULE_KEY,
)

logger = logging.getLogger(__name__)

PUBLIC_DEMO_ORG_SLUG = "synthetix-ux-canli-demo"
COMBINED_PROJECT_NAME = "Synthetix UX Canlı Demo"
COMBINED_TEST_NAME = "Birleşik UX, Erişilebilirlik ve AI Analizi"
COMBINED_TEST_URL = "https://synthetix-ux-ily.onrender.com/"
COMBINED_DEMO_CREDIT = 300
COMBINED_DEMO_CREDIT_KEY = "demo_public_combined_v1_credit"
COMBINED_MODULES = (
    "network_device_test",
    "campaign_cta_test",
    "synthetic_attention_estimate",
    "ai_report",
    AI_INTERACTION_HEATMAP_MODULE_KEY,
)

COMBINED_HEATMAP_RESERVATION_KEY = "demo_public_combined_v1_ai_heatmap"


def _combined_demo_payload(project_id: uuid.UUID) -> dict:
    """Gercek sihirbaz/quote/launch servisine verilecek sunucu-kontrollu payload."""

    return {
        "project_id": str(project_id),
        "name": COMBINED_TEST_NAME,
        "target_task": (
            "Ziyaretçinin ürünün değer önerisini anlayıp uygun başlangıç yolunu seçmesi: "
            "ücretsiz hesap oluşturma, mevcut hesaba giriş veya canlı demoyu inceleme."
        ),
        "test_description": (
            "Synthetix UX ana sayfasının bilgi mimarisi, CTA hiyerarşisi, görev akışı, "
            "erişilebilirlik sinyalleri, sentetik dikkat dağılımı ve farklı ağ/cihaz "
            "profillerindeki teknik davranışını tek raporda değerlendiren canlı demo testi."
        ),
        "test_type": wizard_service.EXISTING_SITE_BASIC_UX,
        "current_url": COMBINED_TEST_URL,
        "current_source_type": wizard_service.SOURCE_TYPE_URL,
        "persona_count": 100,
        "target_audience": (
            "SaaS ürünlerini değerlendiren, farklı yaş ve dijital yatkınlık düzeylerindeki "
            "mobil ve masaüstü web kullanıcıları"
        ),
        "persona_preset_id": "builtin:general_web_users",
        "modules": list(COMBINED_MODULES),
        "authorization_confirmed": True,
        "device_profile": "desktop",
    }


async def _combined_demo_already_ready(session, organization_id: uuid.UUID) -> bool:
    project_count = int(
        (
            await session.execute(
                select(func.count(Project.id)).where(Project.organization_id == organization_id)
            )
        ).scalar_one()
    )
    test_count = int(
        (
            await session.execute(
                select(func.count(TestDefinition.id)).where(
                    TestDefinition.organization_id == organization_id
                )
            )
        ).scalar_one()
    )
    marker_exists = (
        await session.execute(
            select(TestDefinition.id)
            .join(Project, Project.id == TestDefinition.project_id)
            .where(
                Project.organization_id == organization_id,
                Project.name == COMBINED_PROJECT_NAME,
                TestDefinition.name == COMBINED_TEST_NAME,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return project_count == 1 and test_count == 1 and marker_exists is not None


async def _remove_old_public_demo_tests(session, organization_id: uuid.UUID) -> None:
    """Yalnizca public demo'nun eski proje/test/rapor ve analiz verilerini siler.

    Project DB cascade'i test, varyant, run, rapor, persona ve AI pipeline
    satirlarini temizler. Chip defteri, rezervasyonlar ve entitlement gecmisi
    audit amaciyla korunur.
    """

    await session.execute(delete(TestWizardDraft).where(TestWizardDraft.organization_id == organization_id))
    await session.execute(delete(Project).where(Project.organization_id == organization_id))
    await session.execute(delete(PageAnalysis).where(PageAnalysis.organization_id == organization_id))
    await session.flush()


async def _upgrade_existing_combined_demo_heatmap(
    session, organization_id: uuid.UUID
) -> bool:
    """Mevcut tek demo raporunu yeni AI tiklama tahminiyle yerinde yukseltir.

    Yeni proje/test/rapor olusturmaz. Basarili mevcut baseline run'ina modulu
    ekler ve worker'in normal OpenAI + Chip yasam dongusunu kullanabilmesi icin
    ayri, idempotent rezervasyonu baglar. Tekrar calistirmak ek ucret veya ikinci
    is uretmez.
    """

    rows = (
        await session.execute(
            select(SimulationRun, TestVariant)
            .join(TestVariant, TestVariant.id == SimulationRun.test_variant_id)
            .join(TestDefinition, TestDefinition.id == TestVariant.test_definition_id)
            .join(Project, Project.id == TestDefinition.project_id)
            .where(
                Project.organization_id == organization_id,
                Project.name == COMBINED_PROJECT_NAME,
                TestDefinition.name == COMBINED_TEST_NAME,
                SimulationRun.status == SimulationStatus.SUCCEEDED,
            )
            .order_by(SimulationRun.id)
        )
    ).all()
    if not rows:
        return False

    launch_run_id = rows[0][0].launch_run_id or rows[0][0].id
    reservation = await chip_ledger.reserve_chips(
        session,
        organization_id,
        AI_INTERACTION_HEATMAP_CHIP_COST,
        "Canli demo mevcut rapor AI etkilesim isi haritasi rezervasyonu",
        run_id=launch_run_id,
        idempotency_key=COMBINED_HEATMAP_RESERVATION_KEY,
        ttl_seconds=0,
    )

    changed = False
    for run, variant in rows:
        snapshot = dict(run.input_snapshot or {})
        modules = list(snapshot.get("modules") or [])
        if AI_INTERACTION_HEATMAP_MODULE_KEY not in modules:
            modules.append(AI_INTERACTION_HEATMAP_MODULE_KEY)
            snapshot["modules"] = modules
            run.input_snapshot = snapshot
            changed = True

        config = dict(variant.config or {})
        variant_modules = list(config.get("modules") or [])
        if AI_INTERACTION_HEATMAP_MODULE_KEY not in variant_modules:
            variant_modules.append(AI_INTERACTION_HEATMAP_MODULE_KEY)
            config["modules"] = variant_modules
            variant.config = config
            changed = True

        if run.heatmap_chip_reservation_id != reservation.id:
            run.heatmap_chip_reservation_id = reservation.id
            changed = True

    await session.flush()
    return changed


async def ensure_single_combined_demo_test(*, email: str) -> None:
    """Tek proje/tek test durumunu idempotent olarak kurar ve testi kuyruga alir."""

    async with async_session_maker() as session:
        organization = (
            await session.execute(select(Organization).where(Organization.slug == PUBLIC_DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        user = (
            await session.execute(select(User).where(User.email_normalized == email.strip().lower()))
        ).scalar_one_or_none()
        if organization is None or user is None:
            raise RuntimeError("public demo organizasyonu veya kullanicisi bulunamadi")

        if await _combined_demo_already_ready(session, organization.id):
            upgraded = await _upgrade_existing_combined_demo_heatmap(session, organization.id)
            await session.commit()
            logger.info(
                "public demo: tek birlesik test zaten mevcut (AI isi haritasi yukseltildi=%s)",
                upgraded,
            )
            return

        await _remove_old_public_demo_tests(session, organization.id)
        await chip_ledger.credit(
            session,
            organization.id,
            COMBINED_DEMO_CREDIT,
            reason="Tek proje/tek birlesik canli demo testi kredisi",
            idempotency_key=COMBINED_DEMO_CREDIT_KEY,
        )

        project = Project(
            organization_id=organization.id,
            name=COMBINED_PROJECT_NAME,
            description=(
                "Synthetix UX sitesinin tüm analiz modüllerini ve Terra AI raporunu "
                "tek bir paylaşılabilir sonuçta gösteren salt-okunur canlı demo."
            ),
        )
        session.add(project)
        await session.flush()

        draft = TestWizardDraft(
            organization_id=organization.id,
            created_by_user_id=user.id,
            status=TestWizardDraftStatus.DRAFT,
            current_step=5,
            payload=_combined_demo_payload(project.id),
        )
        session.add(draft)
        await session.flush()

        result = await wizard_service.launch_draft(
            session,
            organization_id=organization.id,
            requested_by_user_id=user.id,
            draft=draft,
        )
        await session.commit()
        logger.info(
            "public demo: tek birlesik test kuyruga alindi (test=%s, run=%s)",
            result.test_definition_id,
            ",".join(str(run_id) for run_id in result.simulation_run_ids),
        )


async def _main() -> None:
    email = (settings.demo_account_email or "").strip()
    password_secret = settings.demo_account_password
    if not email or password_secret is None:
        raise SystemExit("DEMO_ACCOUNT_EMAIL ve DEMO_ACCOUNT_PASSWORD gerekli")

    password = password_secret.get_secret_value()
    if len(password) < 16:
        raise SystemExit("DEMO_ACCOUNT_PASSWORD en az 16 karakter olmali")

    await seed_public_demo(email=email, password=password)
    await ensure_single_combined_demo_test(email=email)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
