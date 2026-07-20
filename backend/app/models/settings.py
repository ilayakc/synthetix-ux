import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Tema/dil/para birimi/cihaz profili degerleri kasitli olarak DB enum degil,
# duz String kolonlardir (bkz. `Membership.role`); dogrulama Pydantic/servis
# katmaninda yapilir, boylece yeni bir deger eklemek migration gerektirmez.


class UserPreferences(TimestampMixin, Base):
    """Kullaniciya ozel gorunum/bildirim/yerellestirme tercihleri (1 satir/kullanici)."""

    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="tr")
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Europe/Istanbul")
    theme: Mapped[str] = mapped_column(String(10), nullable=False, default="system")
    compact_view: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_simulation_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_simulation_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_report_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_low_chip_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    low_chip_balance_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)


class OrganizationSettings(TimestampMixin, Base):
    """Organizasyon geneli varsayilanlar: para birimi + yeni test sihirbazi varsayilanlari."""

    __tablename__ = "organization_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="TRY")
    default_persona_count: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    default_persona_preset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    default_device_profile: Mapped[str | None] = mapped_column(String(50), nullable=True)
    default_modules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    default_target_audience: Mapped[str | None] = mapped_column(Text, nullable=True)
