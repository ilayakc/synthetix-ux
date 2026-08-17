"""Ucretsiz kullanim haklari (entitlement) is mantigi.

Her organizasyon, `pricing.FREE_ENTITLEMENT_FEATURE_KEYS` icindeki her
feature_key icin en fazla bir adet ucretsiz hak satirina sahiptir
(quantity=1). Hak, `available -> reserved -> consumed` yasam dongusunu
izler; tek kullanimlik oldugu icin bir kez tuketildikten sonra ayni
organizasyon o ozelligi bir daha ucretsiz kullanamaz.

Bu satirlar organizasyon ilk olusturuldugunda degil, ilk erisildiklerinde
("get or create") olusturulur; `ensure_free_entitlements` bunu acikca
(ornegin seed/usage-summary akisinda) tetiklemek icin kullanilabilir.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import Entitlement, EntitlementStatus
from app.services.exceptions import (
    EntitlementNotFoundError,
    EntitlementUnavailableError,
    InvalidEntitlementStateError,
)
from app.services.pricing import FREE_ENTITLEMENT_FEATURE_KEYS

DEFAULT_RESERVATION_TTL_SECONDS = 3600


async def get_or_create_entitlement(
    session: AsyncSession, organization_id: uuid.UUID, feature_key: str
) -> Entitlement:
    """Verilen ucretsiz hakki dondurur; yoksa `available`/quantity=1 olarak olusturur.

    `with_for_update()`, satir ZATEN varsa gelecekteki es zamanli okumalari
    serilestirir - ama satir HENUZ yoksa kilitlenecek bir satir olmadigi icin
    hicbir koruma saglamaz (TOCTOU): iki es zamanli istek ikisi de `None`
    gorup ikisi de INSERT denerse, ikincisi `uq_entitlements_org_feature`
    benzersizlik kisitini ihlal eder. Bu, gercek bir production hatasi
    olarak gozlemlenmistir (bkz. app.services.settings
    .get_or_create_organization_settings docstring'i - ayni desen, launch
    akisinin (`app.services.test_wizard.launch_draft` ->
    `reserve_entitlement`) TAM ICINDE): bu yuzden INSERT bir SAVEPOINT
    icinde denenir, cakisma olursa yalnizca SAVEPOINT geri alinir ve satir
    guvenle yeniden okunur - cagirana hicbir zaman 500 olarak sizmaz."""

    result = await session.execute(
        select(Entitlement)
        .where(Entitlement.organization_id == organization_id, Entitlement.feature_key == feature_key)
        .with_for_update()
    )
    entitlement = result.scalar_one_or_none()
    if entitlement is not None:
        return entitlement

    try:
        async with session.begin_nested():
            entitlement = Entitlement(
                organization_id=organization_id,
                feature_key=feature_key,
                quantity=1,
                status=EntitlementStatus.AVAILABLE,
            )
            session.add(entitlement)
            await session.flush()
    except IntegrityError:
        result = await session.execute(
            select(Entitlement)
            .where(Entitlement.organization_id == organization_id, Entitlement.feature_key == feature_key)
            .with_for_update()
        )
        entitlement = result.scalar_one_or_none()
        if entitlement is None:
            raise
    return entitlement


async def list_free_entitlements(session: AsyncSession, organization_id: uuid.UUID) -> list[Entitlement]:
    """Organizasyonun iki ucretsiz hakkini (yoksa olusturarak) dondurur."""

    return [
        await get_or_create_entitlement(session, organization_id, feature_key)
        for feature_key in FREE_ENTITLEMENT_FEATURE_KEYS
    ]


def _is_reservation_expired(entitlement: Entitlement) -> bool:
    return (
        entitlement.status == EntitlementStatus.RESERVED
        and entitlement.reserved_until is not None
        and entitlement.reserved_until < datetime.now(UTC)
    )


async def reserve_entitlement(
    session: AsyncSession,
    organization_id: uuid.UUID,
    feature_key: str,
    run_id: uuid.UUID,
    *,
    ttl_seconds: int = DEFAULT_RESERVATION_TTL_SECONDS,
) -> Entitlement:
    """Ucretsiz hakki bir is (run) icin rezerve eder.

    Hak zaten tuketilmisse `EntitlementUnavailableError` firlatir (tek
    kullanim kurali). Ayni `run_id` ile tekrar cagrilirsa (idempotent
    retry) mevcut rezervasyonu degistirmeden dondurur. Suresi gecmis bir
    rezervasyon otomatik olarak serbest sayilir ve yeniden rezerve edilebilir.
    """

    entitlement = await get_or_create_entitlement(session, organization_id, feature_key)

    if _is_reservation_expired(entitlement):
        entitlement.status = EntitlementStatus.AVAILABLE
        entitlement.reserved_run_id = None
        entitlement.reserved_until = None

    if entitlement.status == EntitlementStatus.RESERVED:
        if entitlement.reserved_run_id == run_id:
            return entitlement
        raise EntitlementUnavailableError(f"'{feature_key}' hakki baska bir is icin rezerve edilmis durumda")

    if entitlement.status == EntitlementStatus.CONSUMED:
        # Idempotent yeniden kullanim: hak ZATEN bu is (run_id) tarafindan
        # tuketilmisse, bu bir yeniden-rezervasyon (ornegin sistem/worker hatasi
        # sonrasi ayni run'in `retry_run`i) yeni bir ucretsiz kullanim YARATMAZ -
        # mevcut tuketimi (CONSUMED) degistirmeden dondurur. Boylece ayni run'in
        # retry'si "daha once kullanildi" (409) ile ikinci kez hak talep etmez ve
        # entitlement iki kez tuketilmez (bkz. app.services.simulation_worker.
        # retry_run). BASKA bir is tarafindan tuketildiyse hala reddedilir (tek
        # kullanim kurali - hak gercekten tukenmistir).
        if entitlement.reserved_run_id == run_id:
            return entitlement
        raise EntitlementUnavailableError(f"'{feature_key}' hakki daha once kullanildi")

    entitlement.status = EntitlementStatus.RESERVED
    entitlement.reserved_run_id = run_id
    entitlement.reserved_until = datetime.now(UTC) + timedelta(seconds=ttl_seconds) if ttl_seconds else None
    await session.flush()
    return entitlement


async def consume_entitlement(
    session: AsyncSession, organization_id: uuid.UUID, feature_key: str, run_id: uuid.UUID
) -> Entitlement:
    """Rezerve edilmis ucretsiz hakki kesin olarak tuketir (is basariyla tamamlandi)."""

    result = await session.execute(
        select(Entitlement)
        .where(Entitlement.organization_id == organization_id, Entitlement.feature_key == feature_key)
        .with_for_update()
    )
    entitlement = result.scalar_one_or_none()
    if entitlement is None:
        raise EntitlementNotFoundError(f"Hak bulunamadi: {feature_key}")

    if entitlement.status == EntitlementStatus.CONSUMED:
        return entitlement
    if entitlement.status != EntitlementStatus.RESERVED or entitlement.reserved_run_id != run_id:
        raise InvalidEntitlementStateError(f"'{feature_key}' hakki bu is (run) tarafindan tuketilemez")

    entitlement.status = EntitlementStatus.CONSUMED
    entitlement.reserved_until = None
    await session.flush()
    return entitlement


async def release_entitlement(
    session: AsyncSession, organization_id: uuid.UUID, feature_key: str, run_id: uuid.UUID
) -> Entitlement:
    """Rezerve edilmis ucretsiz hakki serbest birakir (basarisiz/iptal/hic baslamayan is).

    Politika geregi rezerve edilmis ama kullanilmamis hak guvenli bicimde
    tekrar `available` durumuna doner; ikinci kez kullanim hakki dogurmaz.
    """

    result = await session.execute(
        select(Entitlement)
        .where(Entitlement.organization_id == organization_id, Entitlement.feature_key == feature_key)
        .with_for_update()
    )
    entitlement = result.scalar_one_or_none()
    if entitlement is None:
        raise EntitlementNotFoundError(f"Hak bulunamadi: {feature_key}")

    if entitlement.status == EntitlementStatus.AVAILABLE:
        return entitlement
    if entitlement.status != EntitlementStatus.RESERVED or entitlement.reserved_run_id != run_id:
        raise InvalidEntitlementStateError(
            f"'{feature_key}' hakki bu is (run) tarafindan serbest birakilamaz"
        )

    entitlement.status = EntitlementStatus.AVAILABLE
    entitlement.reserved_run_id = None
    entitlement.reserved_until = None
    await session.flush()
    return entitlement
