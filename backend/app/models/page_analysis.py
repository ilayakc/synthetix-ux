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


class PageAnalysisSourceKind(str, enum.Enum):
    URL = "url"
    DESIGN_ASSET = "design_asset"


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
        Index("ix_page_analyses_design_asset_id", "design_asset_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Ortak kaynak sozlesmesi: tam olarak biri dolu olur (bkz.
    # app.services.page_analysis.create_analysis). `source_kind` istemciden
    # degil, sunucu tarafinda gonderilen alandan (url/design_asset_id) turetilir.
    source_kind: Mapped[PageAnalysisSourceKind] = mapped_column(
        SqlEnum(PageAnalysisSourceKind, name="page_analysis_source_kind"),
        nullable=False,
        default=PageAnalysisSourceKind.URL,
    )
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # `design_assets.id` HARD DELETE edilmez (yalnizca soft-delete: status=deleted,
    # image_data=None - bkz. app.services.design_assets.delete_asset); yine de
    # kalici bir PageAnalysis snapshot'inin, orijinal DesignAsset satiri ileride
    # hard-delete edilirse KAYBOLMAMASI icin ondelete="SET NULL" kullanilir (asla
    # CASCADE degil) - `screenshot_data` zaten bagimsiz bir kopya oldugundan bu
    # kolon NULL'a dustugunde bile preview calismaya devam eder.
    design_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("design_assets.id", ondelete="SET NULL"), nullable=True
    )
    # Kullanicinin bu URL'yi analiz etme yetkisine sahip oldugunu acikca
    # onayladigi (kendi beyanina dayali) bayrak; yalnizca `source_kind=url` icin
    # anlamlidir - `false` iken URL kaynakli is olusturulamaz (bkz.
    # app.routers.page_analysis). DesignAsset kaynaginda bu kavram gecerli
    # degildir (kullanici zaten kendi yukledigi gorseli analiz ediyor).
    authorization_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[PageAnalysisStatus] = mapped_column(
        SqlEnum(PageAnalysisStatus, name="page_analysis_status"),
        nullable=False,
        default=PageAnalysisStatus.QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Gecici hatadan (429/timeout/5xx/baglanti) sonra KALICI, gecikmeli yeniden
    # deneme zamani. `queued` bir is yalnizca `next_attempt_at` NULL ya da
    # gecmisteyse alinir (bkz. app.services.page_analysis.claim_next_queued);
    # boylece worker restart/redeploy olsa bile geri-cekilme suresi kaybolmaz ve
    # worker `sleep` ile bloke edilmeden diger isleri tuketmeye devam eder.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ham HTTP/provider govdesi yerine yalnizca allowlist'li, makinece
    # okunabilir hata sinifi saklanir (ornegin `empty_page_snapshot`).
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- Sonuc: surumlu page_feature_snapshot (bkz. analyzer/app/schemas.py) ---
    snapshot_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    analyzer_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    features: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Ekran goruntusu: sureli saklanir, ayri bir purge cron'u ile silinir
    # (bkz. app.services.page_analysis.purge_expired_screenshots); metadata
    # satiri kalir, yalnizca ikili veri ve son kullanma tarihi temizlenir.
    # Kaynak turunden BAGIMSIZ, degismez (immutable) bir snapshot'tir - bir
    # DesignAsset kaynagindan kopyalandiginda orijinal asset SONRADAN
    # silinse/expire olsa bile bu kopya kendi retention suresi boyunca etkilenmez. ---
    screenshot_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    screenshot_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Guvenilir saklanan metadata veya gercek decode sonucundan alinir - istemci
    # veya analyzer'in bildirdigi degere asla dogrudan guvenilmez (bkz. servis).
    screenshot_content_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # SHA-256(screenshot_data) - yalnizca BYTE-duzeyinde eslesme anlamina gelir,
    # perceptual/gorsel benzerlik DEGILDIR ve bir yetkilendirme anahtari degildir
    # (bkz. app.services.page_analysis modul dokstring'i).
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
