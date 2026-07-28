"""AI ile alternatif tasarim (Tasarim B) varyanti uretimi: queued -> running -> succeeded/failed.

`app.services.page_analysis` ile ayni durum makinesi/kilitleme desenini
izler (`claim_next_queued` -> `SELECT ... FOR UPDATE SKIP LOCKED`,
`reap_stale_running`, ayri `run_*_cycle` arq cron girdileri).

Bu modul, `app.services.ai_explanation`'daki saglayici (provider) adaptor
desenini de izler ANCAK **tamamen ayri, bagimsiz bir yapilandirmayla**
(`image_generation_*` - bkz. app.config): bir metin/aciklama
saglayicisinin gorsel de uretebilecegi VARSAYILMAZ. `NoneProvider` burada
`ai_explanation.NoneProvider`in aksine hicbir zaman bir cikti URETMEZ -
"guvenli/deterministik bir sahte gorsel" diye bir sey yoktur; yapilandirma
eksikse tek dogru davranis acikca reddetmektir (bkz. `ImageGenerationNotConfiguredError`).

Gercek bir uzak saglayici sozlesmesi (endpoint/istek-yanit semasi) bu paket
icin dogrulanmamistir; `RemoteHttpProvider.generate` bu nedenle bilinen bir
API formatini TAHMIN ETMEK yerine acikca `NotImplementedError` firlatir.
`get_image_generation_provider`, `settings.image_generation_provider`
"remote" olsa bile bu sinifi asla otomatik SECMEZ; yalnizca testler kendi
mock alt sinifiyla adaptoru dogrulayabilir.

Guvenlik: saglayicidan donen gorsel ciktisina HICBIR ZAMAN guvenilmez -
`app.services.design_assets.store_generated_asset` (Package 1'in TAM
dogrulama hatti: decode/format/SVG reddi/boyut-piksel siniri/coklu-kare
reddi/metadata temizleme/yeniden encode/sha256) disinda hicbir yoldan
`DesignAsset` olusturulmaz.
"""

from __future__ import annotations

import abc
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_maker
from app.models.design_assets import DesignAsset, DesignAssetStatus
from app.models.design_generation import DesignGenerationJob, DesignGenerationStatus
from app.services import design_assets as design_assets_service
from app.services.exceptions import (
    DesignAssetNotFoundError,
    DesignGenerationJobNotFoundError,
    ImageGenerationNotConfiguredError,
    ImageGenerationRequestError,
    ImageTooLargeError,
    InvalidDesignGenerationStateError,
    InvalidImageError,
)

logger = logging.getLogger("synthetix.design_generation")

CLAIM_BATCH_SIZE = 5

GENERATION_VERSION = "v1"

RESULT_ASSET_LABEL = "AI uretilen tasarim taslagi"

SOURCE_ASSET_UNAVAILABLE_MESSAGE = (
    "Referans tasarim gorseli kullanilamiyor (silinmis, saklama suresi dolmus veya "
    "erisilemez olabilir); lutfen yeni bir gorsel yukleyin."
)


def _now() -> datetime:
    return datetime.now(UTC)


# --- Saglayici (provider) protokolu -------------------------------------------------


@dataclass(frozen=True)
class GeneratedImageResult:
    raw_bytes: bytes
    provider_request_id: str | None = None


class BaseImageGenerationProvider(abc.ABC):
    name: str = "base"
    model_name: str = "unspecified"

    @abc.abstractmethod
    async def generate(
        self, *, reference_image: bytes, reference_content_type: str, prompt: str
    ) -> GeneratedImageResult: ...


class NoneProvider(BaseImageGenerationProvider):
    """Varsayilan saglayici: HICBIR gorsel uretmez.

    `ai_explanation.NoneProvider`in aksine burada guvenli/deterministik bir
    "sahte" cikti YOKTUR - bir gorsel uretim saglayicisi olmadan gercekci,
    yaniltici olmayan bir tasarim taslagi uretilemez. Bu provider yalnizca
    savunma amaciyla vardir (`get_image_generation_provider` her zaman
    yapilandirma eksikliginde bunu dondurur); cagiran taraf
    (`create_generation_job`) yapilandirma eksikligini ZATEN is olusturmadan
    once kontrol eder, bu yuzden `generate()` normal akiste hic cagirilmaz.
    """

    name = "none"
    model_name = "unspecified"

    async def generate(
        self, *, reference_image: bytes, reference_content_type: str, prompt: str
    ) -> GeneratedImageResult:
        raise ImageGenerationNotConfiguredError(
            "Gorsel uretim saglayicisi yapilandirilmamis; hicbir gorsel uretilmedi."
        )


