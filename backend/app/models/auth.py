import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Rotasyonlu (dondurulebilir) refresh oturum satiri.

    Ham (plaintext) refresh token hicbir yerde saklanmaz; yalnizca
    `token_hash` (sha256) tutulur. `session_id`, bir girisle baslayan ve her
    rotasyonda (refresh) tasinan rotasyon zincirini gruplar; `logout` veya
    tekrar kullanim (reuse) tespiti tum zinciri (session_id) iptal eder.
    `replaced_by_id`, bu satirin hangi yeni satirla degistirildigini tutar;
    bu sayede daha once rotasyona ugramis (dolayisiyla artik gecersiz) bir
    tokenin tekrar sunulmasi ("refresh token reuse") ayirt edilebilir.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_session_id", "session_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PasswordResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Parola sifirlama icin tek kullanimlik, kisa omurlu token.

    Ham token hicbir yerde saklanmaz (yalnizca sha256 hash'i); `used_at`
    dolduysa token bir daha kullanilamaz. Bu asamada gercek bir e-posta
    saglayicisi baglanmamistir: `/api/auth/password-reset/request`
    yalnizca `ENVIRONMENT=development` iken ham tokeni yanitta dondurur
    (bkz. docs/architecture.md); uretimde token sadece loglanir/saklanir,
    gonderim mekanizmasi sonraki bir asamada eklenecektir.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (Index("ix_password_reset_tokens_user_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
