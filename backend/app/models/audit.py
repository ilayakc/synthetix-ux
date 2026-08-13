import uuid

from sqlalchemy import JSON, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# Kullanicilarin sisteme girisini (kayit + parola girisi + demo giris) kaydeden
# ekle-sadece denetim eylemi. Yazma tarafi (app.routers.auth) ve okuma tarafi
# (app.routers.admin giris gecmisi) arasindaki yazim tutarliligini garanti eder.
USER_LOGIN_ACTION = "user_login"


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Organizasyon icindeki onemli eylemleri kaydeden ekle-sadece iz kaydi."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_organization_id", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    entry_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
