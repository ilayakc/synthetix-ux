import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DesignGenerationStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DesignGenerationJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """AI ile alternatif tasarim (Tasarim B) varyanti uretme isi (bkz.
    app.services.design_generation).

    Kaynak (`source_asset_id`) her zaman zaten Package 1'in guvenli
    dogrulama hattindan gecmis, bu organizasyona ait bir `DesignAsset`dir.
    Basarili sonuc (`result_asset_id`) de AYNI hattan (decode/format
    dogrulama/boyut-piksel siniri/coklu-kare reddi/metadata temizleme/
    yeniden encode) gecirilerek olusturulan yeni bir `DesignAsset`dir - bu
    modelde ikili gorsel veri HICBIR ZAMAN saklanmaz.

    `prompt`, kullanicinin serbest metin talebidir; ham sayfa icerigi veya
    kimlik dogrulama verisi degildir, ancak yine de sureli saklanir ve
    isin/gorselin saklama suresi (`expires_at`) doldugunda `NULL`'a
    cekilir (bkz. docs/security.md "AI ile tasarim uretimi - saklama").
    """

    __tablename__ = "design_generation_jobs"
    __table_args__ = (
        Index("ix_design_generation_jobs_organization_id", "organization_id"),
        Index("ix_design_generation_jobs_status", "status"),
        Index("ix_design_generation_jobs_expires_at", "expires_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("design_assets.id", ondelete="CASCADE"), nullable=False
    )
    result_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("design_assets.id", ondelete="SET NULL"), nullable=True
    )

    status: Mapped[DesignGenerationStatus] = mapped_column(
        SqlEnum(DesignGenerationStatus, name="design_generation_status"),
        nullable=False,
        default=DesignGenerationStatus.QUEUED,
    )

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
