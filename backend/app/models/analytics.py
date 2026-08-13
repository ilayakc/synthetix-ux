"""Ziyaretci/trafik analitigi modelleri (yalnizca platform yoneticisi gorur).

TASARIM / GIZLILIK (bkz. docs/security.md "Ziyaretci ve trafik analitigi" ve
docs/product-rules.md):

- Bu tablolar KVKK acisindan olculu, gizlilik dostu bir tasariml
  ir: HAM IP adresi, tam user-agent metni, parola/token/cookie icerigi, form
  alanlari, sayfa icerigi veya hassas URL query parametreleri (access_token,
  refresh_token, token, code, password, email ...) HICBIR ZAMAN saklanmaz.
  Fingerprinting (canvas/font/donanim) YAPILMAZ.
- Anonim ziyaretci, sunucunun urettigi rastgele bir first-party `visitor_id`
  (bir HttpOnly cookie'de tasinan UUID) ile temsil edilir; istemci bu degeri
  gorup degistiremez.
- Bu analitik olaylari, `audit_logs` (per-tenant, guvenlik/denetim) ile
  KARISTIRILMAZ - amaci farklidir (toplam trafik olcumu), ayri tablolarda tutulur.
- Kullanici/organizasyon iliskileri her zaman mevcut `users`/`memberships`/
  `organizations` tablolarina FK ile baglanir; gosterim adlari istemciden gelen
  degerlere DEGIL, dogrulanmis backend join'lerine dayanir.

Retention: `analytics_*` satirlari `ANALYTICS_RETENTION_DAYS` sonrasi guvenli bir
cleanup cron'u ile silinir (bkz. app.services.analytics.purge_expired). Edinim
(acquisition) bilgisi, ziyaretci satiri silinse bile korunacak sekilde
`user_acquisition_attribution` / `organization_acquisition_attribution` tablolarina
kayit aninda denormalize kopyalanir.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AnalyticsEventType(str, enum.Enum):
    """Kontrollu (enum) olay turleri.

    Yalnizca `PAGE_VIEW` ve `VISITOR_SESSION_STARTED` istemciden (public
    ingestion ucundan) kabul edilir; digerleri YALNIZCA sunucu tarafinda,
    ilgili is olayinin gerceklestigi transaction icinde kaydedilir (bkz.
    app.routers.analytics.ALLOWED_CLIENT_EVENT_TYPES)."""

    PAGE_VIEW = "page_view"
    VISITOR_SESSION_STARTED = "visitor_session_started"
    SIGNUP_STARTED = "signup_started"
    SIGNUP_COMPLETED = "signup_completed"
    LOGIN_SUCCEEDED = "login_succeeded"
    # Guvenlik: basarisiz giris YALNIZCA toplam sayaç icindir - kullanici
    # kimligi/e-posta/parola ASLA bu olaya yazilmaz (bkz. app.routers.auth).
    LOGIN_FAILED_SECURITY_SUMMARY = "login_failed_security_summary"
    LOGOUT = "logout"
    ORGANIZATION_CREATED = "organization_created"
    FIRST_PROJECT_CREATED = "first_project_created"
    FIRST_TEST_STARTED = "first_test_started"
    FIRST_TEST_COMPLETED = "first_test_completed"


# Cihaz kategorisi (dusuk kardinalite, fingerprinting DEGIL - yalnizca kaba
# desktop/mobile/tablet ayrimi).
DEVICE_CATEGORIES = ("desktop", "mobile", "tablet", "unknown")


class AnalyticsVisitor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Anonim ziyaretci kimligi + first-touch edinim (acquisition) anlik goruntusu.

    `id`, sunucunun urettigi ve HttpOnly `analytics_vid` cookie'sinde tasinan
    rastgele UUID'dir. `created_at` ilk gorulme (first seen), `last_seen_at`
    son gorulme zamanidir. `first_touch_*` alanlari, ziyaretcinin ILK
    oturumundaki edinim kaynagini (bir daha degismez) tutar.
    """

    __tablename__ = "analytics_visitors"
    __table_args__ = (Index("ix_analytics_visitors_last_seen_at", "last_seen_at"),)

    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # First-touch edinim (ilk oturumda yakalanir, degismez).
    first_utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_landing_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Son bilinen (dusuk kardinalite) cihaz/tarayici/OS - fingerprint DEGIL.
    device_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    browser_family: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_family: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Ziyaretci kayit olduysa: dogrulanmis kullanici/organizasyona baglanir
    # (SET NULL - kullanici/organizasyon silinse bile analitik satiri bozulmaz).
    linked_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    linked_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )


class AnalyticsSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir ziyaretcinin tekil oturumu (visit). `created_at` = oturum baslangici.

    Oturum, `analytics_vsid` (session-scoped) cookie'siyle izlenir; giris
    (entry) edinim alanlari o oturumun ILK olayindan alinir ve last-touch
    attribution icin kullanilir.
    """

    __tablename__ = "analytics_sessions"
    __table_args__ = (
        Index("ix_analytics_sessions_visitor_id", "visitor_id"),
        Index("ix_analytics_sessions_last_event_at", "last_event_at"),
    )

    visitor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analytics_visitors.id", ondelete="CASCADE"), nullable=False
    )
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landing_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    device_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    browser_family: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_family: Mapped[str | None] = mapped_column(String(50), nullable=True)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )


class AnalyticsEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Ekle-sadece (append-only) analitik olay kaydi. `created_at` = gerceklesme zamani.

    HICBIR ZAMAN ham IP / tam user-agent / hassas query / sayfa icerigi tutmaz.
    `dedup_key` (varsa) UNIQUE'tir: ayni HTTP istegi veya frontend yeniden
    denemesi olayi iki kez kaydetmesin diye idempotency saglar.
    """

    __tablename__ = "analytics_events"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_analytics_events_dedup_key"),
        Index("ix_analytics_events_created_at", "created_at"),
        Index("ix_analytics_events_event_type_created_at", "event_type", "created_at"),
        Index("ix_analytics_events_visitor_id", "visitor_id"),
        Index("ix_analytics_events_session_id", "session_id"),
        Index("ix_analytics_events_user_id", "user_id"),
        Index("ix_analytics_events_organization_id", "organization_id"),
        Index("ix_analytics_events_referral_code", "referral_code"),
        Index("ix_analytics_events_utm_campaign", "utm_campaign"),
    )

    event_type: Mapped[AnalyticsEventType] = mapped_column(
        SqlEnum(AnalyticsEventType, name="analytics_event_type"), nullable=False
    )
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analytics_visitors.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analytics_sessions.id", ondelete="SET NULL"), nullable=True
    )

    path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    device_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    browser_family: Mapped[str | None] = mapped_column(String(50), nullable=True)
    os_family: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Ulke YALNIZCA guvenilir bir reverse-proxy header'i mevcutsa doldurulur;
    # aksi halde IP'den ulke tespiti icin yeni bir ucuncu taraf servise veri
    # GONDERILMEZ - alan bos birakilir (bkz. app.services.analytics).
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )

    dedup_key: Mapped[str | None] = mapped_column(String(200), nullable=True)


class TrackingLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Yonetici tarafindan olusturulan UTM/referral takip baglantisi.

    `referral_code`, tahmin edilmesi kolay sirali bir ID DEGIL, rastgele
    uretilmis (yuksek entropili) bir token'dir. `destination_path`, YALNIZCA
    izin verilen dahili bir yol olabilir (open redirect'e karsi; bkz.
    app.services.analytics.validate_internal_path) - hicbir mutlak/harici URL
    kabul edilmez.
    """

    __tablename__ = "tracking_links"
    __table_args__ = (
        UniqueConstraint("referral_code", name="uq_tracking_links_referral_code"),
        Index("ix_tracking_links_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="/")
    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    referral_code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class UserAcquisitionAttribution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir kullanicinin edinim (acquisition) kaynagi - kayit aninda bir kez yazilir.

    Anonim ziyaretcinin geçmişindeki gereksiz ayrintilar profile TASINMAZ;
    yalnizca first-touch (ziyaretcinin ilk kaynagi) ve last-touch (kayit
    oturumunun kaynagi) edinim alanlari denormalize kopyalanir - boylece
    ziyaretci satiri retention ile silinse bile edinim bilgisi korunur.
    """

    __tablename__ = "user_acquisition_attribution"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_acquisition_attribution_user_id"),
        Index("ix_user_acquisition_attribution_first_campaign", "first_utm_campaign"),
        Index("ix_user_acquisition_attribution_last_campaign", "last_utm_campaign"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analytics_visitors.id", ondelete="SET NULL"), nullable=True
    )

    first_utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)


class OrganizationAcquisitionAttribution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir organizasyonun edinim kaynagi - organizasyon olusturuldugunda bir kez yazilir.

    `UserAcquisitionAttribution` ile ayni denormalize desen; edinim bilgisini
    kullanicidan BAGIMSIZ, organizasyon duzeyinde tutar.
    """

    __tablename__ = "organization_acquisition_attribution"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_organization_acquisition_attribution_organization_id"),
        Index("ix_org_acquisition_attribution_first_campaign", "first_utm_campaign"),
        Index("ix_org_acquisition_attribution_last_campaign", "last_utm_campaign"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analytics_visitors.id", ondelete="SET NULL"), nullable=True
    )

    first_utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    first_referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    first_referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_referral_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_referrer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = [
    "AnalyticsEventType",
    "DEVICE_CATEGORIES",
    "AnalyticsVisitor",
    "AnalyticsSession",
    "AnalyticsEvent",
    "TrackingLink",
    "UserAcquisitionAttribution",
    "OrganizationAcquisitionAttribution",
]
