"""Bir SimulationRun'a bagli, kalici temsili persona projeksiyonu.

Bu tablo `SimulationRun.input_snapshot["persona_sample"]` (bkz.
app.services.personas.sample_cohorts) icin bir YEDEK/ikame DEGILDIR - o hala
motorun (app.engine.baseline) kaynak gercekligi (source of truth) olmaya
devam eder. Buradaki satirlar, ayni cohort sample'in en fazla
`app.services.personas.MAX_REPRESENTATIVE_PERSONAS` (100) kaba, kalici,
incelenebilir personaya indirgenmis bir PROJEKSIYONUDUR - gorunurluk,
inceleme ve ileride eklenecek AI pipeline'i icin (bkz.
app.services.personas.build_representative_personas).
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.simulations import SimulationRun


class Persona(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Bir `SimulationRun`'a ait, kararli sirali (index) temsili persona satiri.

    `updated_at` kasitli olarak yoktur (bkz. `CreatedAtMixin`): bir persona
    projeksiyonu, uretildigi run icin degismez (append-only) kabul edilir -
    guncellenmez, yalnizca run silindiginde CASCADE ile silinir.
    """

    __tablename__ = "personas"
    __table_args__ = (
        UniqueConstraint("simulation_run_id", "index", name="uq_personas_run_index"),
        Index("ix_personas_simulation_run_id", "simulation_run_id"),
        CheckConstraint("population_weight > 0", name="ck_personas_population_weight_positive"),
    )

    simulation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    # 0'dan baslayan, kanonik/kararli siraya gore atanan indeks (bkz.
    # app.services.personas.build_representative_personas) - ayni cohort
    # sample + deterministic_seed her zaman ayni index->persona eslesmesini
    # uretir.
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    # Yalnizca `app.services.personas.PERSONA_DIMENSIONS` anahtarlarini
    # tasiyan dimension_values sozlugu (ornegin {"age_range": "25_34",
    # "device_class": "mobile"}) - yeni demografik/davranissal alan icermez.
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Bu personanin temsil ettigi sanal kullanici sayisi. Bir run'a ait tum
    # personalarin population_weight toplami, o run'in persona_count
    # (agent_count) degerine tam esit olmalidir (bkz.
    # app.services.personas.build_representative_personas dogrulamasi).
    population_weight: Mapped[int] = mapped_column(Integer, nullable=False)

    simulation_run: Mapped["SimulationRun"] = relationship(back_populates="personas")
