"""AI pipeline asama ciktilarinin DB-kalici/serilestirme katmani (Faz 3B.2A).

Bu modul SAFTIR (DB oturumu/`add()`/`commit()` YOK, ag cagrisi YOK) - yalnizca:

1. Her asamanin DOGRULANMIS Pydantic ciktisini (`PageEvidence`,
   `ScenarioInterpretation`, `PersonaBehaviorBatchOutput`, `AggregationResult`,
   `UXReport` + Stage 2 icin `PersonaBatchManifestSet`) `model_dump(mode="json")`
   ile JSON-uyumlu bir dict'e ceviren `encode_stage_output`.
2. DB'nin `JSON` kolonundan gelen ham bir dict'i, dogru tipli Pydantic
   modeline SIKI dogrulamayla geri ceviren `decode_stage_output` - bilinmeyen/
   bozuk payload, cikan `ValidationError` sizdirilmadan, kontrollu bir domain
   hatasina (`StageOutputDecodeError`) sarilarak reddedilir.
3. Bir persist edilmis satirin `AIPipelineStageType`'ini, cozulmeye calisilan
   sema ile eslesip eslesmedigini dogrulayan yardimcilar (ornegin bir
   PERSONA_BEHAVIOR satirinin JSON'i `UXReport` olarak cozulemez).
4. Stage 2 icin MINIMAL DB manifesti (`PersonaBatchManifestSet`) - persona
   `attributes` (demografik boyutlar) DB'ye YAZILMAZ, yalnizca minimal
   referanslar (id/index/population_weight) tutulur - ve bu manifesti taze
   `Persona` DB satirlariyla tam `PersonaBatch` tuple'ina geri kuran
   `rehydrate_persona_batches`.

Ham prompt/provider-cevabi hicbir encode/decode yuzeyinde YOKTUR (Faz 2A/2B
cikti semalari zaten bu alanlari tasimaz; testlerle dogrulanir).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.ai_pipeline import AIPipelineStageType
from app.models.personas import Persona
from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.schemas import (
    DEFAULT_PERSONA_BATCH_SIZE,
    MAX_PERSONA_BATCH_SIZE,
    MAX_PERSONA_BATCHES,
    MAX_PERSONAS_PER_PIPELINE,
    AggregationResult,
    PageEvidence,
    PersonaBatch,
    PersonaBehaviorBatchOutput,
    PersonaContext,
    ScenarioInterpretation,
    UXReport,
)
from app.services.ai_pipeline.stage_hashing import (
    compute_batch_hash,
    persona_batches_output_hash,
)

# --- Domain hatalari (Part 7) ----------------------------------------------------


class AIPipelinePersistenceError(Exception):
    """Persistence/codec/rehydration katmanindaki tum domain hatalarinin
    temel sinifi. `HTTPException`/API-katmani tipi DEGILDIR - ileride bir API
    katmani tarafindan cevrilecek saf Python istisnasidir.

    `code`, ileride guvenle disari verilebilecek kisa/sabit bir tanimlayicidir;
    `message` yalnizca gelistirici/log icindir (ham payload/PII TASIMAZ).
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class StageOutputDecodeError(AIPipelinePersistenceError):
    """Persist edilmis bir asama ciktisi (JSON) hedef semaya gore GECERSIZ/
    bozuk oldugunda firlatilir (Pydantic `ValidationError` sizdirilmaz)."""


class StageSchemaMismatchError(AIPipelinePersistenceError):
    """Bir asama satirinin `stage_type`'i, cozulmeye/kodlanmaya calisilan
    sema ile ESLESMEDIGINDE firlatilir (ornegin PERSONA_BEHAVIOR satiri
    UXReport olarak cozulemez)."""


class ManifestRehydrationError(AIPipelinePersistenceError):
    """Stage 2 manifestinin, taze `Persona` DB satirlariyla tutarli bir
    `PersonaBatch` tuple'ina geri kurulamamasi (eksik/fazla/yinelenen persona,
    degismis population_weight, batch_hash/output_hash uyusmazligi) durumunda
    firlatilir."""


ERROR_STAGE_OUTPUT_DECODE_FAILED = "stage_output_decode_failed"
ERROR_STAGE_SCHEMA_MISMATCH = "stage_output_schema_mismatch"
ERROR_MANIFEST_REHYDRATION_FAILED = "manifest_rehydration_failed"


# --- Stage 2 minimal DB manifesti (Part 2) ---------------------------------------

PERSONA_BATCH_MANIFEST_VERSION = "persona-batch-manifest-v1"


