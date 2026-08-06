"""Provider'a gonderilecek STRICT girdi modelleri (Faz 2B).

Bu modeller, Faz 2A'nin cikti semalarini (schemas.py) hic degistirmeden,
her LLM asamasi (Stage 3/4/6) icin provider'a gecirilecek girdiyi
sikilastirir. Hicbir yerde ham `run.result`, ham `input_snapshot`, ham
HTML/DOM, tam `Persona` ORM nesnesi (yalnizca `PersonaContext` -> `PersonaBatch`
uzerinden) veya herhangi bir SQLAlchemy nesnesi KABUL EDILMEZ.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.ai_pipeline.schemas import (
    AggregationResult,
    PageEvidence,
    PersonaBatch,
    SafeId,
    ScenarioInterpretation,
)


class BaselineMetricSnapshot(BaseModel):
    """Rapordan alinmis, zaten hesaplanmis tek bir baseline metrik degeri -
    yalnizca sayisal/etiket alanlari, hicbir serbest metin/HTML yok."""

    model_config = ConfigDict(extra="forbid")

    metric_id: SafeId
    point_estimate: float
    low: float | None = None
    high: float | None = None


class ModuleSummary(BaseModel):
    """Yalnizca modulun mevcut/present oldugu bilgisi - ham modul sonucu YOK."""

    model_config = ConfigDict(extra="forbid")

    module_key: SafeId
    present: bool


class ScenarioInterpretationInput(BaseModel):
    """Stage 3 (senaryo yorumlama) provider girdisi."""

    model_config = ConfigDict(extra="forbid")

    evidence: PageEvidence
    target_task: str = Field(min_length=1, max_length=500)
    test_name: str = Field(min_length=1, max_length=200)
    test_description: str = Field(min_length=1, max_length=1000)
    methodology_context: str = Field(min_length=1, max_length=1000)


class PersonaBehaviorBatchInput(BaseModel):
    """Stage 4 (persona davranis tahmini) provider girdisi - tek bir batch."""

    model_config = ConfigDict(extra="forbid")

    batch: PersonaBatch
    evidence: PageEvidence
    scenario: ScenarioInterpretation
    baseline_metrics: tuple[BaselineMetricSnapshot, ...] = Field(default_factory=tuple, max_length=20)

    @model_validator(mode="after")
    def _unique_metric_ids(self) -> PersonaBehaviorBatchInput:
        ids = [m.metric_id for m in self.baseline_metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("baseline_metrics metric_id degerleri benzersiz olmalidir")
        return self


class UXReportInput(BaseModel):
    """Stage 6 (UX raporu) provider girdisi."""

    model_config = ConfigDict(extra="forbid")

    evidence: PageEvidence
    baseline_metrics: tuple[BaselineMetricSnapshot, ...] = Field(default_factory=tuple, max_length=20)
    aggregation: AggregationResult
    module_summary: tuple[ModuleSummary, ...] = Field(default_factory=tuple, max_length=20)
    methodology_context: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _unique_module_keys(self) -> UXReportInput:
        keys = [m.module_key for m in self.module_summary]
        if len(keys) != len(set(keys)):
            raise ValueError("module_summary module_key degerleri benzersiz olmalidir")
        return self

    @model_validator(mode="after")
    def _unique_metric_ids(self) -> UXReportInput:
        ids = [m.metric_id for m in self.baseline_metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("baseline_metrics metric_id degerleri benzersiz olmalidir")
        return self


__all__ = [
    "BaselineMetricSnapshot",
    "ModuleSummary",
    "ScenarioInterpretationInput",
    "PersonaBehaviorBatchInput",
    "UXReportInput",
]
