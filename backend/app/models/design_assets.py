import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DesignAssetContentType(str, enum.Enum):
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"


class DesignAssetStatus(str, enum.Enum):
    ACTIVE = "active"
    DELETED = "deleted"


class DesignAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Kullanicinin yukledigi bir tasarim ekran goruntusu (bkz. app.services.design_assets).

    `page_analyses.screenshot_data` ile ayni depolama desenini izler
    (Postgres `LargeBinary`, sureli saklama, ayri bir purge cron'u ile
    temizlenir). Kullanicinin yukledigi orijinal dosya adi HICBIR ZAMAN
    saklanmaz (yalnizca opsiyonel bir kullanici etiketi); rastgele `id`
    (UUID) guvenli dosya kimligi olarak kullanilir. Yalnizca gercekten
    decode edilerek dogrulanmis, EXIF'i temizlenmis, yeniden encode
    edilmis piksel verisi saklanir (bkz. docs/security.md).
    """

    __tablename__ = "design_assets"
    __table_args__ = (
        Index("ix_design_assets_organization_id", "organization_id"),
        Index("ix_design_assets_status", "status"),
        Index("ix_design_assets_expires_at", "expires_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    content_type: Mapped[DesignAssetContentType] = mapped_column(
        SqlEnum(DesignAssetContentType, name="design_asset_content_type"), nullable=False
    )
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Kullanicinin verdigi opsiyonel etiket ("Tasarim A" gibi) - dosya adi DEGILDIR.
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)

    status: Mapped[DesignAssetStatus] = mapped_column(
        SqlEnum(DesignAssetStatus, name="design_asset_status"),
        nullable=False,
        default=DesignAssetStatus.ACTIVE,
    )

    # Ekran goruntusu: sureli saklanir, ayri bir purge cron'u ile silinir
    # (bkz. app.services.design_assets.purge_expired_design_assets); metadata
    # satiri kalir, yalnizca ikili veri ve son kullanma tarihi temizlenir.
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
