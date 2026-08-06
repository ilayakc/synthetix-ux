"""AI pipeline'in TUM asamalari icin sikilastirilmis Pydantic sozlesmeleri.

Bu semalar domain-internal modellerdir - henuz bir API response modeli
DEGILDIR (bkz. gorev talimati madde 5). Hepsi `extra="forbid"` tasir: ham
HTML/DOM/prompt/provider cevabi, API anahtari, cookie, authorization,
e-posta/form icerigi gibi alanlar bu semalarin HICBIRINDE YOKTUR ve
sonradan da (extra="forbid" sayesinde) sessizce eklenemez.
"""

from __future__ import annotations

import enum
import uuid
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.services import personas as personas_service

# Evidence/step/hypothesis kimlikleri icin guvenli karakter kumesi -
# yalnizca kucuk harf/rakam/alt cizgi/iki nokta ust uste/nokta/tire (bkz.
# gorev talimati madde 6 "Guvenli karakter kumesinde").
_SAFE_ID_PATTERN = r"^[a-z0-9_:.\-]+$"
SafeId = Annotated[str, StringConstraints(min_length=1, max_length=100, pattern=_SAFE_ID_PATTERN)]


# --- Stage 1: Page evidence -------------------------------------------------------

PAGE_EVIDENCE_VERSION = "page-evidence-v1"


class PageEvidenceItem(BaseModel):
    """Tek bir kararli/atif-verilebilir kanit parcasi (bkz. `evidence_id` semasi:
    `metric:...`, `page:...`, `module:...`)."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: SafeId
    category: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=200)
    value: float | int | bool | str

    @field_validator("value")
    @classmethod
    def _bounded_string_value(cls, value: float | int | bool | str) -> float | int | bool | str:
        if isinstance(value, str) and len(value) > 200:
            raise ValueError("evidence value metni en fazla 200 karakter olabilir")
        return value


class PageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_version: str = PAGE_EVIDENCE_VERSION
    source_type: str = Field(min_length=1, max_length=50)
    items: tuple[PageEvidenceItem, ...] = Field(min_length=1, max_length=50)
    evidence_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def _unique_evidence_ids(self) -> PageEvidence:
        ids = [item.evidence_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence_id degerleri benzersiz olmalidir")
        return self

    def evidence_ids(self) -> frozenset[str]:
        return frozenset(item.evidence_id for item in self.items)


# --- Stage 2: Persona context + batch ------------------------------------------

# `app.services.personas.PERSONA_DIMENSIONS`/`_deep_scan_for_banned_keys`
# YENIDEN KULLANILIR - burada ikinci bir boyut/yasak-anahtar listesi
# ICAT EDILMEZ (bkz. gorev talimati madde 7).

MIN_PERSONA_BATCH_SIZE = 1
MAX_PERSONA_BATCH_SIZE = 20
DEFAULT_PERSONA_BATCH_SIZE = 15
MAX_PERSONAS_PER_PIPELINE = 100
MAX_PERSONA_BATCHES = 7


class PersonaContext(BaseModel):
    """Kalici `Persona` satirindan turetilmis, DB'den BAGIMSIZ saf DTO."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    persona_id: uuid.UUID
    index: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=500)
    attributes: dict[str, str] = Field(default_factory=dict)
    population_weight: int = Field(gt=0)

    @field_validator("attributes")
    @classmethod
    def _validate_attributes(cls, value: dict[str, str]) -> dict[str, str]:
        unknown = set(value) - set(personas_service.PERSONA_DIMENSIONS)
        if unknown:
            raise ValueError(f"Bilinmeyen persona boyutu: {sorted(unknown)}")
        for key, item in value.items():
            if not isinstance(item, str):
                raise ValueError(f"'{key}' degeri metin olmalidir")
        personas_service._deep_scan_for_banned_keys(value)
        return dict(value)


class PersonaBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_index: int = Field(ge=0)
    personas: tuple[PersonaContext, ...] = Field(
        min_length=MIN_PERSONA_BATCH_SIZE, max_length=MAX_PERSONA_BATCH_SIZE
    )
    population_weight: int = Field(gt=0)
    batch_hash: str = Field(min_length=64, max_length=64)


# --- Stage 3: Scenario interpretation (sema - uretim yok) -----------------------


class TaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: SafeId
    instruction: str = Field(min_length=1, max_length=500)
    success_criterion: str = Field(min_length=1, max_length=300)
    evidence_references: tuple[SafeId, ...] = Field(min_length=1, max_length=10)


class FrictionHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: SafeId
    description: str = Field(min_length=1, max_length=500)
    evidence_references: tuple[SafeId, ...] = Field(min_length=1, max_length=10)


SCENARIO_INTERPRETATION_VERSION = "scenario-v1"


class ScenarioInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_version: str = SCENARIO_INTERPRETATION_VERSION
    steps: tuple[TaskStep, ...] = Field(min_length=1, max_length=15)
    success_criteria: tuple[str, ...] = Field(min_length=1, max_length=8)
    friction_hypotheses: tuple[FrictionHypothesis, ...] = Field(default_factory=tuple, max_length=10)
    limitations: str = Field(min_length=1, max_length=1000)

    @field_validator("success_criteria")
    @classmethod
    def _bounded_success_criteria(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item or not item.strip() or len(item) > 300:
                raise ValueError("success_criteria ogeleri 1-300 karakter olmalidir")
        return value

    @model_validator(mode="after")
    def _unique_ids(self) -> ScenarioInterpretation:
        step_ids = [s.step_id for s in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step_id degerleri benzersiz olmalidir")
        hypothesis_ids = [h.hypothesis_id for h in self.friction_hypotheses]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("hypothesis_id degerleri benzersiz olmalidir")
        return self

    def hypothesis_ids(self) -> frozenset[str]:
        return frozenset(h.hypothesis_id for h in self.friction_hypotheses)


# --- Stage 4: Persona behavior estimation (sema - uretim yok) -------------------


class LikelyCompletion(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PERSONA_BEHAVIOR_VERSION = "persona-behavior-v1"


class PersonaBehaviorEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona_index: int = Field(ge=0)
    likely_completion: LikelyCompletion
    affected_hypothesis_ids: tuple[SafeId, ...] = Field(default_factory=tuple, max_length=10)
    accessibility_concerns: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_references: tuple[SafeId, ...] = Field(min_length=1, max_length=10)

    @field_validator("accessibility_concerns")
    @classmethod
    def _bounded_accessibility_concerns(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item or not item.strip() or len(item) > 300:
                raise ValueError("accessibility_concerns ogeleri 1-300 karakter olmalidir")
        return value


class PersonaBehaviorBatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_index: int = Field(ge=0)
    persona_results: tuple[PersonaBehaviorEstimate, ...] = Field(
        min_length=1, max_length=MAX_PERSONA_BATCH_SIZE
    )
    limitations: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _unique_persona_indices(self) -> PersonaBehaviorBatchOutput:
        indices = [r.persona_index for r in self.persona_results]
        if len(indices) != len(set(indices)):
            raise ValueError("persona_index degerleri benzersiz olmalidir")
        return self


# --- Stage 5: Deterministic aggregation -----------------------------------------

AGGREGATION_VERSION = "aggregation-v1"

AGGREGATION_DISCLAIMER = (
    "Bu agirlikli dagilim, sentetik senaryo tahminlerinin (LLM tarafindan "
    "uretilen persona davranis tahminlerinin) deterministik bir ozetidir; "
    "gercek kullanici olcumu degildir ve kalibre edilmemistir."
)


class CompletionBucketStat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: int = Field(ge=0)
    share: float = Field(ge=0.0, le=1.0)


class CompletionDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high: CompletionBucketStat
    medium: CompletionBucketStat
    low: CompletionBucketStat


class WeightedIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: SafeId
    description: str = Field(min_length=1, max_length=500)
    affected_users: int = Field(ge=0)
    affected_share: float = Field(ge=0.0, le=1.0)
    affected_persona_indices: tuple[int, ...] = Field(default_factory=tuple)
    weighted_confidence: float = Field(ge=0.0, le=1.0)


class AggregationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aggregation_version: str = AGGREGATION_VERSION
    total_population: int = Field(gt=0)
    completion_distribution: CompletionDistribution
    common_issues: tuple[WeightedIssue, ...] = Field(default_factory=tuple, max_length=20)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    disclaimer: str = Field(min_length=1, max_length=500)
    aggregation_hash: str = Field(min_length=64, max_length=64)


# --- Stage 6: UX report (sema - uretim yok) --------------------------------------

UX_REPORT_VERSION = "ux-report-v1"

UX_REPORT_DISCLAIMER = (
    "Bu rapor, kalibre edilmemiş sentetik senaryo tahminlerine dayanır; "
    "gerçek kullanıcı ölçümü değildir ve nihai bir karar dayanağı olarak "
    "tek başına kullanılmamalıdır."
)


class FindingPriority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UXFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: SafeId
    priority: FindingPriority
    finding: str = Field(min_length=1, max_length=500)
    source_stage: str = Field(min_length=1, max_length=50)
    evidence_references: tuple[SafeId, ...] = Field(min_length=1, max_length=10)
    affected_persona_groups: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    estimated_affected_users: int = Field(ge=0)
    recommendation: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("affected_persona_groups")
    @classmethod
    def _bounded_persona_groups(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not item or not item.strip() or len(item) > 200:
                raise ValueError("affected_persona_groups ogeleri 1-200 karakter olmalidir")
        return value


class UXReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_version: str = UX_REPORT_VERSION
    summary: str = Field(min_length=1, max_length=1000)
    findings: tuple[UXFinding, ...] = Field(default_factory=tuple, max_length=20)
    limitations: str = Field(min_length=1, max_length=1000)
    disclaimer: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _unique_finding_ids(self) -> UXReport:
        ids = [f.finding_id for f in self.findings]
        if len(ids) != len(set(ids)):
            raise ValueError("finding_id degerleri benzersiz olmalidir")
        return self


__all__ = [
    "SafeId",
    "PAGE_EVIDENCE_VERSION",
    "PageEvidenceItem",
    "PageEvidence",
    "MIN_PERSONA_BATCH_SIZE",
    "MAX_PERSONA_BATCH_SIZE",
    "DEFAULT_PERSONA_BATCH_SIZE",
    "MAX_PERSONAS_PER_PIPELINE",
    "MAX_PERSONA_BATCHES",
    "PersonaContext",
    "PersonaBatch",
    "SCENARIO_INTERPRETATION_VERSION",
    "TaskStep",
    "FrictionHypothesis",
    "ScenarioInterpretation",
    "PERSONA_BEHAVIOR_VERSION",
    "LikelyCompletion",
    "PersonaBehaviorEstimate",
    "PersonaBehaviorBatchOutput",
    "AGGREGATION_VERSION",
    "AGGREGATION_DISCLAIMER",
    "CompletionBucketStat",
    "CompletionDistribution",
    "WeightedIssue",
    "AggregationResult",
    "UX_REPORT_VERSION",
    "UX_REPORT_DISCLAIMER",
    "FindingPriority",
    "UXFinding",
    "UXReport",
]
