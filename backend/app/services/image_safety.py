"""Ortak gorsel decode/guvenlik dogrulamasi.

`app.services.design_assets` (kullanici yuklemeleri) VE `app.services.page_analysis`
(URL analyzer'inin urettigi ekran goruntuleri VE saklanan DesignAsset
snapshot'larinin worker-zamani yeniden dogrulamasi) tarafindan ortaklasa
kullanilir - format kontrolu, cok-kareli (animasyonlu) red, boyut/piksel/
decompression-bomb korumasi TEK BIR yerde tanimlanir.

Kasitli olarak YALNIZCA decode+dogrulama icerir; EXIF temizleme/yeniden encode
(bkz. `design_assets._reencode_without_metadata`) burada YOKTUR ve buraya
eklenmez - bu, yalnizca kullanici yuklemeleri icin anlamli, ayri bir islemdir.
URL screenshot'lari veya DesignAsset snapshot kopyalari KOR bicimde yeniden
encode edilip mevcut byte-ozdesligi/hash sozlesmesini bozmamalidir (bkz.
app.services.page_analysis modul dokstring'i - `content_sha256` yalnizca
GERCEKTEN saklanan bayt dizisinin hash'idir).
"""

from __future__ import annotations

import io
import warnings

from PIL import Image, UnidentifiedImageError

from app.services.exceptions import ImageTooLargeError, InvalidImageError

# PIL format adi -> standart MIME icerik turu. Cagiran taraf, kendi izin
# verilen alt kumesini (`allowed_formats`) gecerek bunu daraltabilir (ör.
# page_analysis yalnizca PNG kabul eder - analyzer'in sozlesmesi bu).
STANDARD_IMAGE_FORMATS: dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


def check_not_multi_frame(probe: Image.Image) -> None:
    """Animasyonlu/cok kareli dosyalari reddeder (yalnizca statik tek kare kabul edilir).

    `n_frames`, cok kareli formatlari (GIF/APNG/animasyonlu WebP/TIFF)
    destekleyen Pillow eklentilerinde bulunur; tek kareli formatlarda oznitelik
    hic yoktur (bu durumda `getattr` varsayilan olarak 1 doner - guvenli taraf).
    """

    n_frames = getattr(probe, "n_frames", 1)
    if n_frames > 1:
        raise InvalidImageError(
            f"Animasyonlu/cok kareli gorseller kabul edilmez ({n_frames} kare tespit edildi); "
            "yalnizca statik tek kareli gorseller kabul edilir."
        )


def decode_and_validate_image(
    raw: bytes,
    *,
    allowed_formats: dict[str, str] | None = None,
    max_dimension: int,
    max_pixels: int,
) -> tuple[Image.Image, str]:
    """Bir gorsel bayt dizisini GERCEKTEN decode ederek dogrular; format/kare-
    sayisi/boyut/piksel kontrol eder. Iki asamali acilir: once yalnizca
    basligi okuyup format/boyut/kare-sayisini kontrol etmek icin (agir decode
    islemine gecmeden once ucuz bir red yolu), sonra gercek piksel verisini
    yukler. Pillow'un kendi decompression-bomb korumasi
    (`Image.MAX_IMAGE_PIXELS`) devre disi birakilmaz; `DecompressionBombWarning`
    acikca hataya cevrilir, boylece Pillow yalnizca "uyarip devam etme"
    durumunda sessizce riskli bir gorseli islemeye devam etmez.

    Basarili donusde ACIK bir `Image.Image` ve bunun MIME icerik turunu
    dondurur - CAGIRAN taraf goruntuyu kapatmakla (`.close()` veya `with`)
    yukumludur. `InvalidImageError`/`ImageTooLargeError` disinda hicbir
    istisna beklenmez; ikisi de yalnizca sentetik, guvenli (ham veri/stack
    trace icermeyen) aciklama metinleri tasir.
    """

    allowed = allowed_formats or STANDARD_IMAGE_FORMATS

    try:
        probe = Image.open(io.BytesIO(raw))
        probe_format = probe.format
        width, height = probe.size
    except Image.DecompressionBombError as exc:
        raise InvalidImageError("Gorsel guvenlik nedeniyle reddedildi (decompression-bomb siniri)") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("Dosya gecerli bir gorsel olarak okunamadi") from exc

    content_type = allowed.get(probe_format or "")
    if content_type is None:
        allowed_names = ", ".join(sorted(allowed))
        raise InvalidImageError(
            f"Desteklenmeyen gorsel formati: '{probe_format}'. Yalnizca {allowed_names} kabul edilir."
        )

    check_not_multi_frame(probe)

    if width > max_dimension or height > max_dimension:
        raise ImageTooLargeError(
            f"Gorsel cozunurlugu ({width}x{height}) izin verilen sinirin ({max_dimension}x{max_dimension}) uzerinde"
        )

    if width * height > max_pixels:
        raise ImageTooLargeError(
            f"Gorsel toplam piksel sayisi ({width * height}) izin verilen sinirin ({max_pixels}) uzerinde"
        )

    try:
        with warnings.catch_warnings():
            # Pillow varsayilan olarak MAX_IMAGE_PIXELS esiginin 1x-2x'i
            # arasinda yalnizca UYARIR (ve gorseli islemeye devam eder); bu,
            # kendi acik piksel/boyut sinirlarimizin USTUNE ek bir savunma
            # katmani olarak, uyariyi burada acikca hataya ceviriyoruz.
            # `Image.MAX_IMAGE_PIXELS` DEGERI DEGISTIRILMEZ/DEVRE DISI BIRAKILMAZ.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
            image.load()
    except Image.DecompressionBombError as exc:
        raise InvalidImageError("Gorsel guvenlik nedeniyle reddedildi (decompression-bomb siniri)") from exc
    except Image.DecompressionBombWarning as exc:
        raise InvalidImageError("Gorsel guvenlik nedeniyle reddedildi (decompression-bomb siniri)") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("Gorsel decode edilemedi (bozuk veya guvensiz dosya)") from exc

    return image, content_type


__all__ = ["STANDARD_IMAGE_FORMATS", "check_not_multi_frame", "decode_and_validate_image"]
