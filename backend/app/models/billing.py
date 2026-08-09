import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class EntitlementStatus(str, enum.Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    CONSUMED = "consumed"


class ChipLedgerEntryType(str, enum.Enum):
    CREDIT = "credit"
    RESERVE = "reserve"
    CONSUME = "consume"
    RELEASE = "release"
    ADJUSTMENT = "adjustment"


class ChipReservationStatus(str, enum.Enum):
    RESERVED = "reserved"
    CONSUMED = "consumed"
    RELEASED = "released"


class ChipTopUpRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Entitlement(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir organizasyonun sahip oldugu ucretsiz ozellik/kota hakki.

    `quantity` her zaman 0 veya 1'dir (bu asamadaki ucretsiz haklar tekil
    kullanimlidir); `status` hakkin yasam dongusunu (available -> reserved ->
    consumed) izler. `reserved_run_id`/`reserved_until`, hakkin hangi is
    (run) icin ve ne zamana kadar rezerve edildigini tutar.
    """

    __tablename__ = "entitlements"
    __table_args__ = (
        UniqueConstraint("organization_id", "feature_key", name="uq_entitlements_org_feature"),
        Index("ix_entitlements_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    feature_key: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[EntitlementStatus] = mapped_column(
        SqlEnum(EntitlementStatus, name="entitlement_status"),
        nullable=False,
        default=EntitlementStatus.AVAILABLE,
    )
    reserved_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChipLedgerEntry(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Chip (kredi) bakiyesindeki degisiklikleri kaydeden ekle-sadece defter satiri.

    Guncel bakiye burada tutulmaz; bakiye bu tablo uzerinden (SUM(amount))
    hesaplanir. `entry_type`, hareketin turunu (credit/reserve/consume/
    release/adjustment) belirtir. `idempotency_key`, ayni isteğin (organizasyon
    basina) birden fazla kez uygulanmasini onlemek icin kullanilir.
    """

    __tablename__ = "chip_ledger_entries"
    __table_args__ = (
        Index("ix_chip_ledger_entries_organization_id", "organization_id"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_chip_ledger_entries_org_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[ChipLedgerEntryType] = mapped_column(
        SqlEnum(ChipLedgerEntryType, name="chip_ledger_entry_type"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ChipReservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir is (run) icin gecici olarak ayrilan (rezerve edilen) Chip miktari.

    Rezervasyon, ilgili `chip_ledger_entries` satirlarina (reserve/consume/
    release) `reference_type="chip_reservation"` ve `reference_id` uzerinden
    baglanir. Bakiye yine yalnizca defterden hesaplanir; bu tablo sadece
    rezervasyonun guncel durumunu (kilitlenebilir tek satir) tutar.
    """

    __tablename__ = "chip_reservations"
    __table_args__ = (
        Index("ix_chip_reservations_organization_id", "organization_id"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_chip_reservations_org_idem"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[ChipReservationStatus] = mapped_column(
        SqlEnum(ChipReservationStatus, name="chip_reservation_status"),
        nullable=False,
        default=ChipReservationStatus.RESERVED,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChipTopUpRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir organizasyonun Chip yukleme (top-up) talebi.

    Gercek bir odeme saglayicisi entegre edilmedigi icin bu satir Chip
    bakiyesini DOGRUDAN etkilemez (hicbir `chip_ledger_entries` satiri
    yazilmaz) - yalnizca "pending" durumda kaydedilir. Kart numarasi/CVV
    gibi hassas odeme verileri hicbir zaman burada veya baska bir yerde
    saklanmaz; yalnizca sabit, sunucu tarafinda tanimli bir paket anahtari
    (bkz. `app.services.chip_packages`) kaydedilir.
    """

    __tablename__ = "chip_topup_requests"
    __table_args__ = (Index("ix_chip_topup_requests_organization_id", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    package_key: Mapped[str] = mapped_column(String(100), nullable=False)
    chip_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ChipTopUpRequestStatus] = mapped_column(
        SqlEnum(ChipTopUpRequestStatus, name="chip_topup_request_status"),
        nullable=False,
        default=ChipTopUpRequestStatus.PENDING,
    )
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
