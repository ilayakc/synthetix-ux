"""Yuklenen tasarim ekran goruntuleri (design_assets) icin servis/router-dogrudan
katmani testleri.

`test_design_assets_api.py` ile ayni ayrimi izler (bkz. `test_page_analysis.py`
docstring'i): bu dosya `session`/`organization` fixture'larini kullanir ve
servis fonksiyonlarini/router coroutine'lerini DOGRUDAN cagirir (TestClient
uzerinden HTTP degil) - streaming/DB-durum/zaman-tabanli senaryolar icin daha
kolay ve daha hizli kontrol saglar.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.design_assets import DesignAsset, DesignAssetContentType, DesignAssetStatus
from app.models.tenancy import Organization
from app.routers import design_assets as design_assets_router
from app.services import design_assets as design_assets_service
from app.services.exceptions import ImageTooLargeError

pytestmark = pytest.mark.integration


def _png_bytes(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(10, 200, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


async def _count_design_assets(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DesignAsset))
    return int(result.scalar_one())


async def _make_expired_asset(
    session: AsyncSession, organization: Organization, *, seconds_ago: int = 3600
) -> DesignAsset:
    """Purge cron'u HENUZ CALISMAMIS gibi, `image_data` hala dolu ama
    `expires_at` gecmiste olan bir kayit olusturur."""

    asset = DesignAsset(
        organization_id=organization.id,
        uploaded_by_user_id=None,
        content_type=DesignAssetContentType.PNG,
        byte_size=100,
        width=40,
        height=30,
        checksum_sha256="0" * 64,
        status=DesignAssetStatus.ACTIVE,
        image_data=b"fake-png-bytes",
        expires_at=datetime.now(UTC) - timedelta(seconds=seconds_ago),
    )
    session.add(asset)
    await session.flush()
    return asset


# --- 1. Streaming/sinirli okuma (bellek siniri) -----------------------------


class _FakeUploadFile:
    """`UploadFile`in `_read_upload_within_limit` icin ihtiyac duydugu asgari
    arayuzu taklit eder; kac parca okundugunu ve dosyanin kapatilip
    kapatilmadigini gozlemlenebilir kilar."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.read_call_count = 0
        self.closed = False

    async def read(self, _size: int) -> bytes:
        self.read_call_count += 1
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    async def close(self) -> None:
        self.closed = True


async def test_read_upload_within_limit_stops_reading_as_soon_as_limit_exceeded():
    """4 parca (toplam 40 bayt) sunulur ama sinir 15 bayttir; sinir 2. parcada
    (10+10=20 > 15) asilmalidir - 3. ve 4. parcalar HIC OKUNMAMALIDIR (yani
    dosyanin tamami onceden belege alinmaz)."""

    fake_file = _FakeUploadFile([b"a" * 10, b"b" * 10, b"c" * 10, b"d" * 10])

    with pytest.raises(ImageTooLargeError):
        await design_assets_router._read_upload_within_limit(fake_file, max_bytes=15)

    assert fake_file.read_call_count == 2
    assert fake_file.closed is True


async def test_read_upload_within_limit_closes_file_on_success_too():
    fake_file = _FakeUploadFile([b"a" * 10, b"b" * 10])

    result = await design_assets_router._read_upload_within_limit(fake_file, max_bytes=100)

    assert result == b"a" * 10 + b"b" * 10
    assert fake_file.closed is True


# --- 4. Sanitized cikti boyutu: basarisiz yukleme yarim satir birakmamali ----


async def test_failed_upload_due_to_stored_size_limit_leaves_no_partial_row(
    session: AsyncSession, organization: Organization, make_user, monkeypatch: pytest.MonkeyPatch
):
    user = await make_user(organization)
    monkeypatch.setattr(design_assets_service.settings, "design_asset_max_stored_bytes", 10)

    before = await _count_design_assets(session)

    with pytest.raises(ImageTooLargeError):
        await design_assets_service.upload_design_asset(
            session,
            organization_id=organization.id,
            uploaded_by_user_id=user.id,
            raw_bytes=_png_bytes(width=100, height=100),
        )

    after = await _count_design_assets(session)
    assert after == before


# --- 6. Suresi dolmus ama purge edilmemis erisim -----------------------------


async def test_preview_returns_404_when_expired_even_if_binary_still_present(
    session: AsyncSession, organization: Organization
):
    asset = await _make_expired_asset(session, organization)
    assert asset.image_data is not None  # purge HENUZ calismadi

    with pytest.raises(HTTPException) as exc_info:
        await design_assets_router.get_design_asset_preview(
            asset.id, organization_id=organization.id, session=session
        )
    assert exc_info.value.status_code == 404


@pytest.mark.security
async def test_tenant_isolation_checked_before_expiry_no_info_leak(
    session: AsyncSession, organization: Organization, make_organization
):
    """Baska bir organizasyona ait, suresi DOLMAMIS (aktif) bir gorsele erisim
    denemesi de, suresi dolmus kendi gorsune erisim denemesiyle AYNI (404)
    yaniti vermelidir - boylece yanit, hedef kaydin var olup olmadigi veya
    suresinin dolup dolmadigi hakkinda hicbir bilgi sizdirmaz."""

    other_org = await make_organization(name="Other Org")
    active_asset = DesignAsset(
        organization_id=other_org.id,
        uploaded_by_user_id=None,
        content_type=DesignAssetContentType.PNG,
        byte_size=100,
        width=40,
        height=30,
        checksum_sha256="1" * 64,
        status=DesignAssetStatus.ACTIVE,
        image_data=b"fake-png-bytes",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(active_asset)
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await design_assets_router.get_design_asset_preview(
            active_asset.id, organization_id=organization.id, session=session
        )
    assert exc_info.value.status_code == 404

    expired_own_asset = await _make_expired_asset(session, organization)
    with pytest.raises(HTTPException) as exc_info_expired:
        await design_assets_router.get_design_asset_preview(
            expired_own_asset.id, organization_id=organization.id, session=session
        )
    assert exc_info_expired.value.status_code == 404
    # Iki farkli senaryo (baska org'un aktif gorseli vs. kendi suresi dolmus
    # gorseli) TAM AYNI durum koduyla sonuclanir - hicbir ayirt edici bilgi yok.
    assert exc_info.value.status_code == exc_info_expired.value.status_code


async def test_purge_clears_binary_and_sets_purged_at(session: AsyncSession, organization: Organization):
    asset = await _make_expired_asset(session, organization)

    purged_count = await design_assets_service.purge_expired_design_assets(session)

    assert purged_count == 1
    await session.refresh(asset)
    assert asset.image_data is None
    assert asset.purged_at is not None

    with pytest.raises(HTTPException) as exc_info:
        await design_assets_router.get_design_asset_preview(
            asset.id, organization_id=organization.id, session=session
        )
    assert exc_info.value.status_code == 404


async def test_non_expired_asset_is_not_purged(session: AsyncSession, organization: Organization):
    asset = DesignAsset(
        organization_id=organization.id,
        uploaded_by_user_id=None,
        content_type=DesignAssetContentType.PNG,
        byte_size=100,
        width=40,
        height=30,
        checksum_sha256="2" * 64,
        status=DesignAssetStatus.ACTIVE,
        image_data=b"fake-png-bytes",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(asset)
    await session.flush()

    purged_count = await design_assets_service.purge_expired_design_assets(session)

    assert purged_count == 0
    await session.refresh(asset)
    assert asset.image_data is not None
