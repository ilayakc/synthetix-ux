"""Yuklenen tasarim ekran goruntuleri (design_assets) API katmani testleri.

`test_page_analysis_api.py` ile ayni desen: `client` fixture'i (tests/conftest.py)
uzerinden gercek bir `TestClient` kullanilir, her test kendi `_register(client)`
cagrisini yapar.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

pytestmark = pytest.mark.integration


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient) -> dict:
    email = _unique_email()
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _png_bytes(width: int = 50, height: int = 40, color: tuple = (255, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg_bytes_with_exif(width: int = 50, height: int = 40) -> bytes:
    image = Image.new("RGB", (width, height), color=(0, 128, 255))
    exif = image.getexif()
    exif[0x0112] = 3  # Orientation etiketi (180 derece dondurulmus)
    exif[0x010F] = "TestCameraMake"  # Make etiketi
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def _webp_bytes(width: int = 50, height: int = 40) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(0, 200, 0)).save(buffer, format="WEBP")
    return buffer.getvalue()


def _gif_bytes(width: int = 50, height: int = 40) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 0, 200)).save(buffer, format="GIF")
    return buffer.getvalue()


def _animated_webp_bytes(width: int = 20, height: int = 20) -> bytes:
    # Kareler KASITLI OLARAK farkli renkte (Pillow, piksel-ozdes ardisik
    # kareleri animasyon encode sirasinda birlestirebilir; bu durumda
    # `n_frames` yanlislikla 1 donebilir).
    frame_a = Image.new("RGB", (width, height), color=(255, 0, 0))
    frame_b = Image.new("RGB", (width, height), color=(0, 0, 255))
    buffer = io.BytesIO()
    frame_a.save(buffer, format="WEBP", save_all=True, append_images=[frame_b], duration=100, loop=0)
    return buffer.getvalue()


def _jpeg_bytes_with_90_degree_exif_rotation(width: int = 60, height: int = 30) -> bytes:
    """EXIF orientation=6 (90 derece saat yonu) uygulandiginda genislik/yukseklik YER DEGISTIRIR."""

    image = Image.new("RGB", (width, height), color=(10, 20, 30))
    exif = image.getexif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_upload_valid_png_returns_metadata_without_binary(client: TestClient):
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
        data={"label": "Tasarim A"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content_type"] == "image/png"
    assert body["width"] == 50
    assert body["height"] == 40
    assert body["label"] == "Tasarim A"
    assert body["has_image"] is True
    assert "image_data" not in body
    assert "data" not in body


def test_upload_valid_webp_succeeds(client: TestClient):
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.webp", _webp_bytes(), "image/webp")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["content_type"] == "image/webp"


@pytest.mark.security
def test_upload_rejects_svg_disguised_as_png(client: TestClient):
    _register(client)
    svg_payload = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    response = client.post(
        "/api/design-assets",
        # Content-Type/uzantiya guvenilmedigini kanitlamak icin dosya "png"
        # olarak sunulur ama gercek icerik bir SVG'dir - decode edilerek reddedilmelidir.
        files={"file": ("fake.png", svg_payload, "image/png")},
    )
    assert response.status_code == 422
    assert (
        "gecerli bir" in response.json()["detail"].lower()
        or "desteklenmeyen" in response.json()["detail"].lower()
    )


@pytest.mark.security
def test_upload_rejects_non_image_bytes(client: TestClient):
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", b"not-a-real-image-at-all", "image/png")},
    )
    assert response.status_code == 422


@pytest.mark.security
def test_upload_rejects_oversized_file(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "design_asset_max_bytes", 10)
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 413


@pytest.mark.security
def test_upload_rejects_oversized_dimensions(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from app.config import settings

    monkeypatch.setattr(settings, "design_asset_max_dimension", 10)
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(width=50, height=40), "image/png")},
    )
    assert response.status_code == 413


def test_upload_rejects_empty_file(client: TestClient):
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", b"", "image/png")},
    )
    assert response.status_code == 422


@pytest.mark.security
def test_upload_rejects_oversized_total_pixel_count(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """Genislik VE yukseklik ayri ayri sinirin altinda kalsa bile toplam piksel
    sayisi (genislik*yukseklik) `design_asset_max_pixels`'i asarsa reddedilmelidir -
    bu, `design_asset_max_dimension`'dan BAGIMSIZ ikinci bir savunma katmanidir."""

    from app.config import settings

    monkeypatch.setattr(settings, "design_asset_max_dimension", 10_000)
    monkeypatch.setattr(settings, "design_asset_max_pixels", 1000)
    _register(client)
    response = client.post(
        # 50x40 = 2000 piksel > 1000 siniri (ikisi de max_dimension'in cok altinda).
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(width=50, height=40), "image/png")},
    )
    assert response.status_code == 413
    assert "piksel" in response.json()["detail"].lower()


@pytest.mark.security
def test_upload_rejects_image_over_decompression_bomb_error_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Pillow'un kendi `MAX_IMAGE_PIXELS` esiginin 2 katindan fazlasi HER ZAMAN
    `DecompressionBombError` firlatir (uyari degil, dogrudan hata); bu, kucuk bir
    test gorseliyle esigi gecici olarak dusurerek deterministik biçimde tetiklenir
    (Pillow'un global korumasi KALICI OLARAK devre disi birakilmaz, yalnizca bu
    test icin dusurulur ve test sonunda otomatik olarak eski haline doner)."""

    from PIL import Image as PILImage

    monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 100)
    _register(client)
    response = client.post(
        # 50x40 = 2000 piksel, esigin (100) 2 katindan (200) fazla.
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(width=50, height=40), "image/png")},
    )
    assert response.status_code == 422
    assert "decompression-bomb" in response.json()["detail"].lower()


@pytest.mark.security
def test_upload_rejects_image_over_decompression_bomb_warning_threshold(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Esigin 1x-2x'i arasi normalde yalnizca bir UYARI'dir (Pillow gorseli
    islemeye devam eder); bu servis bu uyariyi acikca hataya cevirir, boylece
    sessizce riskli bir gorsel islenmez."""

    from PIL import Image as PILImage

    monkeypatch.setattr(PILImage, "MAX_IMAGE_PIXELS", 1500)
    _register(client)
    response = client.post(
        # 50x40 = 2000 piksel, esigin (1500) 1x-2x arasinda (uyari araligi).
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(width=50, height=40), "image/png")},
    )
    assert response.status_code == 422
    assert "decompression-bomb" in response.json()["detail"].lower()


