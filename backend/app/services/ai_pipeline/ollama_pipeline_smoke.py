"""Faz 3D.3B: manuel, alti-asamali (Stage 1-6) yerel Ollama pipeline duman testi.

Otomatik pytest suite'inin BIR PARCASI DEGILDIR ve pytest tarafindan ASLA
otomatik calistirilmaz - yalnizca bir gelistirici, gercekten calisan bir
Ollama daemon'ina karsi manuel olarak calistirdiginda bir sey yapar
(`python -m app.services.ai_pipeline.ollama_pipeline_smoke --confirm-local-live`).

Bu modul GERCEK, DB'siz production yardimcilarini dogrudan cagirir - hicbir
is mantigi burada YENIDEN yazilmaz:
- `app.services.ai_pipeline.stage_runner` (Stage 1-6 calistiricilari; kendi
  icinde `app.services.ai_pipeline.evidence`/`batching`/`aggregation`/
  `validation`/`prompts` modullerini zaten cagirir)
- `app.services.ai_pipeline.ollama_provider.OllamaProvider` (gercek adapter)

`stage_runner` fonksiyonlari HICBIR DB oturumu/Redis/arq KULLANMAZ - yalnizca
bir `simulation_run_id` (burada sabit, hicbir DB satirina baglanmayan bir
UUID) hash/audit girdisi olarak alirlar. Bu script de ayni sekilde hicbir
DB/Redis/arq/chip/SimulationRun kaydi OLUSTURMAZ.

Guvenlik kapilari:
- `--confirm-local-live` ACIKCA verilmeden hicbir provider olusturulmaz,
  hicbir istek atilmaz.
- `AI_REPORT_ENABLED=true` VE `AI_REPORT_PROVIDER=ollama` VE
  `OLLAMA_STRUCTURED_OUTPUT_MODE=json` OLMADIKCA calismayi reddeder -
  otomatik baska bir moda/provider'a DUSULMEZ.
- Basari durumunda EN FAZLA UC gercek `generate_structured` cagrisi yapar
  (Stage 3/4/6); Stage 1/2/5 provider'a hic ULASMAZ.
- Bir asama basarisiz olursa sonraki asamalar CAGRILMAZ, provider icinde
  veya bu harness icinde retry YAPILMAZ.
- Terminale yalnizca sanitize edilmis ozet alanlari yazdirilir - ham
  prompt/model cevabi/tam rapor/persona etiketi-niteligi/sayfa evidence
  metni/dosya yolu/ortam degeri ASLA yazdirilmaz.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field

from app.config import settings
from app.services.ai_pipeline import stage_runner
from app.services.ai_pipeline.ollama_provider import OllamaProvider
from app.services.ai_pipeline.provider import AIProvider
from app.services.ai_pipeline.provider_errors import AIProviderConfigurationError
from app.services.ai_pipeline.schemas import PersonaContext
from app.services.ai_pipeline.stage_runner import PipelineStageError, StageAudit

# Sabit, hicbir DB satirina baglanmayan bir UUID - `stage_runner` fonksiyonlari
# bunu YALNIZCA hash/idempotency-key girdisi olarak kullanir, hicbir DB
# sorgusu/yazisi tetiklemez.
_SIMULATION_RUN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")

# UX raporunun "bu sentetik/dogrulanmamis bir tahmindir" uyarisini gercekten
# tasidigini (LLM'in serbest metnini tam olarak tahmin ETMEDEN) dogrulamak
# icin kullanilan, kucuk/sabit bir anahtar kelime kumesi - promptun/ortak
# guvenlik kurallarinin ("gercek kullanici testi yapildigini ASLA iddia
# etme") zaten talep ettigi ifadelerle es-anlamli.
_SYNTHETIC_WARNING_KEYWORDS = (
    "sentetik",
    "gerçek kullanıcı",
    "gercek kullanici",
    "dogrulanmam",
    "doğrulanmam",
)

_HARNESS_ERROR_SYNTHETIC_WARNING_MISSING = "synthetic_warning_missing"
_HARNESS_ERROR_FIXTURE_POPULATION_WEIGHT = "fixture_population_weight_invalid"
_HARNESS_ERROR_AGGREGATION_AFFECTED_USERS_OUT_OF_BOUNDS = "aggregation_affected_users_out_of_bounds"


def _build_synthetic_personas() -> tuple[PersonaContext, ...]:
    """Sabit, kisisel veri icermeyen 3 persona - toplam population_weight=100.

    `persona_id` degerleri SABIT (rastgele degil) ki girdi her calistirmada
    byte-for-byte ayni olsun (dolayisiyla hash'ler de deterministik kalsin).
    """

    return (
        PersonaContext(
            persona_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            index=0,
            label="persona-0",
            attributes={"region": "marmara", "age_range": "18-25", "device_class": "mobile"},
            population_weight=50,
        ),
        PersonaContext(
            persona_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            index=1,
            label="persona-1",
            attributes={"region": "ic_anadolu", "age_range": "26-40", "device_class": "desktop"},
            population_weight=30,
        ),
        PersonaContext(
            persona_id=uuid.UUID("00000000-0000-0000-0000-000000000003"),
            index=2,
            label="persona-2",
            attributes={"region": "akdeniz", "age_range": "41-55", "device_class": "mobile"},
            population_weight=20,
        ),
    )


def _synthetic_evidence_inputs() -> tuple[dict[str, object], dict[str, object]]:
    """Sentetik bir 'urun kayit sayfasi' icin metrics/page_features.

    Yalnizca sayisal/yapisal ozet degerleri icerir - hicbir script, PII,
    form DEGERI, cookie veya token yoktur (bkz. `app.services.ai_pipeline.
    evidence.prepare_page_evidence`'in kabul ettigi allowlist).
    """

    metrics: dict[str, object] = {
        "task_completion_probability": {"point_estimate": 0.62},
        "misclick_probability": {"point_estimate": 0.18},
        "abandonment_probability": {"point_estimate": 0.22},
        "task_duration_seconds": {"point_estimate": 45.0},
        # Bilinen erisilebilirlik sorunu: dusuk kontrast orani.
        "contrast_check": {"pass": False, "avg_ratio": 3.2},
    }
    page_features: dict[str, object] = {
        "nav_depth": 2,
        "primary_cta_count": 1,
        # Bilinen form surtunme noktasi: goreceli olarak yuksek alan sayisi.
        "form_field_count": 7,
        "above_fold_cta": True,
        "heading_count": 3,
        "mobile_friendly": True,
        "min_contrast_ratio": 2.1,
        "avg_contrast_ratio": 3.2,
    }
    return metrics, page_features


@dataclass
class StageReport:
    """Tek bir asamanin GUVENLI (sanitize edilmis) ozeti - ham prompt/cikti YOK."""

    stage: str
    batch_index: int | None
    success: bool
    prompt_key: str | None = None
    prompt_version: str | None = None
    input_hash_prefix: str | None = None
    output_hash_prefix: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    error_code: str | None = None


@dataclass
class PipelineSmokeResult:
    stage_reports: list[StageReport] = field(default_factory=list)
    generation_calls_made: int = 0
    persona_count: int = 0
    population_weight_total: int = 0
    behavior_persona_count: int | None = None
    persona_index_integrity_ok: bool | None = None
    aggregation_total_population: int | None = None
    finding_count: int | None = None
    synthetic_warning_present: bool | None = None
    success: bool = False
    failed_stage: str | None = None
    failed_error_code: str | None = None


def _pure_stage_report(audit: StageAudit) -> StageReport:
    return StageReport(
        stage=audit.stage_type.value,
        batch_index=audit.batch_index,
        success=True,
        input_hash_prefix=audit.input_hash[:12],
        output_hash_prefix=audit.output_hash[:12],
        duration_ms=audit.duration_ms,
    )


def _provider_stage_report(audit: StageAudit) -> StageReport:
    return StageReport(
        stage=audit.stage_type.value,
        batch_index=audit.batch_index,
        success=True,
        prompt_key=audit.prompt_key,
        prompt_version=audit.prompt_version,
        input_hash_prefix=audit.input_hash[:12],
        output_hash_prefix=audit.output_hash[:12],
        input_tokens=audit.input_tokens,
        output_tokens=audit.output_tokens,
        duration_ms=audit.duration_ms,
    )


def _failed_stage_report(*, stage: str, batch_index: int | None, error_code: str) -> StageReport:
    return StageReport(stage=stage, batch_index=batch_index, success=False, error_code=error_code)


async def run_pipeline_smoke(
    provider: AIProvider,
    *,
    personas: tuple[PersonaContext, ...] | None = None,
    aggregation_personas: tuple[PersonaContext, ...] | None = None,
) -> PipelineSmokeResult:
    """Alti asamayi (Stage 1-6) SIRAYLA calistirir; bir asama basarisiz olursa
    HEMEN durur (sonraki asamalar cagrilmaz, retry yapilmaz).

    `personas`/`aggregation_personas` yalnizca OFFLINE TESTLER icin bir
    genisletme noktasidir (varsayilan davranista ikisi de ayni sabit sentetik
    fixture'dir) - gercek CLI girisi (`main`) bu parametreleri HICBIR ZAMAN
    gecmez.
    """

    active_personas = personas if personas is not None else _build_synthetic_personas()
    active_aggregation_personas = (
        aggregation_personas if aggregation_personas is not None else active_personas
    )

    result = PipelineSmokeResult(
        persona_count=len(active_personas),
        population_weight_total=sum(p.population_weight for p in active_personas),
    )

    # Fixture-seviyesi on-kosul: population_weight toplami. Gercek CLI
    # girisinde bu her zaman 100'dur; offline testlerde kasitli olarak farkli
    # bir fixture verilirse bu deger DOGRU sekilde raporlanir (hicbir yerde
    # sessizce 100'e sabitlenmez).
    if result.population_weight_total <= 0:
        result.failed_stage = "persona_batch_preparation"
        result.failed_error_code = _HARNESS_ERROR_FIXTURE_POPULATION_WEIGHT
        result.stage_reports.append(
            _failed_stage_report(
                stage="persona_batch_preparation",
                batch_index=None,
                error_code=_HARNESS_ERROR_FIXTURE_POPULATION_WEIGHT,
            )
        )
        return result

    metrics, page_features = _synthetic_evidence_inputs()

    # --- Stage 1: EVIDENCE_PREPARATION (saf, provider'a ULASMAZ) ---------------
    try:
        evidence_run = stage_runner.run_evidence_stage(
            simulation_run_id=_SIMULATION_RUN_ID,
            source_type="synthetic_smoke",
            metrics=metrics,
            page_features=page_features,
            selected_modules=(),
            module_results=None,
        )
    except PipelineStageError as exc:
        result.stage_reports.append(
            _failed_stage_report(
                stage=exc.stage_type.value, batch_index=exc.batch_index, error_code=exc.error_code
            )
        )
        result.failed_stage = exc.stage_type.value
        result.failed_error_code = exc.error_code
        return result
    result.stage_reports.append(_pure_stage_report(evidence_run.audit))

    # --- Stage 2: PERSONA_BATCH_PREPARATION (saf, provider'a ULASMAZ) ----------
    try:
        batching_run = stage_runner.run_batching_stage(
            simulation_run_id=_SIMULATION_RUN_ID,
            personas=active_personas,
            evidence_output_hash=evidence_run.audit.output_hash,
        )
    except PipelineStageError as exc:
        result.stage_reports.append(
            _failed_stage_report(
                stage=exc.stage_type.value, batch_index=exc.batch_index, error_code=exc.error_code
            )
        )
        result.failed_stage = exc.stage_type.value
        result.failed_error_code = exc.error_code
        return result
    result.stage_reports.append(_pure_stage_report(batching_run.audit))
    batches = batching_run.output

    # --- Stage 3: SCENARIO_INTERPRETATION (provider cagrisi #1) ----------------
    result.generation_calls_made += 1
    try:
        scenario_run = await stage_runner.run_scenario_stage(
            simulation_run_id=_SIMULATION_RUN_ID,
            provider=provider,
            evidence=evidence_run.output,
            target_task="Yeni kullanici kayit formunu tamamla",
            test_name="ollama-pipeline-smoke",
            test_description="Sentetik urun kayit sayfasi icin manuel, alti asamali pipeline duman testi.",
            methodology_context=(
                "Bu, gercek kullanici verisi icermeyen, sentetik ve deterministik bir duman testidir."
            ),
        )
    except PipelineStageError as exc:
        result.stage_reports.append(
            _failed_stage_report(
                stage=exc.stage_type.value, batch_index=exc.batch_index, error_code=exc.error_code
            )
        )
        result.failed_stage = exc.stage_type.value
        result.failed_error_code = exc.error_code
        return result
    result.stage_reports.append(_provider_stage_report(scenario_run.audit))

    # --- Stage 4: PERSONA_BEHAVIOR (provider cagrisi #2, tek batch) -----------
    result.generation_calls_made += 1
    try:
        behavior_run = await stage_runner.run_persona_behavior_batch(
            simulation_run_id=_SIMULATION_RUN_ID,
            provider=provider,
            batch=batches[0],
            evidence=evidence_run.output,
            scenario=scenario_run.output,
            baseline_metrics=(),
        )
    except PipelineStageError as exc:
        result.stage_reports.append(
            _failed_stage_report(
                stage=exc.stage_type.value, batch_index=exc.batch_index, error_code=exc.error_code
            )
        )
        result.failed_stage = exc.stage_type.value
        result.failed_error_code = exc.error_code
        return result
    result.stage_reports.append(_provider_stage_report(behavior_run.audit))
    result.behavior_persona_count = len(behavior_run.output.persona_results)
    # `run_persona_behavior_batch` basarili donduyse `validate_persona_behavior_
    # batch` (gercek production dogrulamasi) zaten persona index kumesinin
    # batch'le TAM eslestigini garanti eder - burada yalnizca AYNI garantiyi
    # raporlanabilir bir alan olarak ACIKCA yansitiyoruz (ikinci bir dogrulama
    # ALGORITMASI icat etmiyoruz).
    result.persona_index_integrity_ok = {r.persona_index for r in behavior_run.output.persona_results} == {
        p.index for p in batches[0].personas
    }

    # --- Stage 5: AGGREGATION (saf, provider'a ULASMAZ) ------------------------
    try:
        aggregation_run = stage_runner.run_aggregation_stage(
            simulation_run_id=_SIMULATION_RUN_ID,
            personas=active_aggregation_personas,
            behavior_outputs=(behavior_run.output,),
            scenario=scenario_run.output,
        )
    except PipelineStageError as exc:
        result.stage_reports.append(
            _failed_stage_report(
                stage=exc.stage_type.value, batch_index=exc.batch_index, error_code=exc.error_code
            )
        )
        result.failed_stage = exc.stage_type.value
        result.failed_error_code = exc.error_code
        return result
    result.stage_reports.append(_pure_stage_report(aggregation_run.audit))
    aggregation = aggregation_run.output
    result.aggregation_total_population = aggregation.total_population

    # Etkilenen kullanici sayisi [0, total_population] disina CIKAMAZ - bu,
    # `aggregate_persona_behavior`in agirlikli toplamindan matematiksel olarak
    # zaten garanti edilir; burada ACIKCA (ve raporlanabilir sekilde) yeniden
    # dogrulaniyor.
    for issue in aggregation.common_issues:
        if not (0 <= issue.affected_users <= aggregation.total_population):
            result.failed_stage = "aggregation"
            result.failed_error_code = _HARNESS_ERROR_AGGREGATION_AFFECTED_USERS_OUT_OF_BOUNDS
            result.stage_reports.append(
                _failed_stage_report(
                    stage="aggregation",
                    batch_index=None,
                    error_code=_HARNESS_ERROR_AGGREGATION_AFFECTED_USERS_OUT_OF_BOUNDS,
                )
            )
            return result

    # --- Stage 6: UX_REPORT (provider cagrisi #3) ------------------------------
    result.generation_calls_made += 1
    try:
        report_run = await stage_runner.run_ux_report_stage(
            simulation_run_id=_SIMULATION_RUN_ID,
            provider=provider,
            evidence=evidence_run.output,
            baseline_metrics=(),
            aggregation=aggregation,
            module_summary=(),
            methodology_context=(
                "Bu, gercek kullanici verisi icermeyen, sentetik ve deterministik bir duman testidir."
            ),
        )
    except PipelineStageError as exc:
        result.stage_reports.append(
            _failed_stage_report(
                stage=exc.stage_type.value, batch_index=exc.batch_index, error_code=exc.error_code
            )
        )
        result.failed_stage = exc.stage_type.value
        result.failed_error_code = exc.error_code
        return result
    result.stage_reports.append(_provider_stage_report(report_run.audit))

    report = report_run.output
    result.finding_count = len(report.findings)
    disclaimer_text = f"{report.disclaimer} {report.limitations}".lower()
    result.synthetic_warning_present = any(kw in disclaimer_text for kw in _SYNTHETIC_WARNING_KEYWORDS)

    if not result.synthetic_warning_present:
        # "Sentetik uyarı zorunludur" (bkz. gorev talimati) - eksikse pipeline
        # basarili SAYILMAZ, provider cagrisinin kendisi basarili olsa bile.
        result.failed_stage = "ux_report"
        result.failed_error_code = _HARNESS_ERROR_SYNTHETIC_WARNING_MISSING
        return result

    result.success = True
    return result


def _print_report(result: PipelineSmokeResult, provider: OllamaProvider) -> None:
    """Yalnizca sanitize edilmis ozet alanlarini yazdirir - ham prompt/cikti,
    persona etiketi/niteligi, sayfa evidence metni, dosya yolu veya ortam
    degeri ASLA yazdirilmaz."""

    print("provider:", provider.provider_name)
    print("model:", provider.model_name)
    print("structured_output_mode:", settings.ollama_structured_output_mode)
    print("persona_count:", result.persona_count)
    print("population_weight_total:", result.population_weight_total)
    print()

    for stage_report in result.stage_reports:
        label = stage_report.stage
        if stage_report.batch_index is not None:
            label += f" (batch={stage_report.batch_index})"
        print(f"--- stage: {label} ---")
        print("  success:", stage_report.success)
        if stage_report.prompt_key is not None:
            print("  prompt_key:", stage_report.prompt_key)
            print("  prompt_version:", stage_report.prompt_version)
        if stage_report.input_hash_prefix is not None:
            print("  input_hash_prefix:", stage_report.input_hash_prefix)
            print("  output_hash_prefix:", stage_report.output_hash_prefix)
        if stage_report.success:
            print("  input_tokens:", stage_report.input_tokens)
            print("  output_tokens:", stage_report.output_tokens)
            print("  duration_ms:", stage_report.duration_ms)
        else:
            print("  error_code:", stage_report.error_code)

    print()
    total_input_tokens = sum(r.input_tokens for r in result.stage_reports)
    total_output_tokens = sum(r.output_tokens for r in result.stage_reports)
    total_duration_ms = sum(r.duration_ms for r in result.stage_reports)
    print("total_input_tokens:", total_input_tokens)
    print("total_output_tokens:", total_output_tokens)
    print("total_duration_ms:", total_duration_ms)
    print("estimated_cost_usd:", 0.0)
    print("generation_calls_made:", result.generation_calls_made)
    if result.behavior_persona_count is not None:
        print("behavior_persona_count:", result.behavior_persona_count)
    if result.persona_index_integrity_ok is not None:
        print("persona_index_integrity_ok:", result.persona_index_integrity_ok)
    if result.aggregation_total_population is not None:
        print("aggregation_total_population:", result.aggregation_total_population)
    if result.finding_count is not None:
        print("ux_report_finding_count:", result.finding_count)
    if result.synthetic_warning_present is not None:
        print("synthetic_warning_present:", result.synthetic_warning_present)
    print("success:", result.success)
    if not result.success:
        print("failed_stage:", result.failed_stage)
        print("failed_error_code:", result.failed_error_code)


async def _run(*, confirm_local_live: bool) -> int:
    if not confirm_local_live:
        print("HATA: --confirm-local-live bayragi olmadan calismaz.", file=sys.stderr)
        return 2
    if not settings.ai_report_enabled:
        print("HATA: AI_REPORT_ENABLED=true olmadan calismaz.", file=sys.stderr)
        return 2
    if settings.ai_report_provider != "ollama":
        print(
            f"HATA: AI_REPORT_PROVIDER='{settings.ai_report_provider}' (beklenen: 'ollama').",
            file=sys.stderr,
        )
        return 2
    if settings.ollama_structured_output_mode != "json":
        print(
            f"HATA: OLLAMA_STRUCTURED_OUTPUT_MODE='{settings.ollama_structured_output_mode}' "
            "(bu duman testi yalnizca 'json' modunda calisir - bkz. Faz 3D.3A.2 kok neden notu).",
            file=sys.stderr,
        )
        return 2

    try:
        provider = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            temperature=settings.ollama_temperature,
            keep_alive=settings.ollama_keep_alive,
            num_ctx=settings.ollama_num_ctx,
            max_output_tokens=settings.ollama_max_output_tokens,
            max_concurrency=settings.ollama_max_concurrency,
            allow_remote_host=settings.ollama_allow_remote_host,
            structured_output_mode=settings.ollama_structured_output_mode,
        )
    except AIProviderConfigurationError as exc:
        print(f"HATA: provider yapilandirma hatasi: {exc.error_code}", file=sys.stderr)
        return 2

    try:
        result = await run_pipeline_smoke(provider)
    finally:
        await provider.aclose()

    _print_report(result, provider)
    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Manuel, alti-asamali (Stage 1-6) yerel Ollama pipeline duman testi "
            "(gercek network cagrisi yapar - en fazla uc kez)."
        )
    )
    parser.add_argument(
        "--confirm-local-live",
        action="store_true",
        default=False,
        help="Gercek/yerel Ollama'ya en fazla uc istek atmayi ACIKCA onaylar (zorunlu).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(confirm_local_live=args.confirm_local_live))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PipelineSmokeResult",
    "StageReport",
    "run_pipeline_smoke",
    "main",
]
