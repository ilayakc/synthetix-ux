import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.design_assets import DesignAsset
from app.models.design_generation import DesignGenerationJob
from app.services import design_assets as design_assets_service
from app.services import design_generation as design_generation_service
from app.services.exceptions import (
    DesignGenerationJobNotFoundError,
    ImageGenerationNotConfiguredError,
    ImageGenerationRequestError,
    InvalidDesignGenerationStateError,
)

router = APIRouter(prefix="/api/design-generations", tags=["design-generations"])

# Is olusturma/iptal/silme: viewer haric tum roller (bkz. app.routers.design_assets
# ile ayni yetki deseni - docs/architecture.md#roller-ve-yetkiler).
WRITE_ROLES = ("analyst", "admin", "owner")

PROVIDER_DISABLED_MESSAGE = (
    "AI görsel üretim sağlayıcısı henüz yapılandırılmadı. Tasarım B için URL veya "
    "ekran görüntüsü kullanabilirsiniz."
)


class AvailabilityResponse(BaseModel):
    available: bool
    provider_name: str | None
    max_prompt_length: int
    disabled_reason: str | None


@router.get("/availability", response_model=AvailabilityResponse)
async def get_availability(
    organization_id: uuid.UUID = Depends(get_organization_id),
) -> AvailabilityResponse:
    """Frontend'in "AI ile olustur" secenegini gosterip gostermeyecegine karar
    vermesi icin tek dogruluk kaynagi. API anahtarini veya uc nokta URL'sini
    ASLA dondurmez."""

    available = design_generation_service.is_provider_configured()
    return AvailabilityResponse(
        available=available,
        provider_name=settings.image_generation_model_name if available else None,
        max_prompt_length=settings.image_generation_max_prompt_length,
        disabled_reason=None if available else PROVIDER_DISABLED_MESSAGE,
    )


class DesignGenerationResultAsset(BaseModel):
    id: uuid.UUID
    content_type: str
    width: int
    height: int
    expires_at: datetime | None


class DesignGenerationResponse(BaseModel):
    id: uuid.UUID
    status: str
    provider: str
    model_name: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result_asset: DesignGenerationResultAsset | None
    error_message: str | None


async def _to_response(session: AsyncSession, job: DesignGenerationJob) -> DesignGenerationResponse:
    result_asset: DesignGenerationResultAsset | None = None
    if job.result_asset_id is not None:
        asset = await session.get(DesignAsset, job.result_asset_id)
        if asset is not None:
            result_asset = DesignGenerationResultAsset(
                id=asset.id,
                content_type=asset.content_type.value,
                width=asset.width,
                height=asset.height,
                expires_at=asset.expires_at,
            )
    return DesignGenerationResponse(
        id=job.id,
        status=job.status.value,
        provider=job.provider,
        model_name=job.model_name,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result_asset=result_asset,
        error_message=job.error_message if job.status.value == "failed" else None,
    )


class CreateDesignGenerationRequest(BaseModel):
    source_asset_id: uuid.UUID
    prompt: str
    authorization_confirmed: bool = False


@router.post("", response_model=DesignGenerationResponse, status_code=202)
async def create_design_generation(
    body: CreateDesignGenerationRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DesignGenerationResponse:
    """Yeni bir AI tasarim uretim isi olusturur (202: kuyruga alinir, senkron
    islenmez - bkz. app.services.design_generation)."""

    try:
        job = await design_generation_service.create_generation_job(
            session,
            organization_id=principal.organization_id,
            user_id=principal.user_id,
            source_asset_id=body.source_asset_id,
            prompt=body.prompt,
            authorization_confirmed=body.authorization_confirmed,
        )
    except ImageGenerationNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ImageGenerationRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(job)
    return await _to_response(session, job)


async def _get_owned_job_or_404(
    session: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
) -> DesignGenerationJob:
    try:
        return await design_generation_service.get_owned_job(session, organization_id, job_id)
    except DesignGenerationJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Uretim isi bulunamadi") from exc


@router.get("/{job_id}", response_model=DesignGenerationResponse)
async def get_design_generation(
    job_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> DesignGenerationResponse:
    job = await _get_owned_job_or_404(session, organization_id, job_id)
    await session.commit()
    return await _to_response(session, job)


@router.post("/{job_id}/cancel", response_model=DesignGenerationResponse)
async def cancel_design_generation(
    job_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DesignGenerationResponse:
    await _get_owned_job_or_404(session, principal.organization_id, job_id)
    try:
        job = await design_generation_service.cancel_job(session, principal.organization_id, job_id)
    except InvalidDesignGenerationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    return await _to_response(session, job)


@router.delete("/{job_id}", status_code=204)
async def delete_design_generation(
    job_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _get_owned_job_or_404(session, principal.organization_id, job_id)
    await design_generation_service.delete_job(session, principal.organization_id, job_id)
    await session.commit()
