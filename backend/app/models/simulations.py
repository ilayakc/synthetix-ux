import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.ai_pipeline import AIPipelineRun
    from app.models.interaction_heatmap import InteractionHeatmap
    from app.models.personas import Persona


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
        Index("ix_simulation_runs_page_analysis_id", "page_analysis_id"),
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
    # Paket 4B: yeni URL launch'larinda sunucu tarafinda otomatik olusturulan,
    # bu run'a atanmis PageAnalysis capture'i (bkz. app.services.test_wizard
    # .launch_draft). Eski (Paket 4B oncesi) run'larda NULL kalir - bu durumda
    # motor legacy `app.engine.fixtures` yerlesigini kullanmaya devam eder (bkz.
    # app.services.simulation_worker._load_dom_input). `design_assets.id` ile
    # ayni gerekce: `ondelete="SET NULL"` (asla CASCADE) - boylece organizasyon
    # hard-delete'i, page_analyses/simulation_runs kendi organization_id CASCADE'
    # lerinden hangisinin once islendigine bakilmaksizin FK ihlali uretmez; ve
    # PageAnalysis ekran goruntusu purge edildiginde (yalnizca screenshot_data/
    # screenshot_expires_at null'lanir, satir silinmez) bu FK hicbir zaman
    # etkilenmez.
    page_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("page_analyses.id", ondelete="SET NULL"), nullable=True
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
    # `chip_reservation_id` (baseline/modul Chip'i) ile AYNI desende, ama AYRI
    # bir referans: `ai_report` seciliyse launch grubu basina rezerve edilen
    # TEK AI Chip rezervasyonu (bkz. app.services.test_wizard.launch_draft,
    # app.services.pricing.AI_REPORT_CHIP_COST). A/B'nin iki run'i AYNI id'yi
    # tasir; AI secilmemis run'larda NULL kalir (eski run'lar icin backfill
    # YOKTUR). Baseline `chip_reservation_id` semantigi DEGISMEZ. `chip_
    # reservation_id` gibi bu alan da bilerek plain (FK'siz, indekssiz) bir
    # UUID'dir - rezervasyonun kanonik durumu `chip_reservations` tablosunda
    # tutulur, bu yalnizca zayif bir referanstir.
    ai_chip_reservation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # `ai_chip_reservation_id` ile AYNI desende ama TAMAMEN AYRI bir referans:
    # `ai_interaction_heatmap` seciliyse launch grubu basina rezerve edilen TEK
    # heatmap Chip rezervasyonu (bkz. app.services.pricing.
    # AI_INTERACTION_HEATMAP_CHIP_COST, app.services.test_wizard.launch_draft). AI
    # raporu rezervasyonundan (`ai_chip_reservation_id`) BAGIMSIZDIR - iki modul
    # birlikte secilirse iki AYRI rezervasyon/yasam dongusu olusur. A/B'nin iki
    # run'i AYNI id'yi tasir; heatmap secilmemis run'larda NULL kalir (eski run'lar
    # icin backfill YOKTUR). `chip_reservation_id` gibi bilerek plain (FK'siz,
    # indekssiz) bir UUID'dir - kanonik durum `chip_reservations` tablosundadir.
    heatmap_chip_reservation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # `passive_deletes=True`: fiili silme, DB'deki `ondelete="CASCADE"` FK'si
    # (bkz. app.models.personas.Persona) tarafindan yapilir; ORM burada
    # ayrica tek tek DELETE yayinlamaz, yalnizca ayni transaction icinde
    # `run.personas` erisimini/iliskisini saglar.
    personas: Mapped[list["Persona"]] = relationship(
        back_populates="simulation_run", cascade="all, delete-orphan", passive_deletes=True
    )
    # Bir SimulationRun icin en fazla bir AI pipeline kaydi olabilir (bkz.
    # app.models.ai_pipeline.AIPipelineRun.simulation_run_id UNIQUE
    # constraint'i) - bu yuzden liste degil, tekil/opsiyonel iliski.
    ai_pipeline_run: Mapped["AIPipelineRun | None"] = relationship(
        back_populates="simulation_run", cascade="all, delete-orphan", passive_deletes=True
    )
    # Bir SimulationRun icin en fazla bir AI etkilesim isi haritasi kaydi olabilir
    # (bkz. app.models.interaction_heatmap.InteractionHeatmap.simulation_run_id
    # UNIQUE) - tekil/opsiyonel iliski.
    interaction_heatmap: Mapped["InteractionHeatmap | None"] = relationship(
        back_populates="simulation_run", cascade="all, delete-orphan", passive_deletes=True
    )


class CalibrationObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Gonullu, acik rizali GERCEK kullanilabilirlik testi sonucu (bkz.
    docs/methodology.md "Kalibrasyon plani", adim 1).

    Bu, motorun agirliklarini (`app.engine.rules_config`) OTOMATIK OLARAK
    degistirmez/kalibre ETMEZ - yalnizca ilgili `SimulationRun`'in sentetik
    tahminiyle ileride (bagimsiz bir surecte, manuel gozden gecirmeyle)
    karsilastirilabilecek gercek olcum sonucunu SAKLAR. Bir run icin birden
    fazla gozlem eklenebilir (ornegin farkli kohortlardan/zamanlardan gelen
    testler); bu nedenle `simulation_run_id` UZERINDE bir benzersizlik
    kisiti YOKTUR - gecmis gozlemler asla ustune yazilmaz/silinmez.

    `simulation_run_id` FK'si `ondelete="CASCADE"`dir: bir SimulationRun
    (yalnizca organizasyon hard-delete'i ile, bkz. app.models.simulations.
    SimulationRun) silinirse ona bagli gozlemlerin de silinmesi dogaldir -
    bagimsiz bir "yetim" gozlem hicbir anlam tasimaz (karsilastirilacak
    sentetik tahmin de yok olmustur).
    """

    __tablename__ = "calibration_observations"
    __table_args__ = (
        Index("ix_calibration_observations_organization_id", "organization_id"),
        Index("ix_calibration_observations_simulation_run_id", "simulation_run_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Acik riza onayi (bkz. app.services.calibration.record_observation) -
    # `authorization_confirmed` (app.models.page_analysis) ile ayni desen:
    # servis katmani `False` durumunda kaydi hic OLUSTURMAZ, bu alan yalnizca
    # onaylanmis (`True`) durumun denetim izi (audit trail) icin saklanir.
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Dorttu, `docs/methodology.md` "Metrikler ve formuller" bolumundeki
    # sentetik karsiliklarina (gorev tamamlama/sure/yanlis tiklama/terk)
    # birebir denk dusecek sekilde tasarlanmistir; her biri bagimsiz olarak
    # opsiyoneldir (gercek testte hepsi olculmemis olabilir) - en az birinin
    # dolu olmasi servis katmaninda zorunlu kilinir.
    real_task_completion_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_median_task_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_misclick_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    real_abandonment_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_note: Mapped[str | None] = mapped_column(Text, nullable=True)