class PersonaManifestEntry(BaseModel):
    """Bir batch icindeki tek bir personaya MINIMAL referans.

    KASITLI olarak `attributes` VE `label` TASIMAZ (kullanicinin acik urun
    karari): demografik boyutlar DB'ye ikinci kez yazilmaz, rehydration
    sirasinda taze `Persona` satirindan okunur. Yalnizca `population_weight`
    manifeste yazilir - boylece rehydration, DB'deki agirligin Stage 2
    anindan bu yana DEGISMEDIGINI dogrulayabilir (sentetik populasyon
    matematigini sessizce gecersiz kilan bir degisikligi yakalar)."""

    model_config = ConfigDict(extra="forbid")

    persona_id: uuid.UUID
    index: int = Field(ge=0)
    population_weight: int = Field(gt=0)


class PersonaBatchManifest(BaseModel):
    """Tek bir persona batch'inin minimal, `attributes`-siz DB manifesti."""

    model_config = ConfigDict(extra="forbid")

    batch_index: int = Field(ge=0)
    batch_hash: str = Field(min_length=64, max_length=64)
    batch_size: int = Field(gt=0)
    # Bu batch'e ait personalarin population_weight toplami (per-batch).
    population_weight: int = Field(gt=0)
    personas: tuple[PersonaManifestEntry, ...] = Field(min_length=1, max_length=MAX_PERSONA_BATCH_SIZE)


class PersonaBatchManifestSet(BaseModel):
    """Stage 2'nin (PERSONA_BATCH_PREPARATION) DB'ye persist edilen TAM ciktisi.

    Hem per-batch manifestleri (`batches`) hem de tum Stage 2 ciktisina ait
    ozet alanlari (toplam persona sayisi/agirlik, batch sayisi, ve Stage 2
    `output_hash`i) tasir. `output_hash`, rehydration'in yeniden urettigi tam
    `PersonaBatch` tuple'inin hash'i ile karsilastirilmak icin saklanir (bkz.
    "kritik hash kurali", `rehydrate_persona_batches`).
    """

    model_config = ConfigDict(extra="forbid")

    manifest_version: str = PERSONA_BATCH_MANIFEST_VERSION
    # Tum batch'ler genelindeki toplam persona sayisi ve population_weight.
    total_persona_count: int = Field(gt=0, le=MAX_PERSONAS_PER_PIPELINE)
    total_population_weight: int = Field(gt=0)
    batch_count: int = Field(gt=0, le=MAX_PERSONA_BATCHES)
    # Stage 2'nin `stage_runner`/`executor` tarafindan kaydedilen output_hash'i
    # (tam runtime `PersonaBatch` tuple'i uzerinden - attributes DAHIL).
    output_hash: str = Field(min_length=64, max_length=64)
    batches: tuple[PersonaBatchManifest, ...] = Field(min_length=1, max_length=MAX_PERSONA_BATCHES)


def build_stage2_manifest(batches: Sequence[PersonaBatch]) -> PersonaBatchManifestSet:
    """Runtime `PersonaBatch` tuple'indan (attributes DAHIL) minimal DB
    manifestini (attributes HARIC) uretir - deterministiktir (ayni girdi ->
    ayni manifest). `output_hash`, tam runtime tuple uzerinden PAYLASILAN
    yardimciyla hesaplanir (Stage 2 `stage_runner` ile birebir ayni)."""

    if not batches:
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED, "manifest icin en az bir batch gereklidir"
        )

    ordered = sorted(batches, key=lambda b: b.batch_index)
    batch_manifests = tuple(
        PersonaBatchManifest(
            batch_index=b.batch_index,
            batch_hash=b.batch_hash,
            batch_size=len(b.personas),
            population_weight=b.population_weight,
            personas=tuple(
                PersonaManifestEntry(
                    persona_id=p.persona_id,
                    index=p.index,
                    population_weight=p.population_weight,
                )
                for p in b.personas
            ),
        )
        for b in ordered
    )
    total_persona_count = sum(len(b.personas) for b in ordered)
    total_population_weight = sum(b.population_weight for b in ordered)
    return PersonaBatchManifestSet(
        total_persona_count=total_persona_count,
        total_population_weight=total_population_weight,
        batch_count=len(ordered),
        output_hash=persona_batches_output_hash(tuple(ordered)),
        batches=batch_manifests,
    )


# --- Codec (Part 1) --------------------------------------------------------------

