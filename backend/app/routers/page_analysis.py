import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.page_analysis import PageAnalysis
from app.services import page_analysis as page_analysis_service
from app.services.exceptions import PageAnalysisNotFoundError, UnauthorizedPageAnalysisError
from app.services.url_safety import UnsafeUrlError

router = APIRouter(prefix="/api/page-analyses", tags=["page-analysis"])

# URL analizi baslatma: viewer haric tum roller (bkz.
# docs/architecture.md#roller-ve-yetkiler, "Proje/test/simulasyon olustur").
WRITE_ROLES = ("analyst", "admin", "owner")


class CreateAnalysisRequest(BaseModel):
    url: str
    authorization_confirmed: bool = False


class AnalysisResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    url: str
    status: str
    attempt_count: int
    error: str | None
    snapshot_version: str | None
    analyzer_version: str | None
    source: str | None
    features: dict | None
    has_screenshot: bool
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _to_response(analysis: PageAnalysis) -> AnalysisResponse:
    return AnalysisResponse(
        id=analysis.id,
        organization_id=analysis.organization_id,
        url=analysis.url,
        status=analysis.status.value,
        attempt_count=analysis.attempt_count,
        error=analysis.error,
        snapshot_version=analysis.snapshot_version,
        analyzer_version=analysis.analyzer_version,
        source=analysis.source,
        features=analysis.features,
        has_screenshot=analysis.screenshot_data is not None,
        started_at=analysis.started_at,
        finished_at=analysis.finished_at,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


@router.post("", response_model=AnalysisResponse, status_code=201)
async def create_analysis(
    body: CreateAnalysisRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> AnalysisResponse:
    """Bir URL icin pasif, SSRF-guvenli analiz isi olusturur ve kuyruga alir.

    Is, worker tarafindan (bkz. app.services.page_analysis) asenkron olarak
    islenir; sonuc `GET /{id}` ile sorgulanir. Kullanicinin bu URL'yi analiz
    etme yetkisini acikca onaylamis olmasi (`authorization_confirmed=true`)
    zorunludur.
    """

    try:
        analysis = await page_analysis_service.create_analysis(
            session,
            organization_id=principal.organization_id,
            requested_by_user_id=principal.user_id,
            url=body.url,
            authorization_confirmed=body.authorization_confirmed,
        )
    except UnauthorizedPageAnalysisError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc

    await session.commit()
    await session.refresh(analysis)
    return _to_response(analysis)


@router.get("/{analysis_id}", response_model=AnalysisResponse)
async def get_analysis(
    analysis_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> AnalysisResponse:
    """Analiz isinin durumunu/sonucunu dondurur (polling icin)."""

    try:
        analysis = await page_analysis_service.get_owned_analysis(session, organization_id, analysis_id)
    except PageAnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Analiz bulunamadi") from exc

    await session.commit()
    return _to_response(analysis)


@router.get("/{analysis_id}/screenshot")
async def get_screenshot(
    analysis_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Viewport ekran goruntusunu (PNG) dondurur; saklama suresi dolmussa 404 doner."""

    try:
        analysis = await page_analysis_service.get_owned_analysis(session, organization_id, analysis_id)
    except PageAnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Analiz bulunamadi") from exc

    await session.commit()

    if analysis.screenshot_data is None:
        raise HTTPException(status_code=404, detail="Ekran goruntusu mevcut degil veya saklama suresi doldu")

    return Response(content=analysis.screenshot_data, media_type="image/png")
