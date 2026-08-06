"""Bir `SimulationRun`e bagli, asamali (staged) AI pipeline'inin durum takibi.

Bu modul YALNIZCA muhasebe/durum/izlenebilirlik verisini tasir - hic bir
zaman ham prompt metni, ham provider cevabi, HTML/DOM veya PII SAKLAMAZ
(bkz. `AIPipelineStage` dokstring'i). Gercek orkestrasyon (provider cagrisi,
prompt sablonlari, worker) BU ASAMANIN (Faz 1) kapsami DISINDADIR - burada
yalnizca veri modeli tanimlanir.

Iki katmanli tasarim:
- `AIPipelineRun`: bir `SimulationRun` icin TEK (unique) pipeline kaydi -
  genel durum, toplam token/maliyet, iptal bayragi.
- `AIPipelineStage`: pipeline icindeki HER asamanin (ve persona batch'inin)
  bagimsiz durumu - `stage_type` + `batch_index` ayrimiyla (ornegin
  "persona_behavior_batch_4" gibi dinamik bir string yerine
  `stage_type=PERSONA_BEHAVIOR, batch_index=4`).
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.simulations import SimulationRun


class AIPipelineStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class AIPipelineStageType(str, enum.Enum):
    EVIDENCE_PREPARATION = "evidence_preparation"
    PERSONA_BATCH_PREPARATION = "persona_batch_preparation"
    SCENARIO_INTERPRETATION = "scenario_interpretation"
    PERSONA_BEHAVIOR = "persona_behavior"
    AGGREGATION = "aggregation"
    UX_REPORT = "ux_report"


class AIPipelineStageStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class AIPipelineRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir `SimulationRun`e bagli, tekil (unique) AI pipeline calistirmasi.

    `simulation_run_id` UNIQUE'dir: bir SimulationRun icin en fazla bir
    pipeline kaydi olabilir (yeniden calistirma - ileride eklenecek - ayni
    satirin durumunu sifirlamalidir, `SimulationRun.retry_run`in ayni satiri
    yeniden kuyruga almasi gibi - bkz. app.services.simulation_worker).

    `organization_id`, `SimulationRun` uzerinden join yerine DENORMALIZE
    edilmis olarak burada da tutulur (bkz. `Persona`nin AKSINE) - cunku bu
    tablo, kendi basina sik sik tenant-scoped sorgulanacak/yetkilendirilecek
    (bkz. gelecekteki API endpoint'leri), `Persona`nin aksine.

    `ondelete="CASCADE"` (hem organization_id hem simulation_run_id icin):
    repodaki TUM tenant-scoped is/kayit tablolarinin (SimulationRun,
    CalibrationObservation, Entitlement, ChipReservation, DesignGenerationJob)
    zaten kullandigi mevcut, tutarli konvansiyondur - burada varsayimla
    secilmedi.
    """

    __tablename__ = "ai_pipeline_runs"
    __table_args__ = (
        UniqueConstraint("simulation_run_id", name="uq_ai_pipeline_runs_simulation_run_id"),
        Index("ix_ai_pipeline_runs_organization_id", "organization_id"),
        Index("ix_ai_pipeline_runs_status", "status"),
        CheckConstraint(
            "total_input_tokens >= 0", name="ck_ai_pipeline_runs_total_input_tokens_non_negative"
        ),
        CheckConstraint(
            "total_output_tokens >= 0", name="ck_ai_pipeline_runs_total_output_tokens_non_negative"
        ),
        CheckConstraint(
            "manual_retry_count >= 0", name="ck_ai_pipeline_runs_manual_retry_count_non_negative"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AIPipelineStatus] = mapped_column(
        SqlEnum(AIPipelineStatus, name="ai_pipeline_status"),
        nullable=False,
        default=AIPipelineStatus.QUEUED,
    )
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # MANUEL retry jenerasyonu (Faz 3C.3). 0 = ilk ucretli AI calismasi; her
    # BASARILI manuel retry transaction'i (bkz. app.services.ai_pipeline.
    # mutations.retry_ai_pipeline_group) bu degeri +1 yapar ve AYNI launch
    # grubundaki TUM pipeline'larda AYNI degeri tutar (grup basina tek 50 Chip
    # ucret jenerasyonu). Otomatik worker retry'lari (bkz. worker.persist_stage_
    # retry / reap_stale_ai_stages) bu alani ASLA artirmaz - onlar mevcut
    # rezervasyonu/jenerasyonu kullanan UCRETSIZ tekrar denemelerdir. `chip_
    # ledger` idempotency key'i `ai-report-retry:{launch_group_id}:{generation}`
    # bu deger uzerinden turetilir; boylece es-zamanli/duplice retry ayni
    # rezervasyonu paylasir (cift ucret olmaz). Negatif olamaz (check constraint).
    manual_retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Pipeline genelindeki toplam token/maliyet (stage satirlarindan
    # turetilebilir olsa da, sik okunan bir ozet olarak burada da tutulur -
    # bkz. `ChipLedgerEntry` ile `ChipReservation` arasindaki "defter satiri
    # / guncel durum ozeti" ayrimiyla ayni desen).
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Ic muhasebe icin TAHMINI maliyet (kullaniciya yansitilan Chip fiyati
    # DEGILDIR, bkz. Asama 5 mimari raporu "Maliyet ve Chip politikasi") -
    # repoda parasal/ondalikli bir tip konvansiyonu olmadigi icin (Chip
    # miktarlari her zaman tam sayidir) `CalibrationObservation`in tahmini
    # oran alanlariyla (`real_task_completion_rate` vb.) ayni sekilde
    # `Float` kullanilir.
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    simulation_run: Mapped["SimulationRun"] = relationship(back_populates="ai_pipeline_run")
    stages: Mapped[list["AIPipelineStage"]] = relationship(
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AIPipelineStage.created_at",
    )


class AIPipelineStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir `AIPipelineRun` icindeki TEK bir asamanin (veya persona batch'inin) durumu.

    ONEMLI - bu tablo asla su alanlari TASIMAZ (bkz. Asama 5 mimari raporu
    "Guvenlik" ve kullanicinin acik urun karari): ham prompt metni, ham
    provider cevabi, HTML/DOM, form verisi, PII, API anahtari/Authorization
    header'i veya exception stack trace'i. Yalnizca DOGRULANMIS (Pydantic
    ile validate edilmis, henuz bu asamada tanimlanmayan) JSON ciktisi
    (`validated_output`), hash'ler, provider/model metadata'si, token
    sayilari ve sinirli/guvenli bir `error_code` saklanir.

    `batch_index`, yalnizca `stage_type=PERSONA_BEHAVIOR` gibi batch'lenen
    asamalarda dolu olur (0..6 arasi, en fazla 7 batch - 100 persona / 15
    batch buyuklugu); batch'lenmeyen asamalarda (ornegin
    EVIDENCE_PREPARATION, AGGREGATION) NULL'dur. Dinamik bir
    `"persona_behavior_batch_4"` stringi asla stage_type'a GOMULMEZ.

    `idempotency_key` UNIQUE'dir - ileride (Faz 2+) su alanlarin hash'inden
    turetilecek: simulation_run_id + stage_type + batch_index +
    prompt_version + prompt_hash + provider + model_name + input_hash.
    Bilerek `(ai_pipeline_run_id, stage_type, batch_index)` uzerinde AYRICA
    bir UNIQUE constraint YOKTUR - bu, ayni asamanin yeni bir prompt/model
    surumuyle (farkli idempotency_key ile) yeniden calistirilip YENI bir
    satir olarak (eskisi silinmeden/uzerine yazilmadan) saklanmasina izin
    verir (bkz. kullanicinin acik karari, madde 3).
    """

    __tablename__ = "ai_pipeline_stages"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ai_pipeline_stages_idempotency_key"),
        # `execution_idempotency_key` (Faz 3B.2C) - GERCEK stage execution
        # kimligi (provider/model'e bagli); logical `idempotency_key`den AYRIDIR
        # (bkz. asagidaki kolon dokstring'i). UNIQUE (nullable): iki FARKLI stage
        # satiri asla ayni execution identity'yi paylasmamalidir - deger zaten
        # deterministik bir hash oldugundan (IdempotencyKeyInput: run/stage/
        # batch/prompt/provider/model/input) iki ayri satirin ayni execution
        # key'i uretmesi DAG butunlugu acisindan anlamsizdir. Retry AYNI satiri
        # (ayni provider/model/input ile) gunceller, execution key'i DEGISTIRMEZ
        # - dolayisiyla bir satir kendi key'ini hic degistirmez, cakisma olmaz.
        # Postgres birden fazla NULL'a UNIQUE altinda izin verir (henuz
        # pinlenmemis/saf-basarisiz satirlar NULL kalir).
        UniqueConstraint("execution_idempotency_key", name="uq_ai_pipeline_stages_execution_idempotency_key"),
        Index("ix_ai_pipeline_stages_ai_pipeline_run_id", "ai_pipeline_run_id"),
        Index("ix_ai_pipeline_stages_status", "status"),
        Index("ix_ai_pipeline_stages_run_stage_type", "ai_pipeline_run_id", "stage_type"),
        CheckConstraint("attempt_count >= 0", name="ck_ai_pipeline_stages_attempt_count_non_negative"),
        CheckConstraint("input_tokens >= 0", name="ck_ai_pipeline_stages_input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="ck_ai_pipeline_stages_output_tokens_non_negative"),
        CheckConstraint(
            "batch_index IS NULL OR batch_index >= 0",
            name="ck_ai_pipeline_stages_batch_index_non_negative",
        ),
    )

    ai_pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_pipeline_runs.id", ondelete="CASCADE"), nullable=False
    )
    stage_type: Mapped[AIPipelineStageType] = mapped_column(
        SqlEnum(AIPipelineStageType, name="ai_pipeline_stage_type"), nullable=False
    )
    # Yalnizca batch'lenen asamalarda dolu (bkz. sinif dokstring'i).
    batch_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[AIPipelineStageStatus] = mapped_column(
        SqlEnum(AIPipelineStageStatus, name="ai_pipeline_stage_status"),
        nullable=False,
        default=AIPipelineStageStatus.QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # SHA-256 hex digest'ler (64 karakter) - bkz. app.services.personas
    # `sample_hash`/`_hash_snapshot` ile ayni hashleme konvansiyonu.
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # MANTIKSAL (logical) stage key'i - provider'dan BAGIMSIZ, deterministik.
    # Amac: ayni DAG node'unun iki kere olusturulmamasi, successor dedup,
    # Stage 4 batch_index tekilligi, fan-in yarisinda tek Aggregation. Retry/
    # stale-requeue sirasinda DEGISMEZ (bkz. Faz 3B.2B/3B.2C).
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    # GERCEK stage EXECUTION key'i (Faz 3B.2C) - `idempotency_key`den (logical)
    # KASITLI olarak AYRIDIR. Provider asamalarinda (3/4/6) su girdilere baglidir:
    # simulation_run_id + stage_type + batch_index + input_hash + prompt_version/
    # prompt_hash + provider + model_name (yani `StageRunResult.audit.
    # idempotency_key` buraya yazilir). Saf asamalarda (1/2/5) logical key ile
    # AYNI deterministik degerdir. Ilk provider execution'indan HEMEN ONCE
    # pinlenir (provider/model ile birlikte); retry AYNI degeri korur; NULL =
    # henuz pinlenmemis (mevcut/eski satirlarda ve saf-basarisiz stage'lerde).
    execution_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Provider'in SEMANTIK yapilandirma kimligi (Faz 3D.1) - model + reasoning
    # effort + structured-output modu/surumu + semantik uretim ayarlarinin
    # SHA-256 hex digest'i (bkz. app.services.ai_pipeline.provider.
    # compute_configuration_fingerprint). API key/timeout/secret/pid/timestamp
    # ASLA icermez. Yalnizca provider (LLM) asamalarinda (3/4/6) ilk execution
    # pinlemesinde doldurulur (bkz. worker._pin_provider_execution); saf
    # asamalarda (1/2/5) NULL kalir. Retry sirasinda DEGISMEZ - farkli bir
    # deger PINLI stage'e yazilmaya calisilirsa provider DEGISTIREN bir retry
    # sayilir ve provider cagrilmadan terminal FAILED yapilir (bkz.
    # ProviderConfigurationMismatchError).
    provider_configuration_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Yalnizca Pydantic ile DOGRULANMIS yapilandirilmis cikti (Faz 2+'da
    # tanimlanacak stage sema'lari) - ham/dogrulanmamis provider cevabi
    # ASLA buraya yazilmaz.
    validated_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Sinirli, guvenli, kullaniciya gosterilebilir bir hata KODU (ornegin
    # "provider_timeout", "output_validation_failed") - exception mesaji/
    # stack trace/HTTP govdesi DEGIL.
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pipeline_run: Mapped["AIPipelineRun"] = relationship(back_populates="stages")