# Persist edilmis JSON'in HANGI Pydantic semasina cozulecegini stage_type'a
# gore sabitler. Stage 2 (PERSONA_BATCH_PREPARATION), runtime `PersonaBatch`
# yerine MINIMAL manifest (`PersonaBatchManifestSet`) olarak persist edilir.
_STAGE_OUTPUT_SCHEMA: dict[AIPipelineStageType, type[BaseModel]] = {
    AIPipelineStageType.EVIDENCE_PREPARATION: PageEvidence,
    AIPipelineStageType.PERSONA_BATCH_PREPARATION: PersonaBatchManifestSet,
    AIPipelineStageType.SCENARIO_INTERPRETATION: ScenarioInterpretation,
    AIPipelineStageType.PERSONA_BEHAVIOR: PersonaBehaviorBatchOutput,
    AIPipelineStageType.AGGREGATION: AggregationResult,
    AIPipelineStageType.UX_REPORT: UXReport,
}


def stage_output_schema(stage_type: AIPipelineStageType) -> type[BaseModel]:
    """Verilen `stage_type` icin beklenen persist-cikti semasini dondurur."""

    schema = _STAGE_OUTPUT_SCHEMA.get(stage_type)
    if schema is None:
        raise StageSchemaMismatchError(ERROR_STAGE_SCHEMA_MISMATCH, f"bilinmeyen stage_type: {stage_type!r}")
    return schema


def assert_stage_output_type(stage_type: AIPipelineStageType, model: BaseModel) -> None:
    """`model`'in tipinin, `stage_type` icin beklenen sema ile TAM eslestigini
    dogrular (alt sinif kabul edilmez) - aksi halde `StageSchemaMismatchError`."""

    expected = stage_output_schema(stage_type)
    if type(model) is not expected:
        raise StageSchemaMismatchError(
            ERROR_STAGE_SCHEMA_MISMATCH,
            f"{stage_type.value} asamasi {expected.__name__} bekler, " f"gelen: {type(model).__name__}",
        )


def encode_stage_output(stage_type: AIPipelineStageType, model: BaseModel) -> dict:
    """Dogrulanmis bir asama ciktisini JSON-uyumlu bir dict'e cevirir.

    `model`'in tipi `stage_type` ile eslesmezse `StageSchemaMismatchError`.
    Serilestirme el ile YAPILMAZ - yalnizca `model_dump(mode="json")`.
    """

    assert_stage_output_type(stage_type, model)
    return model.model_dump(mode="json")


def decode_stage_output(stage_type: AIPipelineStageType, payload: object) -> BaseModel:
    """Ham bir dict'i (`JSON` kolonundan geldigi gibi) `stage_type`'a karsilik
    gelen tipli Pydantic modeline SIKI dogrulamayla cozer.

    Bilinmeyen/eksik/fazla alan iceren payload, Pydantic `ValidationError`i
    sizdirilmadan `StageOutputDecodeError`e sarilarak reddedilir (semalar
    zaten `extra="forbid"` tasir).
    """

    schema = stage_output_schema(stage_type)
    if not isinstance(payload, dict):
        raise StageOutputDecodeError(
            ERROR_STAGE_OUTPUT_DECODE_FAILED,
            f"{stage_type.value} ciktisi bir JSON nesnesi olmalidir",
        )
    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        # Ham payload/PII sizdirmadan yalnizca hata SAYISINI paylas.
        raise StageOutputDecodeError(
            ERROR_STAGE_OUTPUT_DECODE_FAILED,
            f"{stage_type.value} ciktisi {schema.__name__} semasina gore gecersiz "
            f"({len(exc.errors())} dogrulama hatasi)",
        ) from exc


# Codec'in disari acilan, statik-tipli decode kolayliklari (cagiran tarafta
# tip daraltma icin) - hepsi ayni `decode_stage_output`u kullanir.
def decode_page_evidence(payload: object) -> PageEvidence:
    model = decode_stage_output(AIPipelineStageType.EVIDENCE_PREPARATION, payload)
    assert isinstance(model, PageEvidence)
    return model


def decode_persona_batch_manifest(payload: object) -> PersonaBatchManifestSet:
    model = decode_stage_output(AIPipelineStageType.PERSONA_BATCH_PREPARATION, payload)
    assert isinstance(model, PersonaBatchManifestSet)
    return model


def decode_scenario_interpretation(payload: object) -> ScenarioInterpretation:
    model = decode_stage_output(AIPipelineStageType.SCENARIO_INTERPRETATION, payload)
    assert isinstance(model, ScenarioInterpretation)
    return model


def decode_persona_behavior_batch(payload: object) -> PersonaBehaviorBatchOutput:
    model = decode_stage_output(AIPipelineStageType.PERSONA_BEHAVIOR, payload)
    assert isinstance(model, PersonaBehaviorBatchOutput)
    return model


