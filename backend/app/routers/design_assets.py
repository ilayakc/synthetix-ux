import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.design_assets import DesignAsset
from app.services import design_assets as design_assets_service
from app.services.exceptions import DesignAssetNotFoundError, ImageTooLargeError, InvalidImageError

router = APIRouter(prefix="/api/design-assets", tags=["design-assets"])

# Yukleme/silme: viewer haric tum roller (bkz. app.routers.page_analysis ile
# ayni yetki deseni - docs/architecture.md#roller-ve-yetkiler).
WRITE_ROLES = ("analyst", "admin", "owner")

MAX_UPLOAD_LABEL_LENGTH = 200

# Istek govdesi bu boyutta parcalar (chunk) halinde okunur; boylece limit
# asildiginda dosyanin TAMAMI onceden belege alinmadan okuma durdurulur
# (bkz. `_read_upload_within_limit`). Istemcinin gonderdigi `Content-Length`
# basligina TEK BASINA guvenilmez - o yalniz beyandir, gercek sinir kontrolu
# yalnizca fiilen okunan bayt sayisina gore yapilir.
_UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024


class DesignAssetResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    content_type: str
    byte_size: int
    width: int
    height: int
    label: str | None
    status: str
    has_image: bool
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _to_response(asset: DesignAsset) -> DesignAssetResponse:
    return DesignAssetResponse(
        id=asset.id,
        organization_id=asset.organization_id,
        content_type=asset.content_type.value,
        byte_size=asset.byte_size,
        width=asset.width,
        height=asset.height,
        label=asset.label,
        status=asset.status.value,
        has_image=asset.image_data is not None,
        expires_at=asset.expires_at,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


async def _read_upload_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    """`file`'i sinirli parcalar halinde okur; toplam boyut `max_bytes`'i asarsa
    okumayi HEMEN durdurup `ImageTooLargeError` firlatir.

    Istemcinin bildirdigi `Content-Length`'e guvenilmez (yalnistan/eksik
    olabilir); gercek sinir, fiilen akistan okunan bayt sayisi uzerinden
    uygulanir - bu sayede limitin cok uzerinde bir dosya, tamami belege
    alinmadan reddedilir. `finally` bloğu, erken red durumunda dahi
    `UploadFile`'in alttaki gecici dosya tanimlayicisinin kapatilmasini
    garanti eder.
    """

    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = await file.read(_UPLOAD_CHUNK_SIZE_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ImageTooLargeError(
                    f"Dosya boyutu izin verilen sinirin ({max_bytes} bayt) uzerinde"
                )
            chunks.append(chunk)
    finally:
        await file.close()
    return b"".join(chunks)


@router.post("", response_model=DesignAssetResponse, status_code=201)
async def upload_design_asset(
    file: UploadFile = File(...),
    label: str | None = Form(default=None),
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DesignAssetResponse:
    """Bir tasarim ekran goruntusu yukler.

    Dosya, Content-Type header'ina veya uzantisina bakilmaksizin gercekten
    decode edilerek dogrulanir (yalnizca PNG/JPEG/WebP kabul edilir, SVG
    reddedilir), EXIF/metadata temizlenir ve yeniden encode edilerek saklanir
    (bkz. app.services.design_assets). Kullanicinin gonderdigi orijinal dosya
    adi hicbir zaman saklanmaz. Istek govdesi, boyut siniri asilir asilmaz
    akisi durduran sinirli parcalar halinde okunur (bkz. `_read_upload_within_limit`).
    """

    if label is not None and len(label) > MAX_UPLOAD_LABEL_LENGTH:
        raise HTTPException(status_code=422, detail=f"'label' en fazla {MAX_UPLOAD_LABEL_LENGTH} karakter olabilir")

    try:
        raw_bytes = await _read_upload_within_limit(file, settings.design_asset_max_bytes)
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    if not raw_bytes:
        raise HTTPException(status_code=422, detail="Bos dosya yuklenemez")

    try:
        asset = await design_assets_service.upload_design_asset(
            session,
            organization_id=principal.organization_id,
            uploaded_by_user_id=principal.user_id,
            raw_bytes=raw_bytes,
            label=label,
        )
    except ImageTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(asset)
    return _to_response(asset)


@router.get("/{asset_id}", response_model=DesignAssetResponse)
async def get_design_asset(
    asset_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> DesignAssetResponse:
    """Bir tasarim gorselinin metadata'sini dondurur (ikili veri icermez)."""

    try:
        asset = await design_assets_service.get_owned_asset(session, organization_id, asset_id)
    except DesignAssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tasarim gorseli bulunamadi") from exc

    await session.commit()
    return _to_response(asset)


@router.get("/{asset_id}/preview")
async def get_design_asset_preview(
    asset_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Gorselin ikili verisini dondurur; silinmis veya saklama suresi dolmussa 404 doner.

    Tenant sahiplik kontrolu (`get_owned_asset`) HER ZAMAN suresi dolma
    kontrolunden ONCE calisir; boylece baska bir organizasyona ait bir
    `asset_id` icin "suresi dolmus" ile "hic yok" arasinda hicbir bilgi
    sizdirilmaz - ikisi de ayni 404 yanitini uretir (bkz. app.routers.page_analysis
    ile ayni "mevcut degil veya suresi doldu" konvansiyonu).
    """

    try:
        asset = await design_assets_service.get_owned_asset(session, organization_id, asset_id)
    except DesignAssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tasarim gorseli bulunamadi") from exc

    await session.commit()

    # Saklama suresi dolmus bir gorsel, purge cron'u henuz calismamis olsa
    # (yani `image_data` DB'de hala mevcut olsa) BILE artik sunulmaz -
    # erisimin kesilmesi purge cron'unun bir sonraki calismasini beklemez.
    if asset.image_data is None or design_assets_service.is_expired(asset):
        raise HTTPException(status_code=404, detail="Gorsel mevcut degil veya saklama suresi doldu")

    extension = design_assets_service.CONTENT_TYPE_FILE_EXTENSION[asset.content_type]
    return Response(
        content=asset.image_data,
        media_type=asset.content_type.value,
        headers={
            "X-Content-Type-Options": "nosniff",
            # Kullaniciya ozel, kimlik dogrulama/tenant kontrolune tabi bir
            # gorsel: hicbir ara/paylasimli onbellek (proxy/CDN) veya disk
            # onbellegi tarafindan saklanmamalidir.
            "Cache-Control": "private, no-store",
            # Kullanicinin gonderdigi orijinal dosya adi ASLA kullanilmaz;
            # yalnizca dogrulanmis icerik turunden turetilen sabit bir ad.
            "Content-Disposition": f'inline; filename="design-asset.{extension}"',
        },
    )


@router.delete("/{asset_id}", status_code=204)
async def delete_design_asset(
    asset_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Bir tasarim gorselini erken siler (ikili veri hemen temizlenir, metadata kalir)."""

    try:
        await design_assets_service.delete_asset(session, principal.organization_id, asset_id)
    except DesignAssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Tasarim gorseli bulunamadi") from exc

    await session.commit()
    return Response(status_code=204)
