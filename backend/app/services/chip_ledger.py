"""Chip bakiyesi ve rezervasyon is mantigi.

Bakiye hicbir yerde ayri bir kolonda tutulmaz; her zaman
`chip_ledger_entries` uzerinden `SUM(amount)` ile hesaplanir
(bkz. `app.models.billing.ChipLedgerEntry`). Rezervasyon/tuketim akisi:

    reserve()  -> ledger'a negatif (-amount) bir 'reserve' satiri yazar ve
                  `chip_reservations` icinde durumu 'reserved' olan bir
                  satir olusturur. Bakiye hemen dusuruldugu icin ayni
                  Chip'in iki kez rezerve edilmesi engellenmis olur.
    consume()  -> rezervasyonu 'consumed' yapar, ledger'a bilgi amacli
                  (amount=0) bir 'consume' satiri ekler. Bakiye degismez
                  (zaten reserve asamasinda dusulmustu).
    release()  -> rezervasyonu 'released' yapar, ledger'a rezerve edilen
                  miktari geri ekleyen (+amount) bir 'release' satiri
                  yazar. Basarisiz/iptal edilen/hic baslamayan isler icin
                  kullanilir.

Es zamanlilik: `reserve()` her zaman once organizasyon satirini
`SELECT ... FOR UPDATE` ile kilitler; ayni organizasyon icin yarisan
istekler serilestirilir ve bakiye asla negatife dusmez. `consume()`/
`release()` ise ilgili rezervasyon satirini kilitler.

Idempotency: `idempotency_key` verildiginde, ayni organizasyon+anahtar
kombinasyonu icin islem daha once uygulanmissa yeni bir yan etki
yaratilmadan mevcut sonuc dondurulur (guvenli tekrar deneme).
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import ChipLedgerEntry, ChipLedgerEntryType, ChipReservation, ChipReservationStatus
from app.models.tenancy import Organization
from app.services.exceptions import (
    ChipReservationNotFoundError,
    InsufficientChipBalanceError,
    InvalidChipReservationStateError,
    OrganizationNotFoundError,
)

DEFAULT_RESERVATION_TTL_SECONDS = 3600


async def _lock_organization(session: AsyncSession, organization_id: uuid.UUID) -> None:
    result = await session.execute(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    if result.scalar_one_or_none() is None:
        raise OrganizationNotFoundError(f"Organizasyon bulunamadi: {organization_id}")


async def get_chip_balance(session: AsyncSession, organization_id: uuid.UUID) -> int:
    """Organizasyonun guncel Chip bakiyesini defterden toplayarak hesaplar."""

    result = await session.execute(
        select(func.coalesce(func.sum(ChipLedgerEntry.amount), 0)).where(
            ChipLedgerEntry.organization_id == organization_id
        )
    )
    return int(result.scalar_one())


async def _find_ledger_entry_by_idempotency_key(
    session: AsyncSession, organization_id: uuid.UUID, idempotency_key: str
) -> ChipLedgerEntry | None:
    result = await session.execute(
        select(ChipLedgerEntry).where(
            ChipLedgerEntry.organization_id == organization_id,
            ChipLedgerEntry.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def credit(
    session: AsyncSession,
    organization_id: uuid.UUID,
    amount: int,
    reason: str,
    *,
    idempotency_key: str | None = None,
    reference_type: str | None = None,
    reference_id: uuid.UUID | None = None,
) -> ChipLedgerEntry:
    """Organizasyona Chip ekler (ornegin dev/admin test kredisi, adjustment).

    Halka acik bir "bedava chip ekle" API'si yoktur; bu fonksiyon yalnizca
    seed/admin komutlari veya (ileride) gercek satin alma entegrasyonu
    tarafindan cagrilmalidir.
    """

    if amount <= 0:
        raise ValueError("credit miktari pozitif olmalidir")

    await _lock_organization(session, organization_id)

    if idempotency_key is not None:
        existing = await _find_ledger_entry_by_idempotency_key(session, organization_id, idempotency_key)
        if existing is not None:
            return existing

    entry = ChipLedgerEntry(
        organization_id=organization_id,
        amount=amount,
        entry_type=ChipLedgerEntryType.CREDIT,
        reason=reason,
        reference_type=reference_type,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _find_reservation_by_idempotency_key(
    session: AsyncSession, organization_id: uuid.UUID, idempotency_key: str
) -> ChipReservation | None:
    result = await session.execute(
        select(ChipReservation).where(
            ChipReservation.organization_id == organization_id,
            ChipReservation.idempotency_key == idempotency_key,
        )
    )
    return result.scalar_one_or_none()


async def reserve_chips(
    session: AsyncSession,
    organization_id: uuid.UUID,
    amount: int,
    reason: str,
    *,
    run_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    ttl_seconds: int = DEFAULT_RESERVATION_TTL_SECONDS,
) -> ChipReservation:
    """Belirtilen miktarda Chip'i bir is (run) icin rezerve eder.

    Bakiye yetersizse `InsufficientChipBalanceError` firlatir; negatif
    bakiye asla olusturulmaz. Ayni `idempotency_key` ile tekrar cagrilirsa
    yeni bir rezervasyon olusturmadan mevcut olani dondurur.
    """

    if amount <= 0:
        raise ValueError("reserve miktari pozitif olmalidir")

    # Organizasyon satirini kilitleyerek ayni organizasyon icin yarisan
    # rezervasyon isteklerini serilestiriyoruz; boylece bakiye kontrolu ve
    # yeni satirin eklenmesi atomik olur.
    await _lock_organization(session, organization_id)

    if idempotency_key is not None:
        existing = await _find_reservation_by_idempotency_key(session, organization_id, idempotency_key)
        if existing is not None:
            return existing

    balance = await get_chip_balance(session, organization_id)
    if balance < amount:
        raise InsufficientChipBalanceError(f"Yetersiz Chip bakiyesi: mevcut={balance}, istenen={amount}")

    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds) if ttl_seconds else None

    reservation = ChipReservation(
        organization_id=organization_id,
        amount=amount,
        status=ChipReservationStatus.RESERVED,
        run_id=run_id,
        idempotency_key=idempotency_key,
        expires_at=expires_at,
    )
    session.add(reservation)
    await session.flush()

    session.add(
        ChipLedgerEntry(
            organization_id=organization_id,
            amount=-amount,
            entry_type=ChipLedgerEntryType.RESERVE,
            reason=reason,
            reference_type="chip_reservation",
            reference_id=reservation.id,
            idempotency_key=idempotency_key,
        )
    )
    await session.flush()
    return reservation


async def _lock_reservation(
    session: AsyncSession, organization_id: uuid.UUID, reservation_id: uuid.UUID
) -> ChipReservation:
    result = await session.execute(
        select(ChipReservation)
        .where(
            ChipReservation.id == reservation_id,
            ChipReservation.organization_id == organization_id,
        )
        .with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if reservation is None:
        raise ChipReservationNotFoundError(f"Rezervasyon bulunamadi: {reservation_id}")
    return reservation


async def consume_reservation(
    session: AsyncSession,
    organization_id: uuid.UUID,
    reservation_id: uuid.UUID,
    reason: str,
) -> ChipReservation:
    """Rezerve edilmis Chip'leri kesin olarak tuketir (is basariyla tamamlandi)."""

    reservation = await _lock_reservation(session, organization_id, reservation_id)

    if reservation.status == ChipReservationStatus.CONSUMED:
        return reservation
    if reservation.status != ChipReservationStatus.RESERVED:
        raise InvalidChipReservationStateError(
            f"Rezervasyon '{reservation.status.value}' durumundan tuketilemez"
        )

    reservation.status = ChipReservationStatus.CONSUMED
    session.add(
        ChipLedgerEntry(
            organization_id=organization_id,
            amount=0,
            entry_type=ChipLedgerEntryType.CONSUME,
            reason=reason,
            reference_type="chip_reservation",
            reference_id=reservation.id,
        )
    )
    await session.flush()
    return reservation


async def release_reservation(
    session: AsyncSession,
    organization_id: uuid.UUID,
    reservation_id: uuid.UUID,
    reason: str,
) -> ChipReservation:
    """Rezerve edilmis Chip'leri geri serbest birakir (basarisiz/iptal/hic baslamayan is)."""

    reservation = await _lock_reservation(session, organization_id, reservation_id)

    if reservation.status == ChipReservationStatus.RELEASED:
        return reservation
    if reservation.status != ChipReservationStatus.RESERVED:
        raise InvalidChipReservationStateError(
            f"Rezervasyon '{reservation.status.value}' durumundan serbest birakilamaz"
        )

    reservation.status = ChipReservationStatus.RELEASED
    session.add(
        ChipLedgerEntry(
            organization_id=organization_id,
            amount=reservation.amount,
            entry_type=ChipLedgerEntryType.RELEASE,
            reason=reason,
            reference_type="chip_reservation",
            reference_id=reservation.id,
        )
    )
    await session.flush()
    return reservation
