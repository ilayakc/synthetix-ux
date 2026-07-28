"""Kullanicinin yukledigi tasarim ekran goruntuleri: dogrulama, saklama, temizlik.

`app.services.page_analysis` ile ayni saklama/TTL/purge desenini izler
(Postgres `LargeBinary`, sureli saklama, ayri bir purge cron'u). Buradaki
temel fark: veri bir URL'den (Playwright ile) degil, dogrudan kullanicinin
yukledigi bir dosyadan gelir; bu nedenle SSRF dogrulamasi yerine **gorsel
icerik dogrulamasi** yapilir (bkz. docs/security.md "Yuklenen gorseller").

Guvenlik ilkeleri (hicbiri atlanmaz):
- Yalnizca PNG/JPEG/WebP kabul edilir; SVG hicbir zaman kabul edilmez
  (Pillow'un varsayilan format kayitlarinda SVG decode edici yoktur, bu
  yuzden bir SVG dosyasi zaten `UnidentifiedImageError` ile reddedilir).
- Content-Type header'ina veya dosya uzantisina GUVENILMEZ; dosya Pillow ile
  GERCEKTEN decode edilerek dogrulanir.
- Cok kareli (animasyonlu PNG/WebP) dosyalar reddedilir - yalnizca statik
  tek kareli tasarim ekran goruntuleri kabul edilir.
- Boyut (genislik/yukseklik) VE toplam piksel sayisi (genislik*yukseklik)
  bagimsiz olarak sinirlanir; Pillow'un kendi `DecompressionBombWarning`/
  `DecompressionBombError` korumasi (`Image.MAX_IMAGE_PIXELS`) ASLA devre
  disi birakilmaz, uyari acikca hataya cevrilir (bkz. `_decode_and_validate`).
- EXIF/ICC-profile gibi metadata, `ImageOps.exif_transpose` (yalnizca
  gorunum acisini piksellere gomup EXIF etiketini atmak icin) + piksel
  tamponunun (`tobytes`/`frombytes`) yeniden encode edilmesiyle tamamen
  dusurulur; sonrasinda kaydedilen gercek genislik/yukseklik (donme
  sonrasi degisebilir) veritabanina yazilir.
- Yeniden encode edilmis (saklanacak) cikti da ayrica boyut siniriyla
  kontrol edilir (yukleme siniriyle AYNI DEGIL - bkz. `design_asset_max_stored_bytes`).
- Kullanicinin yukledigi orijinal dosya adi hicbir zaman saklanmaz.
- Rastgele UUID birincil anahtar, guvenli dosya kimligi olarak kullanilir.
- `organization_id` her sorguda filtrelenir (bkz. `get_owned_asset`).
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.design_assets import DesignAsset, DesignAssetContentType, DesignAssetStatus
from app.services import image_safety
from app.services.exceptions import (
    DesignAssetNotFoundError,
    DesignAssetUnavailableError,
    ImageTooLargeError,
)

logger = logging.getLogger("synthetix.design_assets")

# Yalnizca PNG/JPEG/WebP kabul edilir - GIF dahil baska hicbir format bu
# haritada olmadigi icin `image_safety.decode_and_validate_image` tarafindan
# "desteklenmeyen format" olarak reddedilir.
_ALLOWED_MIME_FORMATS: dict[str, str] = {
    "PNG": DesignAssetContentType.PNG.value,
    "JPEG": DesignAssetContentType.JPEG.value,
    "WEBP": DesignAssetContentType.WEBP.value,
}

_PIL_SAVE_FORMAT: dict[DesignAssetContentType, str] = {
    DesignAssetContentType.PNG: "PNG",
    DesignAssetContentType.JPEG: "JPEG",
    DesignAssetContentType.WEBP: "WEBP",
}

# Dosya adi asla kullanicidan gelen degeri tasimaz; yalnizca icerik turune
# gore sabit, guvenli bir uzanti secilir (bkz. app.routers.design_assets).
CONTENT_TYPE_FILE_EXTENSION: dict[DesignAssetContentType, str] = {
    DesignAssetContentType.PNG: "png",
    DesignAssetContentType.JPEG: "jpg",
    DesignAssetContentType.WEBP: "webp",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _decode_and_validate(raw: bytes) -> tuple[Image.Image, DesignAssetContentType]:
    """Dosyayi gercekten decode ederek dogrular; format/kare-sayisi/boyut/piksel
    kontrolu `app.services.image_safety` (bkz. orada - `app.services.page_analysis`
    ile PAYLASILAN ortak dogrulama) tarafindan yapilir. Bu fonksiyon yalnizca
    MIME icerik turunu DesignAsset'e ozgu enum'a cevirir."""

    image, content_type_mime = image_safety.decode_and_validate_image(
        raw,
        allowed_formats=_ALLOWED_MIME_FORMATS,
        max_dimension=settings.design_asset_max_dimension,
        max_pixels=settings.design_asset_max_pixels,
    )
    return image, DesignAssetContentType(content_type_mime)


def _reencode_without_metadata(
    image: Image.Image, content_type: DesignAssetContentType
) -> tuple[bytes, int, int]:
    """EXIF/ICC-profile gibi tum metadata'yi dusurup yalnizca piksel verisini yeniden encode eder.

    `exif_transpose`, EXIF donme bilgisini (varsa) atmadan once piksellere
    gomer (aksi halde EXIF silindiginde gorsel yanlis yonde gorunebilir); bu
    islem 90/270 derecelik donmelerde genislik/yukseklik degerlerini
    DEGISTIREBILIR - bu yuzden fonksiyon, ORIJINAL boyut yerine SONUC
    (kaydedilecek) gorselin gercek genislik/yuksekligini de dondurur.
    """

    oriented = ImageOps.exif_transpose(image) or image

    pil_format = _PIL_SAVE_FORMAT[content_type]
    if pil_format == "JPEG":
        # JPEG saydamligi desteklemez; alfa kanali varsa duz beyaz zemine gomulur.
        if oriented.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", oriented.size, (255, 255, 255))
            rgba = oriented.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            oriented = background
        else:
            oriented = oriented.convert("RGB")
    elif oriented.mode not in ("RGB", "RGBA"):
        oriented = oriented.convert("RGBA" if "A" in oriented.getbands() else "RGB")

    # `frombytes(tobytes())`, yorum/EXIF/ICC-profile disinda hicbir metadata
    # tasimayan taze bir piksel tamponu uretir (Pillow'un kendi python-seviyeli
    # piksel dongulerinden cok daha hizlidir).
    clean = Image.frombytes(oriented.mode, oriented.size, oriented.tobytes())

    buffer = io.BytesIO()
    save_kwargs: dict = {}
    if pil_format == "JPEG":
        save_kwargs["quality"] = 90
    elif pil_format == "WEBP":
        save_kwargs["quality"] = 90
    clean.save(buffer, format=pil_format, **save_kwargs)
    final_width, final_height = clean.size
    return buffer.getvalue(), final_width, final_height


async def upload_design_asset(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    uploaded_by_user_id: uuid.UUID,
    raw_bytes: bytes,
    label: str | None = None,
) -> DesignAsset:
    """Yuklenen bir dosyayi dogrular, EXIF'ini temizler, yeniden encode eder ve saklar.

    Herhangi bir dogrulama/boyut kontrolu basarisiz olursa (ImageTooLargeError/
    InvalidImageError), fonksiyon bu noktalarin HICBIRINDE `session.add`
    cagirmamis olur - yani basarisiz bir yukleme veritabaninda YARIM/tutarsiz
    bir `DesignAsset` satiri BIRAKMAZ (satir yalnizca tum dogrulamalar
    basarili olduktan SONRA olusturulur).
    """

    if len(raw_bytes) > settings.design_asset_max_bytes:
        raise ImageTooLargeError(
            f"Dosya boyutu ({len(raw_bytes)} bayt) izin verilen sinirin "
            f"({settings.design_asset_max_bytes} bayt) uzerinde"
        )

    image, content_type = _decode_and_validate(raw_bytes)
    clean_bytes, width, height = _reencode_without_metadata(image, content_type)

    # Yeniden encode edilmis cikti, kaynak formatin orijinal sikistirma
    # ayarlarini korumadigi icin (ozellikle PNG) kaynaktan BUYUK cikabilir;
    # bu yuzden saklama siniri, yukleme siniriyle AYNI DEGIL, ayri ve biraz
    # daha genis bir ayar (`design_asset_max_stored_bytes`) ile kontrol
    # edilir - meshru bir yuklemenin yalnizca yeniden-encode fazlaligi
    # yuzunden reddedilmesini onlemek icin.
    if len(clean_bytes) > settings.design_asset_max_stored_bytes:
        raise ImageTooLargeError(
            f"Yeniden encode edilmis gorsel boyutu ({len(clean_bytes)} bayt) izin verilen saklama "
            f"sinirinin ({settings.design_asset_max_stored_bytes} bayt) uzerinde"
        )

    asset = DesignAsset(
        organization_id=organization_id,
        uploaded_by_user_id=uploaded_by_user_id,
        content_type=content_type,
        byte_size=len(clean_bytes),
        width=width,
        height=height,
        checksum_sha256=hashlib.sha256(clean_bytes).hexdigest(),
        label=label,
        status=DesignAssetStatus.ACTIVE,
        image_data=clean_bytes,
        expires_at=_now() + timedelta(seconds=settings.design_asset_retention_seconds),
    )
    session.add(asset)
    await session.flush()
    return asset


async def store_generated_asset(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    raw_bytes: bytes,
    label: str | None = None,
) -> DesignAsset:
    """AI saglayicisindan donen gorsel ciktisini, kullanici yuklemesiyle AYNI
    guvenli dogrulama hattindan (`_decode_and_validate`/
    `_reencode_without_metadata` - PNG/JPEG/WebP dogrulamasi, SVG reddi,
    boyut/piksel siniri, coklu-kare reddi, metadata temizleme, yeniden
    encode, sha256) gecirerek saklar (bkz. app.services.design_generation).

    Saglayicidan gelen veriye HICBIR ZAMAN guvenilmez: gecersiz/bozuk/asiri
    buyuk/SVG/coklu-kareli bir cikti da `upload_design_asset` ile aynen
    ayni istisnalarla (InvalidImageError/ImageTooLargeError) reddedilir - bu
    durumda cagiran taraf (is/job) 'failed' isaretlenir, HICBIR `DesignAsset`
    olusturulmaz. `uploaded_by_user_id` kasitli olarak `None`dir (bu bir
    kullanici yuklemesi degildir); saglayicinin kendisi degil, bu satiri
    olusturan is (job) kaynagini belirler.
    """

    if len(raw_bytes) > settings.design_asset_max_bytes:
        raise ImageTooLargeError(
            f"Uretilen gorsel boyutu ({len(raw_bytes)} bayt) izin verilen sinirin "
            f"({settings.design_asset_max_bytes} bayt) uzerinde"
        )

    image, content_type = _decode_and_validate(raw_bytes)
    clean_bytes, width, height = _reencode_without_metadata(image, content_type)

    if len(clean_bytes) > settings.design_asset_max_stored_bytes:
        raise ImageTooLargeError(
            f"Yeniden encode edilmis gorsel boyutu ({len(clean_bytes)} bayt) izin verilen saklama "
            f"sinirinin ({settings.design_asset_max_stored_bytes} bayt) uzerinde"
        )

    asset = DesignAsset(
        organization_id=organization_id,
        uploaded_by_user_id=None,
        content_type=content_type,
        byte_size=len(clean_bytes),
        width=width,
        height=height,
        checksum_sha256=hashlib.sha256(clean_bytes).hexdigest(),
        label=label,
        status=DesignAssetStatus.ACTIVE,
        image_data=clean_bytes,
        expires_at=_now() + timedelta(seconds=settings.design_asset_retention_seconds),
    )
    session.add(asset)
    await session.flush()
    return asset


async def get_owned_asset(
    session: AsyncSession, organization_id: uuid.UUID, asset_id: uuid.UUID
) -> DesignAsset:
    result = await session.execute(select(DesignAsset).where(DesignAsset.id == asset_id))
    asset = result.scalar_one_or_none()
    if asset is None or asset.organization_id != organization_id:
        raise DesignAssetNotFoundError(f"Tasarim gorseli bulunamadi: {asset_id}")
    return asset


def is_expired(asset: DesignAsset) -> bool:
    """Saklama suresi dolmus mu (purge cron'u henuz calismamis olsa bile)."""

    return asset.expires_at is not None and asset.expires_at <= _now()


def ensure_asset_usable(asset: DesignAsset) -> None:
    """Bir DesignAsset'in (sahiplik kontrolunden GECTIKTEN sonra) PageAnalysis
    kaynagi olarak kullanilabilir oldugunu dogrular: aktif, saklama suresi
    dolmamis ve ikili verisi (henuz purge edilmemis) mevcut olmali.

    Hem create-time (app.services.page_analysis.create_analysis) hem de
    worker-time (app.services.page_analysis.process_analysis) tarafindan
    cagrilir - create ile worker'in isleme zamani arasinda asset silinebilir/
    expire olabilir, bu yuzden create-time kontrolune TEK BASINA guvenilmez.
    """

    if asset.status != DesignAssetStatus.ACTIVE or asset.image_data is None or is_expired(asset):
        raise DesignAssetUnavailableError(f"Tasarim gorseli artik kullanilabilir durumda degil: {asset.id}")


async def delete_asset(session: AsyncSession, organization_id: uuid.UUID, asset_id: uuid.UUID) -> DesignAsset:
    """Kullanici tarafindan erken silme: metadata satiri kalir, ikili veri hemen temizlenir."""

    asset = await get_owned_asset(session, organization_id, asset_id)
    asset.status = DesignAssetStatus.DELETED
    asset.image_data = None
    asset.purged_at = _now()
    await session.flush()
    return asset


async def purge_expired_design_assets(session: AsyncSession) -> int:
    """Saklama suresi dolmus gorsel verilerini siler; metadata satiri kalir."""

    result = await session.execute(
        select(DesignAsset).where(
            DesignAsset.image_data.is_not(None),
            DesignAsset.expires_at.is_not(None),
            DesignAsset.expires_at < _now(),
        )
    )
    expired = list(result.scalars().all())
    for asset in expired:
        asset.image_data = None
        asset.purged_at = _now()
    await session.flush()
    return len(expired)


async def run_purge_cycle() -> None:
    """arq cron girdisi: saklama suresi dolmus tasarim gorseli verilerini siler."""

    from app.db import async_session_maker

    async with async_session_maker() as session:
        try:
            await purge_expired_design_assets(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("purge_expired_design_assets basarisiz oldu")


__all__ = [
    "CONTENT_TYPE_FILE_EXTENSION",
    "upload_design_asset",
    "store_generated_asset",
    "get_owned_asset",
    "is_expired",
    "ensure_asset_usable",
    "delete_asset",
    "purge_expired_design_assets",
    "run_purge_cycle",
]
