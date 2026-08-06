"""Stage 1 (EVIDENCE_PREPARATION) girdilerini `SimulationRun` verisinden
KURAN, PAYLASILAN saf yardimci (Faz 3B.2A).

`stage_runner.run_evidence_stage`, evidence girdilerini (source_type/metrics/
page_features/selected_modules/module_results) ACIK parametre olarak alir -
bu degerlerin `SimulationRun.result`/`input_snapshot`tan nasil turetilecegi
bilgisi bugune kadar hicbir yerde yoktu. Bu modul o turetmeyi TEK bir saf
yardimcida sabitler ki:

- `orchestration.initialize_ai_pipeline` (bu faz) Stage 1 `input_hash`ini
  (dolayisiyla QUEUED evidence satirinin deterministik `idempotency_key`ini)
  pipeline-init aninda, stage'i CALISTIRMADAN hesaplayabilsin;
- ileride (Faz 3B.2B) gercek worker, Stage 1'i calistirirken evidence
  girdilerini AYNI yardimciyla kurup `run_evidence_stage`e gecirsin - boylece
  worker'in urettigi `input_hash`/`idempotency_key`, init aninda kaydedilenle
  BIREBIR AYNI olur.

ONEMLI (bkz. bilinen riskler): Idempotency-key uyumunun korunmasi, Faz 3B.2B
worker'inin da bu yardimciyi kullanmasina baglidir - worker evidence
girdilerini farkli bir sekilde turetirse key eslesmez. Bu yardimci TAMAMEN
saftir (DB/ORM/ag yok); yalnizca duz `dict`ler alir.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.services.ai_pipeline.hashing import hash_payload
from app.services.ai_pipeline.schemas import (
    DEFAULT_PERSONA_BATCH_SIZE,
    AggregationResult,
    PageEvidence,
    PersonaBatch,
    PersonaBehaviorBatchOutput,
    ScenarioInterpretation,
)
from app.services.ai_pipeline.stage_hashing import evidence_stage_input_hash
from app.services.ai_pipeline.stage_inputs import (
    BaselineMetricSnapshot,
    ModuleSummary,
    PersonaBehaviorBatchInput,
    ScenarioInterpretationInput,
    UXReportInput,
)


@dataclass(frozen=True)
class EvidenceStageSource:
    """Stage 1'in evidence girdilerinin, `SimulationRun`tan turetilmis saf
    (ORM'siz) gorunumu - dogrudan `prepare_page_evidence`/`run_evidence_stage`
    parametrelerine akar."""

    source_type: str
    metrics: dict[str, Any]
    page_features: dict[str, Any] | None
    selected_modules: tuple[str, ...]
    module_results: dict[str, Any] | None


def build_evidence_stage_source(
    *,
    result: dict[str, Any] | None,
    input_snapshot: dict[str, Any] | None,
) -> EvidenceStageSource:
    """`SimulationRun.result` + `SimulationRun.input_snapshot`tan Stage 1
    evidence girdilerini kurar (allowlist edilmis, sabit alanlar).

    - `source_type`: `input_snapshot["source_type"]`
    - `metrics`: `result["metrics"]` (motorun ureptigi sabit metrik sozlugu)
    - `page_features`: `result["page_feature_snapshot"]`
    - `selected_modules`: `input_snapshot["modules"]`
    - `module_results`: `result["modules"]` (varsa)
    """

    result = result or {}
    input_snapshot = input_snapshot or {}

    raw_metrics = result.get("metrics")
    metrics: dict[str, Any] = dict(raw_metrics) if isinstance(raw_metrics, dict) else {}

    raw_page_features = result.get("page_feature_snapshot")
    page_features: dict[str, Any] | None = (
        dict(raw_page_features) if isinstance(raw_page_features, dict) else None
    )

    raw_modules = input_snapshot.get("modules")
    selected_modules: tuple[str, ...] = (
        tuple(str(m) for m in raw_modules) if isinstance(raw_modules, list | tuple) else ()
    )

    raw_module_results = result.get("modules")
    module_results: dict[str, Any] | None = (
        dict(raw_module_results) if isinstance(raw_module_results, dict) else None
    )

    return EvidenceStageSource(
        source_type=str(input_snapshot.get("source_type") or ""),
        metrics=metrics,
        page_features=page_features,
        selected_modules=selected_modules,
        module_results=module_results,
    )


def evidence_stage_source_input_hash(source: EvidenceStageSource) -> str:
    """`EvidenceStageSource`tan Stage 1 `input_hash`ini, `run_evidence_stage`
    ile BIREBIR ayni PAYLASILAN yardimciyla hesaplar."""

    return evidence_stage_input_hash(
        source_type=source.source_type,
        metrics=source.metrics,
        page_features=source.page_features,
        selected_modules=source.selected_modules,
        module_results=source.module_results,
    )


# --- Stage 2-6 PAYLASILAN source/input-hash yardimcilari (Faz 3B.2B) -------------
#
# Bu yardimcilar, Faz 3B.2B worker'inin (a) bir successor QUEUED satirini
# olustururken ve (b) o satiri calistirirken, her stage'in `input_hash`ini
# TEK, PAYLASILAN bir kaynaktan (bu modul) hesaplamasini saglar. Boylece
# worker icinde IKINCI bir hash algoritmasi/yapisi YENIDEN yazilmaz - her
# `*_stage_input_hash`, `stage_runner`daki ilgili `run_*` fonksiyonunun
# `input_hash` formuluyle BIREBIR ayni kanonik yapiyi (`hash_payload` + ayni
# strict input modeli) uretir. Bu fonksiyonlar TAMAMEN saftir (DB/ORM/ag yok);
# yalnizca hazir DTO'lar ve duz `dict`ler alir.

# Idempotency-key/input-hash amacli SAFE_ID (kucuk harf/rakam/_/:/./-) deseni
# (bkz. schemas.SafeId) - baseline metrik/modul anahtarlarini allowlist'lemek
# icin yeniden kullanilir; burada ikinci bir desen ICAT EDILMEZ.
_SAFE_ID_RE = re.compile(r"^[a-z0-9_:.\-]+$")

# Task/test/methodology metni, bugun `SimulationRun`/`Report` uzerinde
# authoritative olarak PERSIST EDILMEMEKTEDIR (bkz. bilinen sinirlamalar).
# Worker, deterministik/sinirli/allowlist'li bir baglam turetir; asagidaki
# sabitler, ilgili alan `input_snapshot`ta yoksa kullanilan guvenli, sabit
# yedeklerdir (PII/DOM/prompt TASIMAZ).
DEFAULT_TARGET_TASK = "Sayfadaki birincil kullanici gorevini tamamla"
DEFAULT_TEST_NAME = "ux_test"
DEFAULT_TEST_DESCRIPTION = "Genel kullanici kitlesi icin sentetik UX degerlendirmesi"
DEFAULT_METHODOLOGY_CONTEXT = (
    "Sentetik, kalibre edilmemis persona tabanli UX degerlendirmesi; gercek " "kullanici olcumu degildir."
)

# Faz 3C.2B1: launch aninda `SimulationRun.input_snapshot`a yazilan, SUNUCU
# TARAFINDAN kontrol edilen, SABIT ve SURUMLENMIS methodology metni. Kullanici
# girdisi DEGILDIR (kullanici bu degeri etkileyemez). Yeni bir surum gerekirse
# `METHODOLOGY_CONTEXT_V2` eklenip launch onu yazmaya baslar; eski run'lar
# kendi snapshot'larindaki degeri korur (deterministik hash stabilligi).
# `DEFAULT_METHODOLOGY_CONTEXT` (yukarida) yalnizca ESKI (bu alani hic
# tasimayan) run'lar icin geriye donuk yedek olarak DEGISMEDEN kalir.
METHODOLOGY_CONTEXT_V1 = (
    "Sentetik, kalibre edilmemis persona tabanli UX degerlendirmesi (Faz 3C surumu v1); "
    "gercek kullanici olcumu veya bir 'AI karari' degildir."
)

# HTML etiketi ve kontrol karakteri temizleme desenleri (launch-zamani baglam
# alanlarini duz metne indirger). ONEMLI: bu, prompt injection'i "tamamen
# temizledigimiz" ANLAMINA GELMEZ - `target_task`/`test_name`/`test_description`
# alanlari kullanici kaynaklidir ve prompt'larda GUVENILMEYEN veri olarak
# degerlendirilmelidir (provider katmani bu alanlari asla ayricalikli/sistem
# talimati olarak yorumlamamalidir). Buradaki normalize yalnizca depolama/
# gosterim guvenligi (kontrol karakteri, gizli HTML) icindir.
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def sanitize_context_text(value: object, *, max_len: int) -> str | None:
    """Kullanici kaynakli bir baglam metnini (target_task/test_name/
    test_description) guvenli, sinirli, duz metne indirger.

    - `str` DEGILSE (dict/list/None/sayi vb.) `None` doner - cagiran taraf
      deterministik bir fallback'e duser (asla ham/yanlis tipli veri yazilmaz).
    - Unicode NFC normalize + kontrol karakterleri temizlenir + HTML etiketleri
      cikarilir + ardisik bosluklar tek boslukla birlestirilip strip edilir.
    - Sonuc bos ise `None` (yine fallback); degilse `max_len`e kirpilir.

    NOT (bkz. yukaridaki desen yorumu): prompt injection'a karsi TAM koruma
    IDDIA ETMEZ; bu alanlar promptlarda guvenilmeyen veri olarak isaretlidir.
    """

    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _CONTROL_CHARS_RE.sub("", text)
    text = " ".join(text.split())
    if not text:
        return None
    return text[:max_len]


@dataclass(frozen=True)
class TaskContextSource:
    """Stage 3/6'nin task/test/methodology girdilerinin, `SimulationRun`tan
    turetilmis saf gorunumu. Alanlarin authoritative bir kaynagi henuz
    persist edilmedigi icin (bkz. bilinen sinirlamalar) deterministik,
    allowlist'li, sinirli degerlerden olusur."""

    target_task: str
    test_name: str
    test_description: str
    methodology_context: str


def _safe_text(value: object, *, max_len: int, fallback: str) -> str:
    """`value` bos olmayan bir metinse kirpilmis halini, degilse `fallback`i
    dondurur (min_length>=1 sozlesmelerini garantiler)."""

    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped[:max_len]
    return fallback


def build_task_context_source(*, input_snapshot: dict[str, Any] | None) -> TaskContextSource:
    """`SimulationRun.input_snapshot`tan (allowlist'li alanlar) Stage 3/6 icin
    deterministik task/test/methodology baglamini kurar.

    Faz 3C.2B1 sonrasi launch'lar, `input_snapshot`a AUTHORITATIVE baglam
    alanlarini (`target_task`/`test_name`/`test_description`/`methodology_
    context`) yazar (bkz. app.services.test_wizard.launch_draft). Bu alanlar
    varsa (yeni run) DOGRUDAN kullanilir. ESKI run'lar (bu alanlari hic
    tasimayan) icin, deterministik hash STABILLIGINI bozmamak adina onceki
    turetme mantigi AYNEN korunur (test adi <- `wizard_test_type`, test
    aciklamasi <- `target_audience`, methodology <- `DEFAULT_METHODOLOGY_
    CONTEXT`). `methodology_context` her yeni run'da SUNUCU tarafindan yazildigi
    icin "yeni snapshot" ayirt edici isaretidir.
    """

    snap = input_snapshot or {}
    if "methodology_context" in snap:
        # Yeni (Faz 3C.2B1+) authoritative snapshot.
        return TaskContextSource(
            target_task=_safe_text(snap.get("target_task"), max_len=500, fallback=DEFAULT_TARGET_TASK),
            test_name=_safe_text(snap.get("test_name"), max_len=200, fallback=DEFAULT_TEST_NAME),
            test_description=_safe_text(
                snap.get("test_description"), max_len=1000, fallback=DEFAULT_TEST_DESCRIPTION
            ),
            methodology_context=_safe_text(
                snap.get("methodology_context"), max_len=500, fallback=DEFAULT_METHODOLOGY_CONTEXT
            ),
        )
    # Eski run'lar: onceki turetme mantigi (backward-compat, hash stabilligi).
    return TaskContextSource(
        target_task=_safe_text(snap.get("target_task"), max_len=500, fallback=DEFAULT_TARGET_TASK),
        test_name=_safe_text(snap.get("wizard_test_type"), max_len=200, fallback=DEFAULT_TEST_NAME),
        test_description=_safe_text(
            snap.get("target_audience"), max_len=1000, fallback=DEFAULT_TEST_DESCRIPTION
        ),
        methodology_context=DEFAULT_METHODOLOGY_CONTEXT,
    )


def build_baseline_metrics(*, result: dict[str, Any] | None) -> tuple[BaselineMetricSnapshot, ...]:
    """`SimulationRun.result["metrics"]`ten Stage 4/6 baseline metriklerini
    ALLOWLIST'leyerek kurar (ham `result` TOPLUCA gecirilmez).

    Yalnizca: anahtari SafeId desenine uyan, degeri sayisal bir `point_estimate`
    tasiyan metrikler alinir; en fazla 20 tanesi (stage_inputs siniri),
    metric_id'ye gore deterministik siralamada, tekrarsiz."""

    res = result or {}
    raw_metrics = res.get("metrics")
    if not isinstance(raw_metrics, dict):
        return ()

    snapshots: list[BaselineMetricSnapshot] = []
    for key in sorted(str(k) for k in raw_metrics):
        if len(key) > 100 or not _SAFE_ID_RE.match(key):
            continue
        value = raw_metrics.get(key)
        if not isinstance(value, dict):
            continue
        point = value.get("point_estimate")
        if not isinstance(point, int | float) or isinstance(point, bool):
            continue

        def _opt_float(raw: object) -> float | None:
            return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None

        snapshots.append(
            BaselineMetricSnapshot(
                metric_id=key,
                point_estimate=float(point),
                low=_opt_float(value.get("low")),
                high=_opt_float(value.get("high")),
            )
        )
        if len(snapshots) >= 20:
            break
    return tuple(snapshots)


def build_module_summary(*, input_snapshot: dict[str, Any] | None) -> tuple[ModuleSummary, ...]:
    """`SimulationRun.input_snapshot["modules"]`ten Stage 6 modul ozetini
    (yalnizca "present" bilgisi, ham modul sonucu YOK) allowlist'leyerek kurar."""

    snap = input_snapshot or {}
    raw = snap.get("modules")
    if not isinstance(raw, list | tuple):
        return ()

    summaries: list[ModuleSummary] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or len(item) > 100 or not _SAFE_ID_RE.match(item):
            continue
        if item in seen:
            continue
        seen.add(item)
        summaries.append(ModuleSummary(module_key=item, present=True))
        if len(summaries) >= 20:
            break
    return tuple(summaries)


def scenario_stage_input(
    *, evidence: PageEvidence, task_context: TaskContextSource
) -> ScenarioInterpretationInput:
    """Stage 3 (SCENARIO_INTERPRETATION) strict provider girdisini kurar -
    `stage_runner.run_scenario_stage`in ic olarak kurdugu nesne ile BIREBIR
    ayni alanlardan olusur."""

    return ScenarioInterpretationInput(
        evidence=evidence,
        target_task=task_context.target_task,
        test_name=task_context.test_name,
        test_description=task_context.test_description,
        methodology_context=task_context.methodology_context,
    )


def batching_stage_input_hash(*, evidence_output_hash: str) -> str:
    """Stage 2 (PERSONA_BATCH_PREPARATION) `input_hash`i -
    `run_batching_stage` ile BIREBIR ayni."""

    return hash_payload({"batch_size": DEFAULT_PERSONA_BATCH_SIZE, "evidence": evidence_output_hash})


def scenario_stage_input_hash(*, evidence: PageEvidence, task_context: TaskContextSource) -> str:
    """Stage 3 `input_hash`i - `run_scenario_stage` ile BIREBIR ayni."""

    return hash_payload(
        scenario_stage_input(evidence=evidence, task_context=task_context).model_dump(mode="json")
    )


def persona_behavior_stage_input_hash(
    *,
    batch: PersonaBatch,
    evidence: PageEvidence,
    scenario: ScenarioInterpretation,
    baseline_metrics: tuple[BaselineMetricSnapshot, ...],
) -> str:
    """Stage 4 (PERSONA_BEHAVIOR, tek batch) `input_hash`i -
    `run_persona_behavior_batch` ile BIREBIR ayni."""

    return hash_payload(
        PersonaBehaviorBatchInput(
            batch=batch,
            evidence=evidence,
            scenario=scenario,
            baseline_metrics=baseline_metrics,
        ).model_dump(mode="json")
    )


def aggregation_stage_input_hash(
    *,
    behavior_outputs: tuple[PersonaBehaviorBatchOutput, ...],
    scenario: ScenarioInterpretation,
) -> str:
    """Stage 5 (AGGREGATION) `input_hash`i - `run_aggregation_stage` ile
    BIREBIR ayni kanonik yapi."""

    return hash_payload(
        {
            "behavior_outputs": [o.model_dump(mode="json") for o in behavior_outputs],
            "scenario": scenario.model_dump(mode="json"),
        }
    )


def ux_report_stage_input_hash(
    *,
    evidence: PageEvidence,
    baseline_metrics: tuple[BaselineMetricSnapshot, ...],
    aggregation: AggregationResult,
    module_summary: tuple[ModuleSummary, ...],
    task_context: TaskContextSource,
) -> str:
    """Stage 6 (UX_REPORT) `input_hash`i - `run_ux_report_stage` ile BIREBIR
    ayni."""

    return hash_payload(
        UXReportInput(
            evidence=evidence,
            baseline_metrics=baseline_metrics,
            aggregation=aggregation,
            module_summary=module_summary,
            methodology_context=task_context.methodology_context,
        ).model_dump(mode="json")
    )


__all__ = [
    "EvidenceStageSource",
    "build_evidence_stage_source",
    "evidence_stage_source_input_hash",
    "TaskContextSource",
    "DEFAULT_TARGET_TASK",
    "DEFAULT_TEST_NAME",
    "DEFAULT_TEST_DESCRIPTION",
    "DEFAULT_METHODOLOGY_CONTEXT",
    "METHODOLOGY_CONTEXT_V1",
    "sanitize_context_text",
    "build_task_context_source",
    "build_baseline_metrics",
    "build_module_summary",
    "scenario_stage_input",
    "batching_stage_input_hash",
    "scenario_stage_input_hash",
    "persona_behavior_stage_input_hash",
    "aggregation_stage_input_hash",
    "ux_report_stage_input_hash",
]