def decode_aggregation_result(payload: object) -> AggregationResult:
    model = decode_stage_output(AIPipelineStageType.AGGREGATION, payload)
    assert isinstance(model, AggregationResult)
    return model


def decode_ux_report(payload: object) -> UXReport:
    model = decode_stage_output(AIPipelineStageType.UX_REPORT, payload)
    assert isinstance(model, UXReport)
    return model


# --- Rehydration (Part 2) --------------------------------------------------------


def persona_context_from_row(row: Persona) -> PersonaContext:
    """Kalici bir `Persona` satirindan, DB'den bagimsiz saf `PersonaContext`
    DTO'su uretir (`attributes` TAZE olarak satirdan okunur)."""

    return PersonaContext(
        persona_id=row.id,
        index=row.index,
        label=row.label,
        attributes=dict(row.attributes or {}),
        population_weight=row.population_weight,
    )


def rehydrate_persona_batches(
    manifest_set: PersonaBatchManifestSet,
    persona_rows: Sequence[Persona],
) -> tuple[PersonaBatch, ...]:
    """Stage 2 manifesti + taze `Persona` DB satirlarindan, pipeline-init
    aninda `build_persona_batches`in urettigi TAM `PersonaBatch` tuple'ini
    (attributes DAHIL) yeniden kurar.

    Dogrulamalar (hepsi kontrollu `ManifestRehydrationError` firlatir):
      - Manifestte referans verilen her persona DB'de HALA mevcut olmalidir.
      - DB'de, manifestte referans verilmeyen FAZLA persona OLMAMALIDIR
        (personalar run basina append-only/degismezdir; fazla satir, Stage 2
        sonrasi eklenmis demektir ve sentetik populasyonu degistirir).
      - Manifest icinde/arasinda YINELENEN persona referansi olmamalidir.
      - Manifestteki `population_weight`, DB'deki guncel deger ile eslesmelidir.
      - Manifestteki `index`, DB'deki guncel deger ile eslesmelidir.
      - Batch'ler kanonik `batch_index` ASC sirada olmalidir.
      - Yeniden-uretilen her batch'in `batch_hash`i, manifesttekiyle eslesmelidir.
      - Yeniden-uretilen tam tuple'in `output_hash`i, manifestteki
        (Stage 2'nin `stage_runner` tarafindan kaydedilen) `output_hash` ile
        AYNI olmalidir - bu, rehydration'in orijinal runtime ciktiyla birebir
        ayni oldugunu KANITLAR.
      - Yeniden hesaplanan toplam population_weight, manifest ozetiyle eslesmelidir.
    """

    # 1) Batch sirasi kanonik (ASC) mi?
    batch_indices = [b.batch_index for b in manifest_set.batches]
    if batch_indices != sorted(batch_indices):
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED, "batch'ler batch_index'e gore artan sirada olmalidir"
        )
    if len(batch_indices) != len(set(batch_indices)):
        raise ManifestRehydrationError(ERROR_MANIFEST_REHYDRATION_FAILED, "yinelenen batch_index bulundu")

    # 2) DB satirlarini id->row esle; yinelenen id'yi (savunma amacli) reddet.
    rows_by_id: dict[uuid.UUID, Persona] = {}
    for row in persona_rows:
        if row.id in rows_by_id:
            raise ManifestRehydrationError(
                ERROR_MANIFEST_REHYDRATION_FAILED, "DB'de yinelenen persona id bulundu"
            )
        rows_by_id[row.id] = row

    # 3) Manifest referanslarini topla; yinelenen referansi reddet.
    referenced_ids: set[uuid.UUID] = set()
    for batch in manifest_set.batches:
        for entry in batch.personas:
            if entry.persona_id in referenced_ids:
                raise ManifestRehydrationError(
                    ERROR_MANIFEST_REHYDRATION_FAILED,
                    "manifestte yinelenen persona referansi bulundu",
                )
            referenced_ids.add(entry.persona_id)

    # 4) Eksik / fazla persona kontrolu (iki yonlu set esitligi).
    db_ids = set(rows_by_id)
    missing = referenced_ids - db_ids
    if missing:
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED,
            f"manifestte referans verilen {len(missing)} persona DB'de bulunamadi",
        )
    extra = db_ids - referenced_ids
    if extra:
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED,
            f"DB'de manifestte referans verilmeyen {len(extra)} fazla persona var",
        )

    # 5) Her manifest referansinin population_weight/index'i, DB ile eslesmeli.
    for batch in manifest_set.batches:
        for entry in batch.personas:
            row = rows_by_id[entry.persona_id]
            if row.population_weight != entry.population_weight:
                raise ManifestRehydrationError(
                    ERROR_MANIFEST_REHYDRATION_FAILED,
                    "persona population_weight'i Stage 2 anindan bu yana degismis",
                )
            if row.index != entry.index:
                raise ManifestRehydrationError(
                    ERROR_MANIFEST_REHYDRATION_FAILED,
                    "persona index'i Stage 2 anindan bu yana degismis",
                )

    # 6) TUM DB satirlarindan (attributes TAZE) PersonaContext'leri kur ve
    #    KANONIK batching mantigini (build_persona_batches) YENIDEN kullan -
    #    boylece yeniden-uretilen tuple, Stage 2'nin urettigiyle birebir
    #    aynidir (yapiyi tahmin etmek yerine ayni saf fonksiyon cagirilir).
    contexts = tuple(persona_context_from_row(row) for row in rows_by_id.values())
    try:
        rebuilt = build_persona_batches(contexts, batch_size=DEFAULT_PERSONA_BATCH_SIZE)
    except Exception as exc:  # AIPipelineError alt tipleri dahil
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED,
            f"personalar kanonik batch'lere yeniden bolunemedi: {exc}",
        ) from exc

    # 7) Yeniden-uretilen batch yapisini manifeste karsi dogrula.
    if len(rebuilt) != len(manifest_set.batches):
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED,
            "yeniden-uretilen batch sayisi manifest ile eslesmiyor",
        )
    manifest_by_index = {b.batch_index: b for b in manifest_set.batches}
    for rebuilt_batch in rebuilt:
        manifest_batch = manifest_by_index.get(rebuilt_batch.batch_index)
        if manifest_batch is None:
            raise ManifestRehydrationError(
                ERROR_MANIFEST_REHYDRATION_FAILED,
                f"batch_index {rebuilt_batch.batch_index} manifestte yok",
            )
        # PAYLASILAN yardimci ile yeniden hesaplanan batch_hash, hem
        # manifesttekiyle hem de yeniden-uretilen batch'inkiyle eslesmeli.
        recomputed = compute_batch_hash(
            batch_index=rebuilt_batch.batch_index,
            personas=rebuilt_batch.personas,
            population_weight=rebuilt_batch.population_weight,
        )
        if recomputed != rebuilt_batch.batch_hash or recomputed != manifest_batch.batch_hash:
            raise ManifestRehydrationError(
                ERROR_MANIFEST_REHYDRATION_FAILED,
                f"batch_index {rebuilt_batch.batch_index} icin batch_hash uyusmuyor",
            )
        if rebuilt_batch.population_weight != manifest_batch.population_weight:
            raise ManifestRehydrationError(
                ERROR_MANIFEST_REHYDRATION_FAILED,
                f"batch_index {rebuilt_batch.batch_index} icin population_weight uyusmuyor",
            )

    # 8) KRITIK HASH KURALI: yeniden-uretilen tam tuple'in output_hash'i,
    #    manifestteki (Stage 2 stage_runner) output_hash ile AYNI olmalidir.
    rebuilt_output_hash = persona_batches_output_hash(rebuilt)
    if rebuilt_output_hash != manifest_set.output_hash:
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED,
            "rehydration edilmis PersonaBatch tuple'inin output_hash'i, "
            "Stage 2'nin kaydedilen output_hash'i ile eslesmiyor",
        )

    # 9) Toplam population_weight ozeti tutarli mi?
    total_weight = sum(b.population_weight for b in rebuilt)
    if total_weight != manifest_set.total_population_weight:
        raise ManifestRehydrationError(
            ERROR_MANIFEST_REHYDRATION_FAILED,
            "toplam population_weight, manifest ozetiyle eslesmiyor",
        )

    return rebuilt


__all__ = [
    "AIPipelinePersistenceError",
    "StageOutputDecodeError",
    "StageSchemaMismatchError",
    "ManifestRehydrationError",
    "ERROR_STAGE_OUTPUT_DECODE_FAILED",
    "ERROR_STAGE_SCHEMA_MISMATCH",
    "ERROR_MANIFEST_REHYDRATION_FAILED",
    "PERSONA_BATCH_MANIFEST_VERSION",
    "PersonaManifestEntry",
    "PersonaBatchManifest",
    "PersonaBatchManifestSet",
    "build_stage2_manifest",
    "stage_output_schema",
    "assert_stage_output_type",
    "encode_stage_output",
    "decode_stage_output",
    "decode_page_evidence",
    "decode_persona_batch_manifest",
    "decode_scenario_interpretation",
    "decode_persona_behavior_batch",
    "decode_aggregation_result",
    "decode_ux_report",
    "persona_context_from_row",
    "rehydrate_persona_batches",
]