class RemoteHttpProvider(BaseImageGenerationProvider):
    """Uzak (remote) saglayici icin adaptor iskeleti.

    KASITLI OLARAK TAMAMLANMAMIS: bu paket kapsaminda dogrulanmis, gercek
    bir saglayici sozlesmesi (endpoint istek/yanit semasi, kimlik dogrulama
    yontemi, hata kodlari) YOKTUR. Rastgele bir API formati uydurup "gercek
    entegrasyon" gibi gostermek yerine, adaptorun sekli (constructor imzasi)
    burada tanimlanir ve `generate()` acikca `NotImplementedError` firlatir.
    Gercek entegrasyon, saglayici secildikten ve sozlesme dogrulandiktan
    SONRA, bu sinif icine yazilmalidir (bkz. proje talimatinin ilgili
    bolumu ve final raporundaki "kalan urun kararlari" listesi).
    """

    name = "remote"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str | None,
        model_name: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)

    async def generate(
        self, *, reference_image: bytes, reference_content_type: str, prompt: str
    ) -> GeneratedImageResult:
        raise NotImplementedError(
            "RemoteHttpProvider.generate henuz uygulanmadi: dogrulanmis bir gorsel "
            "uretim saglayicisi sozlesmesi (endpoint/istek-yanit semasi) secilmeden "
            "rastgele bir API formati varsayilamaz. Bkz. app.config.settings."
            "image_generation_* ve proje kararlari raporu."
        )


def get_image_generation_provider() -> BaseImageGenerationProvider:
    """Yapilandirmaya gore aktif saglayiciyi dondurur.

    `ai_explanation.get_provider`den KASITLI OLARAK FARKLI davranir: metin
    aciklamasinin aksine "remote yapilandirilmis ama gercekte cagrilamaz"
    durumunda sessizce `NoneProvider`a DUSMEZ - `RemoteHttpProvider` henuz
    calismadigi icin (`NotImplementedError`) bunu gizlemek yaniltici
    olurdu. `create_generation_job` bu yuzden is olusturmadan ONCE, bu
    fonksiyonu hic cagirmadan, saglayicinin GERCEKTEN calisir durumda olup
    olmadigini ayrica kontrol eder (bkz. asagisi).
    """

    if settings.image_generation_provider == "remote" and settings.image_generation_endpoint:
        return RemoteHttpProvider(
            endpoint=settings.image_generation_endpoint,
            api_key=settings.image_generation_api_key,
            model_name=settings.image_generation_model_name,
            timeout_seconds=settings.image_generation_request_timeout_seconds,
            max_retries=settings.image_generation_max_retries,
        )
    return NoneProvider()


def is_provider_configured() -> bool:
    """Frontend'in "AI ile olustur" secenegini gosterip gostermeyecegine karar
    vermek icin kullandigi tek dogruluk kaynagi (bkz. GET /api/design-generations/availability).
    """

    return settings.image_generation_provider == "remote" and bool(settings.image_generation_endpoint)


# --- Is (job) olusturma / sahiplik --------------------------------------------------


async def create_generation_job(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    source_asset_id: uuid.UUID,
    prompt: str,
    authorization_confirmed: bool,
) -> DesignGenerationJob:
    """Yeni bir AI tasarim uretim isi olusturur ve kuyruga (queued) ekler.

    Sira onemlidir: hicbir DB satiri, kullanicinin uzak saglayiciya aktarim
    onayi vermeden VEYA saglayici yapilandirilmamisken OLUSTURULMAZ (bkz.
    proje talimati "Gizlilik ve acik kullanici onayi").
    """

    if not authorization_confirmed:
        raise ImageGenerationRequestError(
            "Goruntunun uzak bir AI saglayicisina aktarilacagini onaylamadan is olusturulamaz."
        )

    normalized_prompt = (prompt or "").strip()
    if not normalized_prompt:
        raise ImageGenerationRequestError("Talep (prompt) bos olamaz.")
    if len(normalized_prompt) > settings.image_generation_max_prompt_length:
        raise ImageGenerationRequestError(
            f"Talep (prompt) izin verilen en fazla {settings.image_generation_max_prompt_length} "
            "karakteri asiyor."
        )

    if not is_provider_configured():
        raise ImageGenerationNotConfiguredError(
            "AI gorsel uretim saglayicisi henuz yapilandirilmadi. Tasarim B icin URL veya "
            "ekran goruntusu kullanabilirsiniz."
        )

    try:
        source_asset = await design_assets_service.get_owned_asset(session, organization_id, source_asset_id)
    except DesignAssetNotFoundError as exc:
        raise ImageGenerationRequestError(SOURCE_ASSET_UNAVAILABLE_MESSAGE) from exc

    if (
        source_asset.status != DesignAssetStatus.ACTIVE
        or source_asset.image_data is None
        or design_assets_service.is_expired(source_asset)
    ):
        raise ImageGenerationRequestError(SOURCE_ASSET_UNAVAILABLE_MESSAGE)

    provider = get_image_generation_provider()
    job = DesignGenerationJob(
        organization_id=organization_id,
        created_by_user_id=user_id,
        source_asset_id=source_asset_id,
        status=DesignGenerationStatus.QUEUED,
        prompt=normalized_prompt,
        provider=provider.name,
        model_name=provider.model_name,
        attempt_count=0,
        generation_version=GENERATION_VERSION,
        expires_at=_now() + timedelta(seconds=settings.design_asset_retention_seconds),
    )
    session.add(job)
    await session.flush()
    return job


