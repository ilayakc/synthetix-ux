"""Kalibrasyon gozlemi API'si icin testler: acik riza zorunlulugu, en az bir
metrik zorunlulugu, yalnizca SUCCEEDED run'a ekleme, tenant izolasyonu.

Router uc noktalari HTTP uzerinden degil, dogrudan (Depends varsayilanlarini
atlayarak) cagrilir - bkz. test_reports.py ayni desen icin.
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import Principal
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization, User
from app.models.tests import TestDefinition, TestVariant
from app.routers import simulations as simulations_router
from app.services import simulation_worker

pytestmark = pytest.mark.integration


async def _make_project_and_variant(
    session: AsyncSession, organization: Organization
) -> TestVariant:
    from app.models.projects import Project

    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()

    definition = TestDefinition(
        organization_id=organization.id,
        project_id=project.id,
        name=f"Test tanimi {uuid.uuid4().hex[:6]}",
    )
    session.add(definition)
    await session.flush()

    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Ana Senaryo",
        config={"role": "primary", "url": "https://example.com/anasayfa"},
    )
    session.add(variant)
    await session.flush()
    return variant


async def _make_succeeded_run(
    session: AsyncSession, organization: Organization, variant: TestVariant, *, seed: int = 42
) -> SimulationRun:
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.RUNNING,
        deterministic_seed=seed,
        model_version="pending-engine",
        input_snapshot={
            "wizard_test_type": "existing_site_basic_ux",
            "persona_count": 200,
            "target_audience": "Yeni B2B musteri adaylari",
            "modules": [],
            "url": "https://example.com/anasayfa",
            "role": "primary",
            "pricing_version": "2026.1",
        },
    )
    session.add(run)
    await session.flush()
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.SUCCEEDED
    return run


async def _make_principal(
    session: AsyncSession, organization_id: uuid.UUID, *, role: str = "analyst"
) -> Principal:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        email_normalized=f"{uuid.uuid4().hex[:8]}@example.test",
        password_hash="not-a-real-hash",
    )
    session.add(user)
    await session.flush()
    return Principal(user_id=user.id, organization_id=organization_id, role=role)


async def test_create_observation_requires_consent(session: AsyncSession, organization: Organization):
    variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run(session, organization, variant)

    body = simulations_router.CalibrationObservationCreateRequest(
        consent_confirmed=False,
        real_task_completion_rate=0.7,
    )
    principal = await _make_principal(session, organization.id)
    with pytest.raises(HTTPException) as exc_info:
        await simulations_router.create_calibration_observation(
            run.id, body, principal=principal, session=session
        )
    assert exc_info.value.status_code == 422
    assert "riza" in exc_info.value.detail.lower()


async def test_create_observation_requires_at_least_one_metric(
    session: AsyncSession, organization: Organization
):
    variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run(session, organization, variant)

    body = simulations_router.CalibrationObservationCreateRequest(consent_confirmed=True)
    principal = await _make_principal(session, organization.id)
    with pytest.raises(HTTPException) as exc_info:
        await simulations_router.create_calibration_observation(
            run.id, body, principal=principal, session=session
        )
    assert exc_info.value.status_code == 422


async def test_create_observation_rejects_non_succeeded_run(
    session: AsyncSession, organization: Organization
):
    variant = await _make_project_and_variant(session, organization)
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.QUEUED,
        deterministic_seed=1,
        model_version="pending-engine",
        input_snapshot={},
    )
    session.add(run)
    await session.flush()

    body = simulations_router.CalibrationObservationCreateRequest(
        consent_confirmed=True, real_task_completion_rate=0.5
    )
    principal = await _make_principal(session, organization.id)
    with pytest.raises(HTTPException) as exc_info:
        await simulations_router.create_calibration_observation(
            run.id, body, principal=principal, session=session
        )
    assert exc_info.value.status_code == 409


async def test_create_and_list_observation_roundtrip(session: AsyncSession, organization: Organization):
    variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run(session, organization, variant)

    body = simulations_router.CalibrationObservationCreateRequest(
        consent_confirmed=True,
        real_task_completion_rate=0.62,
        real_median_task_duration_seconds=48.5,
        sample_size=12,
        source_note="Gonullu kullanilabilirlik testi, 12 katilimci",
    )
    principal = await _make_principal(session, organization.id)
    created = await simulations_router.create_calibration_observation(
        run.id, body, principal=principal, session=session
    )
    assert created.simulation_run_id == run.id
    assert created.real_task_completion_rate == pytest.approx(0.62)
    assert created.sample_size == 12

    listed = await simulations_router.list_calibration_observations(
        run.id, organization_id=organization.id, session=session
    )
    assert [o.id for o in listed] == [created.id]


@pytest.mark.security
async def test_create_observation_cross_tenant_run_raises_not_found(
    session: AsyncSession, organization: Organization
):
    other_org = Organization(name="Other Org", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run(session, organization, variant)

    body = simulations_router.CalibrationObservationCreateRequest(
        consent_confirmed=True, real_task_completion_rate=0.5
    )
    principal = await _make_principal(session, other_org.id)
    with pytest.raises(HTTPException) as exc_info:
        await simulations_router.create_calibration_observation(
            run.id, body, principal=principal, session=session
        )
    assert exc_info.value.status_code == 404
