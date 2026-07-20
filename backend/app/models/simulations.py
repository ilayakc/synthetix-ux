import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SimulationStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CalibrationStatus(str, enum.Enum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATING = "calibrating"
    CALIBRATED = "calibrated"


class SimulationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir test varyanti icin calistirilan tekil simulasyon."""

    __tablename__ = "simulation_runs"
    __table_args__ = (
        Index("ix_simulation_runs_organization_id", "organization_id"),
        Index("ix_simulation_runs_test_variant_id", "test_variant_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    test_variant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_variants.id", ondelete="CASCADE"), nullable=False
    )
    persona_preset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("persona_presets.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[SimulationStatus] = mapped_column(
        SqlEnum(SimulationStatus, name="simulation_status"),
        nullable=False,
        default=SimulationStatus.QUEUED,
    )
    deterministic_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    calibration_status: Mapped[CalibrationStatus] = mapped_column(
        SqlEnum(CalibrationStatus, name="calibration_status"),
        nullable=False,
        default=CalibrationStatus.UNCALIBRATED,
    )
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Durum makinesi ve ilerleme (bkz. app.services.simulation_worker) ---
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Motor surumleri ve sonuc (bkz. app.engine.baseline) ---
    rules_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fixture_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # --- Prompt 3 rezervasyonu ile bagi (bkz. app.services.test_wizard.launch_draft) ---
    # `launch_run_id`, ayni baslatmadan (launch) dogan tum calistirmalarin
    # (ornegin A/B'nin iki varyanti) paylastigi tek bir entitlement/Chip
    # rezervasyonunu isaret eder; tuketim/serbest birakma bu anahtar
    # uzerinden idempotent olarak yapilir.
    launch_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    free_entitlement_feature_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chip_reservation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
