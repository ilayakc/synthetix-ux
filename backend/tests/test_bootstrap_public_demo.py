"""Public demo'nun tek proje/tek kapsamli test kurulumuna ait guvenlik testleri."""

from sqlalchemy import func, select

from app.bootstrap_public_demo import (
    COMBINED_HEATMAP_RESERVATION_KEY,
    COMBINED_MODULES,
    COMBINED_PROJECT_NAME,
    COMBINED_TEST_NAME,
    _combined_demo_already_ready,
    _combined_demo_payload,
    _remove_old_public_demo_tests,
    _upgrade_existing_combined_demo_heatmap,
)
from app.models.billing import ChipLedgerEntry, ChipReservation
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind
from app.models.projects import Project
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.test_wizard import TestWizardDraft
from app.models.tests import TestDefinition, TestVariant
from app.services import chip_ledger
from app.services import test_wizard as wizard_service
from app.services.pricing import AI_INTERACTION_HEATMAP_MODULE_KEY


def test_combined_payload_launches_every_demo_analysis_module():
    import uuid

    payload = _combined_demo_payload(uuid.uuid4())

    assert payload["modules"] == list(COMBINED_MODULES)
    assert payload["persona_count"] == 100
    assert payload["current_source_type"] == wizard_service.SOURCE_TYPE_URL
    assert payload["authorization_confirmed"] is True
    assert len(payload["target_task"]) > 100
    assert len(payload["test_description"]) > 150
    assert wizard_service.missing_fields_for_launch(payload) == []
    assert AI_INTERACTION_HEATMAP_MODULE_KEY in payload["modules"]


async def test_ready_requires_exactly_one_marker_project_and_test(session, organization):
    project = Project(organization_id=organization.id, name=COMBINED_PROJECT_NAME)
    session.add(project)
    await session.flush()
    session.add(
        TestDefinition(
            organization_id=organization.id,
            project_id=project.id,
            name=COMBINED_TEST_NAME,
        )
    )
    await session.flush()

    assert await _combined_demo_already_ready(session, organization.id) is True

    session.add(Project(organization_id=organization.id, name="Eski demo projesi"))
    await session.flush()

    assert await _combined_demo_already_ready(session, organization.id) is False


async def test_cleanup_is_scoped_and_preserves_chip_audit(
    session,
    make_organization,
    make_user,
):
    public_org = await make_organization(name="Public Demo")
    other_org = await make_organization(name="Baska Organizasyon")
    public_user = await make_user(public_org)

    public_project = Project(organization_id=public_org.id, name="Eski public demo")
    other_project = Project(organization_id=other_org.id, name="Korunacak proje")
    session.add_all((public_project, other_project))
    await session.flush()

    public_test = TestDefinition(
        organization_id=public_org.id,
        project_id=public_project.id,
        name="Eski test",
    )
    public_draft = TestWizardDraft(
        organization_id=public_org.id,
        created_by_user_id=public_user.id,
        payload={"project_id": str(public_project.id)},
    )
    public_analysis = PageAnalysis(
        organization_id=public_org.id,
        requested_by_user_id=public_user.id,
        source_kind=PageAnalysisSourceKind.URL,
        url="https://example.com/",
        authorization_confirmed=True,
    )
    session.add_all((public_test, public_draft, public_analysis))
    await chip_ledger.credit(
        session,
        public_org.id,
        25,
        reason="Korunacak audit kaydi",
        idempotency_key="preserved-audit-entry",
    )
    await session.flush()

    await _remove_old_public_demo_tests(session, public_org.id)

    public_project_count = await session.scalar(
        select(func.count(Project.id)).where(Project.organization_id == public_org.id)
    )
    public_draft_count = await session.scalar(
        select(func.count(TestWizardDraft.id)).where(TestWizardDraft.organization_id == public_org.id)
    )
    public_analysis_count = await session.scalar(
        select(func.count(PageAnalysis.id)).where(PageAnalysis.organization_id == public_org.id)
    )
    audit_count = await session.scalar(
        select(func.count(ChipLedgerEntry.id)).where(ChipLedgerEntry.organization_id == public_org.id)
    )
    other_project_count = await session.scalar(
        select(func.count(Project.id)).where(Project.organization_id == other_org.id)
    )

    assert public_project_count == 0
    assert public_draft_count == 0
    assert public_analysis_count == 0
    assert audit_count == 1
    assert other_project_count == 1


async def test_existing_combined_demo_is_upgraded_in_place_and_idempotent(session, organization):
    project = Project(organization_id=organization.id, name=COMBINED_PROJECT_NAME)
    session.add(project)
    await session.flush()
    definition = TestDefinition(
        organization_id=organization.id,
        project_id=project.id,
        name=COMBINED_TEST_NAME,
    )
    session.add(definition)
    await session.flush()
    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Tasarim",
        config={"modules": ["ai_report"]},
    )
    session.add(variant)
    await session.flush()
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.SUCCEEDED,
        deterministic_seed=1,
        model_version="engine",
        input_snapshot={"modules": ["ai_report"], "target_task": "Hesap olustur"},
    )
    session.add(run)
    await chip_ledger.credit(
        session,
        organization.id,
        100,
        reason="Demo yukseltme testi",
        idempotency_key="demo-upgrade-test-credit",
    )
    await session.flush()

    assert await _upgrade_existing_combined_demo_heatmap(session, organization.id) is True
    await session.refresh(run)
    await session.refresh(variant)
    reservation_id = run.heatmap_chip_reservation_id

    assert reservation_id is not None
    assert AI_INTERACTION_HEATMAP_MODULE_KEY in run.input_snapshot["modules"]
    assert AI_INTERACTION_HEATMAP_MODULE_KEY in variant.config["modules"]
    assert await _upgrade_existing_combined_demo_heatmap(session, organization.id) is False
    assert (
        await session.scalar(
            select(func.count(ChipReservation.id)).where(
                ChipReservation.organization_id == organization.id,
                ChipReservation.idempotency_key == COMBINED_HEATMAP_RESERVATION_KEY,
            )
        )
        == 1
    )
