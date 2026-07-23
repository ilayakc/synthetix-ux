"""AI ile tasarim varyanti uretimi (design_generation) icin servis katmani testleri.

`test_design_assets.py` ile ayni desen: `session`/`organization` fixture'lari
(bkz. tests/conftest.py) kullanilarak servis fonksiyonlari DOGRUDAN cagirilir.
Gercek bir uzak saglayici sozlesmesi olmadigi icin, "provider yapilandirilmis"
senaryolari icin `get_image_generation_provider`in dondurdugu deger, kontrollu
bir sahte (mock) saglayiciyla monkeypatch edilir - gercek bir HTTP cagrisi asla
yapilmaz.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.design_assets import DesignAsset, DesignAssetStatus
from app.models.design_generation import DesignGenerationJob, DesignGenerationStatus
from app.models.tenancy import Organization
from app.services import design_assets as design_assets_service
from app.services import design_generation as design_generation_service
from app.services.exceptions import (
    DesignGenerationJobNotFoundError,
    ImageGenerationNotConfiguredError,
    ImageGenerationRequestError,
    InvalidDesignGenerationStateError,
)

pytestmark = pytest.mark.integration


def _png_bytes(width: int = 50, height: int = 40, color: tuple = (10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class _MockProvider(design_generation_service.BaseImageGenerationProvider):
    """Gercek bir ag cagrisi yapmayan, tamamen kontrollu sahte saglayici."""

    name = "mock-remote"
    model_name = "mock-model-v1"

    def __init__(self, *, result_bytes: bytes | None = None, error: Exception | None = None) -> None:
        self._result_bytes = result_bytes
        self._error = error
        self.call_count = 0

    async def generate(self, *, reference_image: bytes, reference_content_type: str, prompt: str):
        self.call_count += 1
        if self._error is not None:
            raise self._error
        assert self._result_bytes is not None
        return design_generation_service.GeneratedImageResult(
            raw_bytes=self._result_bytes, provider_request_id="mock-req-1"
        )


async def _make_source_asset(session: AsyncSession, organization: Organization) -> DesignAsset:
    return await design_assets_service.store_generated_asset(
        session, organization_id=organization.id, raw_bytes=_png_bytes(), label="kaynak"
    )


def _enable_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "image_generation_provider", "remote")
    monkeypatch.setattr(settings, "image_generation_endpoint", "https://mock.invalid/generate")


async def _count_jobs(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DesignGenerationJob))
    return int(result.scalar_one())


async def _count_assets(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(DesignAsset))
    return int(result.scalar_one())


# --- Provider kapali (varsayilan "none") ---------------------------------------------


async def test_provider_off_by_default(session: AsyncSession) -> None:
    assert design_generation_service.is_provider_configured() is False


async def test_create_job_without_provider_raises_and_creates_no_row(
    session: AsyncSession, organization: Organization
) -> None:
    source_asset = await _make_source_asset(session, organization)
    await session.flush()
    jobs_before = await _count_jobs(session)

    with pytest.raises(ImageGenerationNotConfiguredError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="Ana CTA'yi turuncu yap",
            authorization_confirmed=True,
        )

    assert await _count_jobs(session) == jobs_before


async def test_none_provider_generate_always_raises() -> None:
    provider = design_generation_service.NoneProvider()
    with pytest.raises(ImageGenerationNotConfiguredError):
        await provider.generate(reference_image=b"x", reference_content_type="image/png", prompt="p")


async def test_remote_provider_generate_is_not_implemented() -> None:
    """Gercek bir saglayici sozlesmesi dogrulanmadigi icin adaptor kasitli
    olarak tamamlanmamis birakilmistir; rastgele bir API formati UYDURULMAZ."""

    provider = design_generation_service.RemoteHttpProvider(
        endpoint="https://example.invalid",
        api_key=None,
        model_name="unspecified",
        timeout_seconds=10,
        max_retries=0,
    )
    with pytest.raises(NotImplementedError):
        await provider.generate(reference_image=b"x", reference_content_type="image/png", prompt="p")


# --- Is olusturma dogrulamalari (provider acikken) -----------------------------------


async def test_create_job_requires_authorization_confirmed(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)

    with pytest.raises(ImageGenerationRequestError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="Ana CTA'yi turuncu yap",
            authorization_confirmed=False,
        )
    assert await _count_jobs(session) == 0


async def test_create_job_rejects_empty_prompt(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)

    with pytest.raises(ImageGenerationRequestError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="   ",
            authorization_confirmed=True,
        )


async def test_create_job_rejects_prompt_over_max_length(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    _enable_provider(monkeypatch)
    monkeypatch.setattr(settings, "image_generation_max_prompt_length", 20)
    source_asset = await _make_source_asset(session, organization)

    with pytest.raises(ImageGenerationRequestError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="x" * 21,
            authorization_confirmed=True,
        )


async def test_create_job_rejects_missing_source_asset(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)

    with pytest.raises(ImageGenerationRequestError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=uuid.uuid4(),
            prompt="Baslik kisalt",
            authorization_confirmed=True,
        )


async def test_create_job_rejects_expired_source_asset(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    source_asset.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    with pytest.raises(ImageGenerationRequestError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="Baslik kisalt",
            authorization_confirmed=True,
        )


async def test_create_job_rejects_deleted_source_asset(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    await design_assets_service.delete_asset(session, organization.id, source_asset.id)

    with pytest.raises(ImageGenerationRequestError):
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="Baslik kisalt",
            authorization_confirmed=True,
        )


@pytest.mark.security
async def test_create_job_rejects_other_tenant_source_asset_without_leaking(
    session: AsyncSession, organization: Organization,
    make_user, make_organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    other_org = await make_organization()
    source_asset = await _make_source_asset(session, other_org)

    with pytest.raises(ImageGenerationRequestError) as excinfo:
        await design_generation_service.create_generation_job(
            session,
            organization_id=organization.id,
            user_id=uuid.uuid4(),
            source_asset_id=source_asset.id,
            prompt="Baslik kisalt",
            authorization_confirmed=True,
        )
    assert str(source_asset.id) not in str(excinfo.value)


async def test_create_job_succeeds_and_queues(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)

    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Ana CTA'yi turuncu yap",
        authorization_confirmed=True,
    )
    assert job.status == DesignGenerationStatus.QUEUED
    assert job.result_asset_id is None
    assert job.provider == "remote"


# --- Is akisi: queued -> running -> succeeded/failed ----------------------------------


async def test_job_lifecycle_succeeds_with_mock_provider(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Kartlarin araligini artir",
        authorization_confirmed=True,
    )
    await session.flush()

    claimed = await design_generation_service.claim_next_queued(session)
    assert [j.id for j in claimed] == [job.id]
    assert job.status == DesignGenerationStatus.RUNNING
    assert job.attempt_count == 1

    provider = _MockProvider(result_bytes=_png_bytes(color=(200, 100, 0)))
    await design_generation_service.process_job(session, job, provider=provider)

    assert job.status == DesignGenerationStatus.SUCCEEDED
    assert job.result_asset_id is not None
    assert job.provider_request_id == "mock-req-1"

    result_asset = await session.get(DesignAsset, job.result_asset_id)
    assert result_asset is not None
    assert result_asset.organization_id == organization.id
    assert result_asset.status == DesignAssetStatus.ACTIVE
    assert result_asset.image_data is not None


async def test_job_fails_when_provider_raises(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)

    provider = _MockProvider(error=TimeoutError("saglayici zaman asimi"))
    await design_generation_service.process_job(session, job, provider=provider)

    assert job.status == DesignGenerationStatus.FAILED
    assert job.result_asset_id is None
    assert job.error_message is not None
    assert "zaman asimi" not in job.error_message.lower()  # ham hata metni sizdirilmaz


async def test_job_fails_when_provider_returns_invalid_image(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)
    assets_before = await _count_assets(session)

    provider = _MockProvider(result_bytes=b"not-a-real-image-at-all")
    await design_generation_service.process_job(session, job, provider=provider)

    assert job.status == DesignGenerationStatus.FAILED
    assert job.result_asset_id is None
    assert await _count_assets(session) == assets_before


async def test_job_fails_when_provider_returns_svg(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)

    svg_payload = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    provider = _MockProvider(result_bytes=svg_payload)
    await design_generation_service.process_job(session, job, provider=provider)

    assert job.status == DesignGenerationStatus.FAILED
    assert job.result_asset_id is None


async def test_job_fails_when_provider_output_too_large(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import settings

    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)
    monkeypatch.setattr(settings, "design_asset_max_bytes", 10)

    provider = _MockProvider(result_bytes=_png_bytes(width=200, height=200))
    await design_generation_service.process_job(session, job, provider=provider)

    assert job.status == DesignGenerationStatus.FAILED
    assert job.result_asset_id is None


async def test_job_fails_when_source_asset_expired_between_creation_and_processing(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)

    source_asset.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    provider = _MockProvider(result_bytes=_png_bytes())
    await design_generation_service.process_job(session, job, provider=provider)

    assert job.status == DesignGenerationStatus.FAILED
    assert provider.call_count == 0


@pytest.mark.security
async def test_get_owned_job_tenant_isolation(
    session: AsyncSession, organization: Organization,
    make_user, make_organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )

    other_org = await make_organization()
    with pytest.raises(DesignGenerationJobNotFoundError):
        await design_generation_service.get_owned_job(session, other_org.id, job.id)

    found = await design_generation_service.get_owned_job(session, organization.id, job.id)
    assert found.id == job.id


# --- Reap / retry / cancel / purge ----------------------------------------------------


async def test_reap_stale_running_requeues_under_attempt_limit(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)
    job.updated_at = datetime.now(UTC) - timedelta(seconds=999)
    await session.flush()

    reaped = await design_generation_service.reap_stale_running(
        session, timeout_seconds=10, max_attempts=3
    )
    assert reaped == 1
    assert job.status == DesignGenerationStatus.QUEUED


async def test_reap_stale_running_fails_job_at_attempt_limit(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    job.attempt_count = 3
    job.status = DesignGenerationStatus.RUNNING
    job.updated_at = datetime.now(UTC) - timedelta(seconds=999)
    await session.flush()

    reaped = await design_generation_service.reap_stale_running(
        session, timeout_seconds=10, max_attempts=3
    )
    assert reaped == 1
    assert job.status == DesignGenerationStatus.FAILED
    assert job.error_code == "retry_limit_exceeded"


async def test_cancel_job_from_queued_succeeds(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )

    cancelled = await design_generation_service.cancel_job(session, organization.id, job.id)
    assert cancelled.status == DesignGenerationStatus.CANCELLED


async def test_cancel_job_from_running_is_rejected(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)

    with pytest.raises(InvalidDesignGenerationStateError):
        await design_generation_service.cancel_job(session, organization.id, job.id)


async def test_delete_job_does_not_delete_result_asset(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    await design_generation_service.claim_next_queued(session)
    provider = _MockProvider(result_bytes=_png_bytes(color=(1, 2, 3)))
    await design_generation_service.process_job(session, job, provider=provider)
    result_asset_id = job.result_asset_id
    assert result_asset_id is not None

    await design_generation_service.delete_job(session, organization.id, job.id)

    with pytest.raises(DesignGenerationJobNotFoundError):
        await design_generation_service.get_owned_job(session, organization.id, job.id)

    surviving_asset = await session.get(DesignAsset, result_asset_id)
    assert surviving_asset is not None
    assert surviving_asset.status == DesignAssetStatus.ACTIVE


async def test_purge_expired_jobs_clears_prompt_but_keeps_row(
    session: AsyncSession, organization: Organization,
    make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_provider(monkeypatch)
    source_asset = await _make_source_asset(session, organization)
    user = await make_user(organization)
    job = await design_generation_service.create_generation_job(
        session,
        organization_id=organization.id,
        user_id=user.id,
        source_asset_id=source_asset.id,
        prompt="Baslik kisalt",
        authorization_confirmed=True,
    )
    job.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    purged = await design_generation_service.purge_expired_jobs(session)
    assert purged == 1
    assert job.prompt is None
