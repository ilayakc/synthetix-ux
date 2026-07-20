import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Tum ORM modelleri icin ortak taban sinif."""


class UUIDPrimaryKeyMixin:
    """UUID birincil anahtar saglar. Deger uygulama tarafinda uretilir."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """UTC olarak olusturulma/guncellenme zaman damgalari saglar."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedAtMixin:
    """Yalnizca olusturulma zaman damgasi saglar (append-only kayitlar icin).

    `updated_at` kasitli olarak yoktur: bu kayitlar guncellenmez, yalnizca eklenir.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
