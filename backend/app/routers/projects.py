import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.projects import Project, ProjectStatus
from app.models.reports import Report
from app.models.simulations import SimulationRun
from app.models.test_wizard import TestWizardDraft, TestWizardDraftStatus
from app.models.tests import TestDefinition, TestVariant

router = APIRouter(prefix="/api/projects", tags=["projects"])

# Proje olusturma/guncelleme/arsivleme: viewer haric tum roller (bkz.
# docs/architecture.md#roller-ve-yetkiler, "Proje/test/simulasyon olustur").
WRITE_ROLES = ("analyst", "admin", "owner")


class ProjectResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    status: str
    test_count: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


def _to_response(project: Project, test_count: int) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        organization_id=project.organization_id,
        name=project.name,
        description=project.description,
        status=project.status.value,
        test_count=test_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
        archived_at=project.archived_at,
    )


async def _get_test_counts(
    session: AsyncSession, organization_id: uuid.UUID, project_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not project_ids:
        return {}
    result = await session.execute(
        select(TestDefinition.project_id, func.count(TestDefinition.id))
        .where(
            TestDefinition.organization_id == organization_id,
            TestDefinition.project_id.in_(project_ids),
        )
        .group_by(TestDefinition.project_id)
    )
    return {project_id: count for project_id, count in result.all()}


async def _get_owned_project(
    session: AsyncSession, organization_id: uuid.UUID, project_id: uuid.UUID
) -> Project:
    """Projeyi yalnizca cagiran organizasyona aitse dondurur (aksi halde 404).

    Baska bir organizasyonun proje kimligi denenirse 403 yerine 404
    dondurulur; boylece bir kimligin varligi/yoklugu (tenant izolasyonu)
    disariya sizdirilmaz.
    """

    project = await session.get(Project, project_id)
    if project is None or project.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Proje bulunamadi")
    return project


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    include_archived: bool = False,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[ProjectResponse]:
    """Organizasyonun projelerini (varsayilan olarak yalnizca aktif olanlari) listeler."""

    query = select(Project).where(Project.organization_id == organization_id)
    if not include_archived:
        query = query.where(Project.status == ProjectStatus.ACTIVE)
    query = query.order_by(Project.created_at.desc())

    result = await session.execute(query)
    projects = result.scalars().all()

    counts = await _get_test_counts(session, organization_id, [p.id for p in projects])
    await session.commit()

    return [_to_response(project, counts.get(project.id, 0)) for project in projects]


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    existing = await session.execute(
        select(Project).where(
            Project.organization_id == principal.organization_id,
            Project.name == body.name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Bu isimde bir proje zaten var")

    project = Project(
        organization_id=principal.organization_id,
        name=body.name,
        description=body.description,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)

    return _to_response(project, test_count=0)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project = await _get_owned_project(session, organization_id, project_id)
    counts = await _get_test_counts(session, organization_id, [project.id])
    await session.commit()

    return _to_response(project, counts.get(project.id, 0))


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    body: UpdateProjectRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project = await _get_owned_project(session, principal.organization_id, project_id)
    if project.status == ProjectStatus.ARCHIVED:
        raise HTTPException(status_code=409, detail="Arsivlenmis bir proje guncellenemez")

    if body.name is not None and body.name != project.name:
        existing = await session.execute(
            select(Project).where(
                Project.organization_id == principal.organization_id,
                Project.name == body.name,
                Project.id != project.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Bu isimde bir proje zaten var")
        project.name = body.name

    if body.description is not None:
        project.description = body.description

    await session.commit()
    await session.refresh(project)

    counts = await _get_test_counts(session, principal.organization_id, [project.id])
    return _to_response(project, counts.get(project.id, 0))


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    """Projeyi arsivler (fiziksel silme yoktur). Idempotenttir: zaten arsivliyse ayni sonucu dondurur."""

    project = await _get_owned_project(session, principal.organization_id, project_id)

    if project.status != ProjectStatus.ARCHIVED:
        project.status = ProjectStatus.ARCHIVED
        project.archived_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(project)

    counts = await _get_test_counts(session, principal.organization_id, [project.id])
    return _to_response(project, counts.get(project.id, 0))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Tamamlanmis raporu olmayan projeyi aktif listeden kaldirir.

    Proje fiziksel olarak silinmez; olasi baslatilmis calistirmalar ve Chip
    denetim izi korunarak arsivlenir. Yalnizca henuz baslatilmamis sihirbaz
    taslaklari kalici olarak temizlenir. Tamamlanmis raporu olan projeler
    hicbir kosulda bu islemle kaldirilamaz.
    """

    project = await _get_owned_project(session, principal.organization_id, project_id)

    completed_report = await session.execute(
        select(Report.id)
        .join(SimulationRun, SimulationRun.id == Report.simulation_run_id)
        .join(TestVariant, TestVariant.id == SimulationRun.test_variant_id)
        .join(TestDefinition, TestDefinition.id == TestVariant.test_definition_id)
        .where(
            Report.organization_id == principal.organization_id,
            TestDefinition.project_id == project.id,
        )
        .limit(1)
    )
    if completed_report.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Tamamlanmis raporu bulunan proje silinemez",
        )

    draft_result = await session.execute(
        select(TestWizardDraft).where(
            TestWizardDraft.organization_id == principal.organization_id,
            TestWizardDraft.status == TestWizardDraftStatus.DRAFT,
        )
    )
    for draft in draft_result.scalars().all():
        if str((draft.payload or {}).get("project_id", "")) == str(project.id):
            await session.delete(draft)

    project.status = ProjectStatus.ARCHIVED
    project.archived_at = project.archived_at or datetime.now(UTC)
    await session.commit()
