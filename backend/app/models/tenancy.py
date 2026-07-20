import uuid

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir musteri hesabini (kiraciyi) temsil eder."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Global kullanici kimligi. Bir organizasyona `memberships` uzerinden baglanir.

    Parola/oturum alanlari ve dogrulama mantigi sonraki bir asamada eklenecektir.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir kullanicinin bir organizasyondaki uyeligini ve rolunu temsil eder."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_memberships_org_user"),
        Index("ix_memberships_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="member")

    organization: Mapped["Organization"] = relationship()
    user: Mapped["User"] = relationship()
