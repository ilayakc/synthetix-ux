"""AI etkilesim isi haritasi (`ai_interaction_heatmap`) is/durum kaydi.

Bu tablo, `ai_report` pipeline'inin (bkz. app.models.ai_pipeline) AKSINE cok
asamali bir DAG DEGILDIR: bir `SimulationRun` icin TEK bir "AI tiklama tahmini"
isini (queued -> running -> succeeded | failed) ve DOGRULANMIS sonucunu tutar.

MIMARI (bkz. gorev talimati): sonuc, rapor GORUNTULENIRKEN lazy hesaplanmaz -
ayri bir worker (bkz. app.services.ai_interaction_heatmap.worker) gercek OpenAI
provider'ini cagirir, ciktiyi dogrular ve BURAYA yazar; GET /api/reports/{id}
YALNIZCA bu kaydi OKUR (provider cagrisi / Chip tuketimi / DB yazma YAPMAZ).

GUVENLIK: bu tablo asla ham prompt/ham provider cevabi/HTML/DOM/PII/API anahtari
SAKLAMAZ - yalnizca DOGRULANMIS `result` JSON'u (bkz. app.services.
ai_interaction_heatmap.schemas.InteractionHeatmapResult; koordinatlar YALNIZCA
dogrulanmis analyzer adaylarindan gelir, AI koordinat URETEMEZ), fingerprint,
provider/model metadata'si ve sinirli/guvenli bir `error_code` tutulur.

ESZAMANLILIK: `simulation_run_id` UNIQUE'tir - bir run icin en fazla bir heatmap
kaydi olusabilir; bu, worker'in advisory-lock + `FOR UPDATE` CLAIM deseniyle
birlikte (bkz. worker) es-zamanli iki worker'in AYNI isi iki kez CALISTIRMASINI
onler. `fingerprint`, ayni girdiler icin provider'in ikinci kez cagrilmamasini
saglayan dedup anahtaridir (bkz. worker._reuse_existing_result) - `content_
sha256`/`target_task`/`target_audience`/persona dagilimi/aday listesi/model/
prompt+sema surumune baglidir.
"""

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
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


class InteractionHeatmapStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class InteractionHeatmap(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Bir `SimulationRun`e bagli tekil AI etkilesim isi haritasi isi + sonucu."""

    __tablename__ = "interaction_heatmaps"
    __table_args__ = (
        UniqueConstraint("simulation_run_id", name="uq_interaction_heatmaps_simulation_run_id"),
        Index("ix_interaction_heatmaps_organization_id", "organization_id"),
        Index("ix_interaction_heatmaps_status", "status"),
        # Fingerprint UNIQUE DEGILDIR (bilerek): A/B'nin iki tarafi ayni URL/
        # icerik/gorevle ayni fingerprint'i uretebilir - hard-unique bir kisit
        # mesru bir A/B launch'ini IntegrityError ile kirardi. "Ayni fingerprint
        # icin provider ikinci kez cagrilmasin" garantisi in-process dedup ile
        # saglanir (bkz. worker._reuse_existing_result); bu index yalnizca o
        # aramayi hizlandirir.
        Index("ix_interaction_heatmaps_fingerprint", "fingerprint"),
        CheckConstraint(
            "attempt_count >= 0", name="ck_interaction_heatmaps_attempt_count_non_negative"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[InteractionHeatmapStatus] = mapped_column(
        SqlEnum(InteractionHeatmapStatus, name="interaction_heatmap_status"),
        nullable=False,
        default=InteractionHeatmapStatus.QUEUED,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # Deterministik dedup/yeniden-uretim anahtari (bkz. app.services.
    # ai_interaction_heatmap.service.compute_interaction_heatmap_fingerprint).
    # Ilk claim'de (RUNNING) yazilir; provider basarisiz olsa da kalir.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Sinirli, guvenli, kullaniciya gosterilebilir bir hata KODU ("provider_
    # timeout", "output_validation_failed", "no_candidates" vb.) - exception
    # mesaji / stack trace / HTTP govdesi ASLA yazilmaz (bkz. worker error_code
    # sabitleri).
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # YALNIZCA dogrulanmis `InteractionHeatmapResult` JSON'u (koordinatlar
    # analyzer adaylarindan; AI koordinat uretemez). Ham/dogrulanmamis provider
    # cevabi ASLA buraya yazilmaz.
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    simulation_run: Mapped["SimulationRun"] = relationship(back_populates="interaction_heatmap")


__all__ = ["InteractionHeatmap", "InteractionHeatmapStatus"]
