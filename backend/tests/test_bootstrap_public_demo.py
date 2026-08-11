"""Public demo'nun tek proje/tek kapsamli test kurulumuna ait guvenlik testleri."""

from sqlalchemy import func, select

from app.bootstrap_public_demo import (
    COMBINED_MODULES,
    COMBINED_PROJECT_NAME,
    COMBINED_TEST_NAME,
    _combined_demo_already_ready,
    _combined_demo_payload,
    _remove_old_public_demo_tests,
)
from app.models.billing import ChipLedgerEntry
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind
from app.models.projects import Project
from app.models.test_wizard import TestWizardDraft
from app.models.tests import TestDefinition
from app.services import chip_ledger
from app.services import test_wizard as wizard_service


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
