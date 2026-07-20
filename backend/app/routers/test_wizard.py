import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.test_wizard import TestWizardDraft, TestWizardDraftStatus
from app.services import settings as settings_service
from app.services import test_wizard as wizard_service
from app.services.exceptions import InsufficientChipBalanceError

router = APIRouter(prefix="/api/tests/drafts", tags=["test-wizard"])

# Taslak olusturma/guncelleme/baslatma: viewer haric tum roller (bkz.
# docs/architecture.md#roller-ve-yetkiler, "Proje/test/simulasyon olustur").
WRITE_ROLES = ("analyst", "admin", "owner")


class DraftResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    current_step: int
    payload: dict
    missing_fields: list[str]
    created_at: datetime
    updated_at: datetime


def _to_response(draft: TestWizardDraft) -> DraftResponse:
    return DraftResponse(
        id=draft.id,
        organization_id=draft.organization_id,
        status=draft.status.value,
        current_step=draft.current_step,
        payload=draft.payload,
        missing_fields=wizard_service.missing_fields_for_launch(draft.payload),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


async def _get_owned_draft(
    session: AsyncSession, organization_id: uuid.UUID, draft_id: uuid.UUID, *, for_update: bool = False
) -> TestWizardDraft:
    query = select(TestWizardDraft).where(TestWizardDraft.id == draft_id)
    if for_update:
        query = query.with_for_update()
    result = await session.execute(query)
    draft = result.scalar_one_or_none()
    if draft is None or draft.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Taslak bulunamadi")
    return draft


@router.post("", response_model=DraftResponse, status_code=201)
async def create_draft(
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Bos bir sihirbaz taslagi olusturur. Adim verileri sonraki PATCH cagrilariyla doldurulur."""

    initial_payload = await settings_service.build_initial_wizard_payload(session, principal.organization_id)
    draft = TestWizardDraft(
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        status=TestWizardDraftStatus.DRAFT,
        current_step=1,
        payload=initial_payload,
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)

    return _to_response(draft)


@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Taslagi dondurur; sayfa yenilenince veya geri donulunce kaldigi yerden devam edilebilmesi icindir."""

    draft = await _get_owned_draft(session, organization_id, draft_id)
    await session.commit()
    return _to_response(draft)


class PatchDraftRequest(BaseModel):
    current_step: int | None = None
    payload: dict = {}


@router.patch("/{draft_id}", response_model=DraftResponse)
async def patch_draft(
    draft_id: uuid.UUID,
    body: PatchDraftRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Taslagin adim verilerini kismi olarak gunceller (yalnizca gonderilen alanlar dogrulanip birlestirilir)."""

    draft = await _get_owned_draft(session, principal.organization_id, draft_id, for_update=True)
    if draft.status == TestWizardDraftStatus.LAUNCHED:
        raise HTTPException(status_code=409, detail="Baslatilmis bir taslak degistirilemez")

    try:
        wizard_service.validate_patch_fields(body.payload)
    except wizard_service.DraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    draft.payload = wizard_service.merge_payload(draft.payload, body.payload)
    if body.current_step is not None:
        if not (1 <= body.current_step <= 5):
            raise HTTPException(status_code=400, detail="current_step 1 ile 5 arasinda olmalidir")
        draft.current_step = body.current_step

    await session.commit()
    await session.refresh(draft)

    return _to_response(draft)


class LaunchResponse(BaseModel):
    draft_id: uuid.UUID
    status: str
    test_definition_id: uuid.UUID
    simulation_run_ids: list[uuid.UUID]
    used_free_entitlement: bool
    reserved_chips: int
    engine_status_message: str


@router.post("/{draft_id}/launch", response_model=LaunchResponse)
async def launch_draft(
    draft_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> LaunchResponse:
    """Taslagi baslatir. Idempotenttir: cift tiklama/tekrar deneme ayni sonucu dondurur.

    `SELECT ... FOR UPDATE` ile taslak satiri kilitlenerek yarisan istekler
    serilestirilir; boylece iki es zamanli baslatma denemesi hicbir zaman
    entitlement/Chip'i iki kez rezerve edemez veya iki kez calistirma
    olusturamaz (bkz. `app.services.test_wizard.launch_draft`).
    """

    draft = await _get_owned_draft(session, principal.organization_id, draft_id, for_update=True)

    try:
        result = await wizard_service.launch_draft(
            session, organization_id=principal.organization_id, draft=draft
        )
    except wizard_service.DraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InsufficientChipBalanceError as exc:
        raise HTTPException(
            status_code=402,
            detail=(
                "Yetersiz Chip bakiyesi: bu testi baslatmak icin gereken Chip miktari "
                f"mevcut bakiyenizi asiyor ({exc})."
            ),
        ) from exc

    await session.commit()

    return LaunchResponse(
        draft_id=draft.id,
        status=draft.status.value,
        test_definition_id=result.test_definition_id,
        simulation_run_ids=list(result.simulation_run_ids),
        used_free_entitlement=result.used_free_entitlement,
        reserved_chips=result.reserved_chips,
        engine_status_message=result.engine_status_message,
    )
