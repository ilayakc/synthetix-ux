import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PageAnalysisStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PageAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Kullanicinin belirttigi bir URL icin tek seferlik, pasif (Playwright
    tabanli) sayfa analizi is kaydi (bkz. app.services.page_analysis).

    Ham HTML, form degerleri, cookie veya token hicbir zaman saklanmaz;
    `features` yalnizca turetilmis/redakte edilmis ozellikleri (baslik,
    basliklar, metin istatistikleri, kontrol sayilari, yaklasik element
    kutulari, performans zamanlari, kontrast adaylari, axe-core on kontrol
    ozeti) icerir - bkz. docs/security.md "Veri saklama ve redaksiyon".
    """

    __tablename__ = "page_analyses"
    __table_args__ = (
        Index("ix_page_analyses_organization_id", "organization_id"),
        Index("ix_page_analyses_status", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    # Kullanicinin bu URL'yi analiz etme yetkisine sahip oldugunu acikca
    # onayladigi (kendi beyanina dayali) bayrak; `false` iken is olusturulamaz
    # (bkz. app.routers.page_analysis).
    authorization_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[PageAnalysisStatus] = mapped_column(
        SqlEnum(PageAnalysisStatus, name="page_analysis_status"),
        nullable=False,
        default=PageAnalysisStatus.QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Sonuc: surumlu page_feature_snapshot (bkz. analyzer/app/schemas.py) ---
    snapshot_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analyzer_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    features: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Ekran goruntusu: sureli saklanir, ayri bir purge cron'u ile silinir
    # (bkz. app.services.page_analysis.purge_expired_screenshots); metadata
    # satiri kalir, yalnizca ikili veri ve son kullanma tarihi temizlenir. ---
    screenshot_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    screenshot_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