@pytest.mark.security
def test_upload_rejects_animated_webp(client: TestClient):
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("anim.webp", _animated_webp_bytes(), "image/webp")},
    )
    assert response.status_code == 422
    assert "animasyonlu" in response.json()["detail"].lower() or "kare" in response.json()["detail"].lower()


@pytest.mark.security
def test_upload_rejects_gif(client: TestClient):
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.gif", _gif_bytes(), "image/gif")},
    )
    assert response.status_code == 422
    assert "desteklenmeyen" in response.json()["detail"].lower()


@pytest.mark.security
def test_upload_rejects_when_reencoded_output_exceeds_storage_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "design_asset_max_stored_bytes", 50)
    _register(client)
    response = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(width=200, height=200), "image/png")},
    )
    assert response.status_code == 413
    assert "saklama" in response.json()["detail"].lower()


def test_stored_image_has_exif_stripped(client: TestClient):
    _register(client)
    upload = client.post(
        "/api/design-assets",
        files={"file": ("photo.jpg", _jpeg_bytes_with_exif(), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["id"]

    preview = client.get(f"/api/design-assets/{asset_id}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"

    stored_image = Image.open(io.BytesIO(preview.content))
    assert not dict(stored_image.getexif())


def test_metadata_width_height_reflect_exif_orientation_not_original_bytes(client: TestClient):
    """EXIF orientation donmeyi piksellere gomdukten sonra genislik/yukseklik
    YER DEGISTIRIRSE (90/270 derece), veritabaninda saklanan (ve API'nin
    dondurdugu) width/height, ORIJINAL yuklenen dosyanin degil, GERCEKTEN
    saklanan (sanitized) ciktinin olcumleri olmalidir - ve bu, preview
    uc noktasindan donen gercek gorselin olculeriyle TAM AYNI olmalidir."""

    _register(client)
    upload = client.post(
        "/api/design-assets",
        # Orijinal dosya 60x30'dur; EXIF orientation=6, 90 derece dondurup
        # piksellere gomdugunde SAKLANAN gorsel 30x60 OLMALIDIR.
        files={"file": ("photo.jpg", _jpeg_bytes_with_90_degree_exif_rotation(60, 30), "image/jpeg")},
    )
    assert upload.status_code == 201, upload.text
    body = upload.json()
    assert (body["width"], body["height"]) == (30, 60)

    preview = client.get(f"/api/design-assets/{body['id']}/preview")
    assert preview.status_code == 200
    stored_image = Image.open(io.BytesIO(preview.content))
    assert stored_image.size == (30, 60)
    assert stored_image.size == (body["width"], body["height"])


def test_preview_response_has_expected_security_headers(client: TestClient):
    _register(client)
    upload = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
    )
    asset_id = upload.json()["id"]

    preview = client.get(f"/api/design-assets/{asset_id}/preview")
    assert preview.status_code == 200
    assert preview.headers["x-content-type-options"] == "nosniff"
    assert preview.headers["cache-control"] == "private, no-store"
    # Kullanicinin gonderdigi dosya adi ("upload.png") ASLA header'da yer almaz;
    # yalnizca dogrulanmis icerik turunden turetilen sabit bir ad kullanilir.
    assert "upload.png" not in preview.headers.get("content-disposition", "")
    assert "inline" in preview.headers["content-disposition"]
    assert "design-asset" in preview.headers["content-disposition"]


def test_preview_and_metadata_isolated_per_organization(client: TestClient):
    _register(client)
    upload = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
    )
    asset_id = upload.json()["id"]

    other_client_response_org = _register(client)
    assert other_client_response_org is not None

    metadata = client.get(f"/api/design-assets/{asset_id}")
    assert metadata.status_code == 404

    preview = client.get(f"/api/design-assets/{asset_id}/preview")
    assert preview.status_code == 404


def test_get_unknown_asset_returns_404(client: TestClient):
    _register(client)
    response = client.get(f"/api/design-assets/{uuid.uuid4()}")
    assert response.status_code == 404


def test_delete_clears_binary_but_keeps_metadata_row(client: TestClient):
    _register(client)
    upload = client.post(
        "/api/design-assets",
        files={"file": ("upload.png", _png_bytes(), "image/png")},
    )
    asset_id = upload.json()["id"]

    delete_response = client.delete(f"/api/design-assets/{asset_id}")
    assert delete_response.status_code == 204

    metadata = client.get(f"/api/design-assets/{asset_id}")
    assert metadata.status_code == 200
    assert metadata.json()["status"] == "deleted"
    assert metadata.json()["has_image"] is False

    preview = client.get(f"/api/design-assets/{asset_id}/preview")
    assert preview.status_code == 404


def test_delete_unknown_asset_returns_404(client: TestClient):
    _register(client)
    response = client.delete(f"/api/design-assets/{uuid.uuid4()}")
    assert response.status_code == 404
