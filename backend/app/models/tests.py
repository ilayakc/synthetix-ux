import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TestDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir proje icinde tanimlanan UX testi (senaryo)."""

    __tablename__ = "test_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_test_definitions_project_name"),
        Index("ix_test_definitions_organization_id", "organization_id"),
        Index("ix_test_definitions_project_id", "project_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)


class TestVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir test tanimina ait varyant (ornegin A/B varyanti)."""

    __tablename__ = "test_variants"
    __table_args__ = (
        UniqueConstraint("test_definition_id", "name", name="uq_test_variants_definition_name"),
        Index("ix_test_variants_organization_id", "organization_id"),
        Index("ix_test_variants_test_definition_id", "test_definition_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    test_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_definitions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class PersonaPresetStatus(str, enum.Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class PersonaPreset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Organizasyon duzeyinde yeniden kullanilabilir persona dagilim tanimi.

    `config`, `app.services.personas.validate_distribution` tarafindan
    dogrulanan bir dagilim sozlugudur (boyut anahtari -> agirlikli kova
    listesi; bkz. o modulun dokstring'i). Hazir (builtin) presetler bu
    tabloda satir olarak tutulmaz (bkz. `app.services.personas.
    BUILTIN_PRESETS`); bir sirket bunlardan birini "kopyalayarak" kendi
    ozel, duzenlenebilir bir satirini olusturabilir - `source_builtin_key`
    bu durumda kaynak preset'in anahtarini kaydeder (yalnizca izlenebilirlik
    icindir, davranisi etkilemez).
    """

    __tablename__ = "persona_presets"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_persona_presets_org_name"),
        Index("ix_persona_presets_organization_id", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[PersonaPresetStatus] = mapped_column(
        SqlEnum(PersonaPresetStatus, name="persona_preset_status"),
        nullable=False,
        default=PersonaPresetStatus.ACTIVE,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_builtin_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