async def get_owned_job(
    session: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
) -> DesignGenerationJob:
    result = await session.execute(select(DesignGenerationJob).where(DesignGenerationJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None or job.organization_id != organization_id:
        raise DesignGenerationJobNotFoundError(f"Uretim isi bulunamadi: {job_id}")
    return job


async def cancel_job(
    session: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID
) -> DesignGenerationJob:
    """Yalnizca henuz islenmeye baslanmamis (queued) bir is iptal edilebilir.

    Zaten calismakta olan (running) bir uzak cagriyi yari yolda iptal etmek
    bu pakette desteklenmez (saglayici sozlesmesi bilinmedigi icin
    guvenli/temiz bir iptal API'si varsayilamaz).
    """

    job = await get_owned_job(session, organization_id, job_id)
    if job.status != DesignGenerationStatus.QUEUED:
        raise InvalidDesignGenerationStateError(
            f"Is '{job.status.value}' durumundayken iptal edilemez (yalnizca 'queued' iptal edilebilir)."
        )
    job.status = DesignGenerationStatus.CANCELLED
    job.finished_at = _now()
    await session.flush()
    return job


async def delete_job(session: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Isi kullanicinin listesinden kaldirir.

    Bu, isin urettigi `result_asset_id`ye HICBIR SEKILDE dokunmaz: bir
    `DesignAsset`in yasam dongusu, onu olusturan isten bagimsizdir -
    kabul edilmis (draft'a baglanmis) bir gorsel, uretim isi silinse bile
    gecerliligini korur.
    """

    job = await get_owned_job(session, organization_id, job_id)
    await session.delete(job)
    await session.flush()


# --- Is akisi (queue -> process -> reap -> purge) ------------------------------------


async def claim_next_queued(
    session: AsyncSession, limit: int = CLAIM_BATCH_SIZE
) -> list[DesignGenerationJob]:
    result = await session.execute(
        select(DesignGenerationJob)
        .where(DesignGenerationJob.status == DesignGenerationStatus.QUEUED)
        .order_by(DesignGenerationJob.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list(result.scalars().all())
    for job in jobs:
        job.status = DesignGenerationStatus.RUNNING
        job.started_at = _now()
        job.attempt_count += 1
    await session.flush()
    return jobs


async def process_job(
    session: AsyncSession,
    job: DesignGenerationJob,
    *,
    provider: BaseImageGenerationProvider | None = None,
) -> None:
    """Tek bir 'running' uretim isini sonuna kadar isler (basarili/basarisiz).

    Saglayicidan gelen HICBIR veri dogrudan guvenilmez: cikti
    `design_assets_service.store_generated_asset` uzerinden Package 1'in
    tam dogrulama hattindan gecirilir; bu asamada firlayan
    `InvalidImageError`/`ImageTooLargeError` de isin normal (beklenen)
    basarisizlik yollarindan biridir - hicbir yariya kalmis/gecersiz
    `DesignAsset` OLUSTURULMAZ.
    """

    active_provider = provider or get_image_generation_provider()

    source_asset = await session.get(DesignAsset, job.source_asset_id)
    if (
        source_asset is None
        or source_asset.organization_id != job.organization_id
        or source_asset.status != DesignAssetStatus.ACTIVE
        or source_asset.image_data is None
        or design_assets_service.is_expired(source_asset)
    ):
        job.status = DesignGenerationStatus.FAILED
        job.error_code = "source_asset_unavailable"
        job.error_message = SOURCE_ASSET_UNAVAILABLE_MESSAGE
        job.finished_at = _now()
        await session.flush()
        return

    try:
        generated = await active_provider.generate(
            reference_image=source_asset.image_data,
            reference_content_type=source_asset.content_type.value,
            prompt=job.prompt or "",
        )
    except Exception as exc:  # saglayici hatasi, zaman asimi, yapilandirma hatasi, vb.
        job.status = DesignGenerationStatus.FAILED
        job.error_code = type(exc).__name__
        job.error_message = "AI gorsel uretimi basarisiz oldu; lutfen tekrar deneyin."
        job.finished_at = _now()
        await session.flush()
        logger.warning("design_generation basarisiz (id=%s): %s", job.id, exc)
        return

    try:
        result_asset = await design_assets_service.store_generated_asset(
            session,
            organization_id=job.organization_id,
            raw_bytes=generated.raw_bytes,
            label=RESULT_ASSET_LABEL,
        )
    except (InvalidImageError, ImageTooLargeError) as exc:
        job.status = DesignGenerationStatus.FAILED
        job.error_code = type(exc).__name__
        job.error_message = "AI saglayicisindan gecerli bir gorsel alinamadi; lutfen tekrar deneyin."
        job.finished_at = _now()
        await session.flush()
        logger.warning("design_generation cikti dogrulamasi basarisiz (id=%s): %s", job.id, exc)
        return

    job.result_asset_id = result_asset.id
    job.provider_request_id = generated.provider_request_id
    job.status = DesignGenerationStatus.SUCCEEDED
    job.finished_at = _now()
    await session.flush()


async def reap_stale_running(
    session: AsyncSession,
    *,
    timeout_seconds: int | None = None,
    max_attempts: int | None = None,
) -> int:
    timeout_seconds = timeout_seconds or settings.design_generation_stale_timeout_seconds
    max_attempts = max_attempts or settings.design_generation_max_attempts

    cutoff = _now() - timedelta(seconds=timeout_seconds)
    result = await session.execute(
        select(DesignGenerationJob)
        .where(
            DesignGenerationJob.status == DesignGenerationStatus.RUNNING,
            DesignGenerationJob.updated_at < cutoff,
        )
        .with_for_update(skip_locked=True)
    )
    stale = list(result.scalars().all())

    for job in stale:
        if job.attempt_count < max_attempts:
            job.status = DesignGenerationStatus.QUEUED
            job.started_at = None
        else:
            job.status = DesignGenerationStatus.FAILED
            job.error_code = "retry_limit_exceeded"
            job.error_message = "Zaman asimi: maksimum deneme sayisina ulasildi"
            job.finished_at = _now()
        await session.flush()

    return len(stale)


async def purge_expired_jobs(session: AsyncSession) -> int:
    """Saklama suresi dolmus is kayitlarinin prompt metnini temizler (satir kalir).

    Bu, `design_assets.purge_expired_design_assets` ile ayni deseni izler:
    ikili gorsel veri zaten ayri bir `DesignAsset` satirinda (kendi purge
    cron'uyla) yonetilir; burada yalnizca potansiyel olarak hassas prompt
    metni NULL'a cekilir.
    """

    result = await session.execute(
        select(DesignGenerationJob).where(
            DesignGenerationJob.prompt.is_not(None),
            DesignGenerationJob.expires_at.is_not(None),
            DesignGenerationJob.expires_at < _now(),
        )
    )
    expired = list(result.scalars().all())
    for job in expired:
        job.prompt = None
    await session.flush()
    return len(expired)


async def _process_claimed_job(job_id: uuid.UUID) -> None:
    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(DesignGenerationJob).where(DesignGenerationJob.id == job_id).with_for_update()
            )
            job = result.scalar_one_or_none()
            if job is None or job.status != DesignGenerationStatus.RUNNING:
                return
            await process_job(session, job)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("process_job basarisiz oldu (id=%s)", job_id)

    async with async_session_maker() as session:
        try:
            result = await session.execute(
                select(DesignGenerationJob).where(DesignGenerationJob.id == job_id).with_for_update()
            )
            job = result.scalar_one_or_none()
            if job is not None and job.status == DesignGenerationStatus.RUNNING:
                job.status = DesignGenerationStatus.FAILED
                job.error_code = "unexpected_error"
                job.error_message = "Beklenmeyen bir hata olustu."
                job.finished_at = _now()
                await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("is %s icin basarisizlik kaydi da basarisiz oldu", job_id)


async def run_queue_cycle() -> None:
    async with async_session_maker() as session:
        try:
            jobs = await claim_next_queued(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("claim_next_queued basarisiz oldu")
            return

    for job in jobs:
        await _process_claimed_job(job.id)


async def run_reap_cycle() -> None:
    async with async_session_maker() as session:
        try:
            await reap_stale_running(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("reap_stale_running basarisiz oldu")


async def run_purge_cycle() -> None:
    async with async_session_maker() as session:
        try:
            await purge_expired_jobs(session)
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("purge_expired_jobs basarisiz oldu")


__all__ = [
    "CLAIM_BATCH_SIZE",
    "GENERATION_VERSION",
    "GeneratedImageResult",
    "BaseImageGenerationProvider",
    "NoneProvider",
    "RemoteHttpProvider",
    "get_image_generation_provider",
    "is_provider_configured",
    "create_generation_job",
    "get_owned_job",
    "cancel_job",
    "delete_job",
    "claim_next_queued",
    "process_job",
    "reap_stale_running",
    "purge_expired_jobs",
    "run_queue_cycle",
    "run_reap_cycle",
    "run_purge_cycle",
]
