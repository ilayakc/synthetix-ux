import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind
from app.services import page_analysis as page_analysis_service
from app.services.exceptions import (
    DesignAssetNotFoundError,
    DesignAssetUnavailableError,
    PageAnalysisNotFoundError,
    PageAnalysisSourceConflictError,
    UnauthorizedPageAnalysisError,
)
from app.services.url_safety import UnsafeUrlError

router = APIRouter(prefix="/api/page-analyses", tags=["page-analysis"])

# Analiz baslatma: viewer haric tum roller (bkz.
# docs/architecture.md#roller-ve-yetkiler, "Proje/test/simulasyon olustur").
WRITE_ROLES = ("analyst", "admin", "owner")


class CreateAnalysisRequest(BaseModel):
    # Ortak kaynak sozlesmesi: `url` VE `design_asset_id`'den TAM OLARAK biri
    # gonderilmelidir (ikisi birden veya hicbiri -> 422). Eski `{url: ...}`
    # cagrilari (source_kind/design_asset_id olmadan) aynen calismaya devam
    # eder - geriye donuk uyumluluk.
    url: str | None = None
    design_asset_id: uuid.UUID | None = None
    authorization_confirmed: bool = False
    # Istemci isterse gonderebilir; yalnizca gonderilen kaynak alaniyla
    # (url/design_asset_id) tutarli olmasi zorunludur - `organization_id` gibi
    # kaynak/tenant belirleyici hicbir alan istemciden kabul edilmez, tenant
    # her zaman authenticated principal'dan turetilir (bkz. get_organization_id).
    source_kind: str | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> "CreateAnalysisRequest":
        has_url = self.url is not None
        has_asset = self.design_asset_id is not None
        if has_url == has_asset:
            raise ValueError("Tam olarak bir kaynak belirtilmelidir: 'url' veya 'design_asset_id'")
        derived_kind = "url" if has_url else "design_asset"
        if self.source_kind is not None and self.source_kind != derived_kind:
            raise ValueError("'source_kind', gonderilen kaynak alaniyla ('url'/'design_asset_id') celisiyor")
        return self


class AnalysisResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    source_kind: str
    url: str | None
    design_asset_id: uuid.UUID | None
    # `source_kind='design_asset'` iken: kaynak DesignAsset satiri hala mevcutsa
    # `True`, kaynak satiri SONRADAN hard-delete edildiyse (bkz. FK
    # `ON DELETE SET NULL`, migration d1e4a8c2f6b9) `False`. Tamamlanmis bir
    # PageAnalysis'in kendi `screenshot_data` kopyasi bu durumdan ETKILENMEZ -
    # bu alan yalnizca bilgilendirme amaclidir, erisimi kisitlamaz.
    # `source_kind='url'` iken kavram gecerli olmadigi icin `None`'dir.
    design_asset_still_linked: bool | None
    status: str
    attempt_count: int
    error: str | None
    snapshot_version: str | None
    analyzer_version: str | None
    source: str | None
    features: dict | None
    has_screenshot: bool
    image_width: int | None
    image_height: int | None
    screenshot_content_type: str | None
    content_sha256: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _to_response(analysis: PageAnalysis) -> AnalysisResponse:
    design_asset_still_linked = (
        analysis.design_asset_id is not None if analysis.source_kind == PageAnalysisSourceKind.DESIGN_ASSET else None
    )
    return AnalysisResponse(
        id=analysis.id,
        organization_id=analysis.organization_id,
        source_kind=analysis.source_kind.value,
        url=analysis.url,
        design_asset_id=analysis.design_asset_id,
        design_asset_still_linked=design_asset_still_linked,
        status=analysis.status.value,
        attempt_count=analysis.attempt_count,
        error=analysis.error,
        snapshot_version=analysis.snapshot_version,
        analyzer_version=analysis.analyzer_version,
        source=analysis.source,
        features=analysis.features,
        has_screenshot=analysis.screenshot_data is not None,
        image_width=analysis.image_width,
        image_height=analysis.image_height,
        screenshot_content_type=analysis.screenshot_content_type,
        content_sha256=analysis.content_sha256,
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
    """Bir URL veya kullanicinin kendi organizasyonuna ait aktif bir DesignAsset
    icin pasif analiz isi olusturur ve kuyruga alir (ortak kaynak sozlesmesi -
    bkz. `CreateAnalysisRequest`).

    Is, worker tarafindan (bkz. app.services.page_analysis) asenkron olarak
    islenir; sonuc `GET /{id}` ile sorgulanir. URL kaynaginda, kullanicinin bu
    URL'yi analiz etme yetkisini acikca onaylamis olmasi
    (`authorization_confirmed=true`) zorunludur; DesignAsset kaynaginda bu
    kavram gecerli degildir (kullanici zaten kendi yukledigi gorseli analiz
    ediyor). Tenant kimligi HER ZAMAN authenticated principal'dan turetilir -
    istekten `organization_id` kabul edilmez.
    """

    try:
        analysis = await page_analysis_service.create_analysis(
            session,
            organization_id=principal.organization_id,
            requested_by_user_id=principal.user_id,
            url=body.url,
            design_asset_id=body.design_asset_id,
            authorization_confirmed=body.authorization_confirmed,
        )
    except UnauthorizedPageAnalysisError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from exc
    except PageAnalysisSourceConflictError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DesignAssetNotFoundError as exc:
        # Cross-tenant/var olmayan asset ayrimi yapilmaz - bilgi sizdirmayan
        # tek bir 404 (bkz. app.routers.design_assets ile ayni konvansiyon).
        raise HTTPException(status_code=404, detail="Tasarim gorseli bulunamadi") from exc
    except DesignAssetUnavailableError as exc:
        raise HTTPException(status_code=422, detail="Tasarim gorseli artik kullanilabilir durumda degil") from exc

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
    """Ekran goruntusunu (URL kaynaginda viewport, DesignAsset kaynaginda
    yuklenen tasarimin degismez kopyasi) dondurur; saklama suresi dolmussa
    404 doner. Kaynak turunden bagimsiz olarak ayni endpoint kullanilir -
    orijinal DesignAsset sonradan silinse/expire olsa bile, basariyla
    tamamlanmis bir PageAnalysis'in kendi `screenshot_data` kopyasi kendi
    retention suresi boyunca etkilenmez (bkz. app.services.page_analysis).
    """

    try:
        analysis = await page_analysis_service.get_owned_analysis(session, organization_id, analysis_id)
    except PageAnalysisNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Analiz bulunamadi") from exc

    await session.commit()

    if analysis.screenshot_data is None:
        raise HTTPException(status_code=404, detail="Ekran goruntusu mevcut degil veya saklama suresi doldu")

    media_type = analysis.screenshot_content_type or "image/png"
    extension = media_type.rsplit("/", 1)[-1] if "/" in media_type else "png"
    return Response(
        content=analysis.screenshot_data,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            # Kimlik dogrulama/tenant kontrolune tabi, kullaniciya ozel bir
            # gorsel: hicbir ara/paylasimli onbellek tarafindan saklanmamalidir
            # (bkz. app.routers.design_assets ile ayni konvansiyon).
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="page-analysis-screenshot.{extension}"',
        },
    )
