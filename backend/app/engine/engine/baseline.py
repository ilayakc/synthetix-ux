"""Aciklanabilir, deterministik heuristic baseline simulasyon motoru.

Bu motor gercek insan davranisi UretMEZ. Girdileri (sayfa ozellikleri,
persona ornegi), kurallari (bkz. `app.engine.rules_config`) ve belirsizlik
uretim yontemini (ucgen/triangular dagilim) acikca dokumante eder - bkz.
docs/methodology.md. Rastgele sayi ureteci (`random.Random(deterministic_seed)`)
YALNIZCA nokta tahminleri etrafinda bir belirsizlik araligi/dagilim ortaya
koymak icin kullanilir; rastgelelik tek basina bir "model" degildir ve hicbir
nokta tahmini rastgele uretilmez (tum nokta tahminleri `rules_config`
agirliklarindan deterministik olarak hesaplanir).

Ayni (input_snapshot, deterministic_seed, rules_version) girdisi HER ZAMAN
bit-esit (aynı dict) sonucu uretir (bkz. test_simulation_engine.py
determinism testleri).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from app.engine import fixtures
from app.engine.rules_config import get_rules_config

BASELINE_ENGINE_VERSION = "heuristic-baseline-2026.1"

# Bilimsel durustluk: bu motorun urettigi hicbir metinde gecmesi yasak olan
# ifadeler (bkz. docs/scientific-integrity.md ve app.services.personas
# BANNED_CLAIM_PHRASES ile ayni ruh). Ek olarak bu prompt'un acikca
# yasakladigi "kesin neden-sonuc / gercek donusum / gercek goz takibi /
# pazar temsili" iddialari da buraya eklenmistir.
BANNED_CLAIM_PHRASES = (
    "gercek pazar talebi",
    "gerçek pazar talebi",
    "pazar temsili",
    "dogrulanmis",
    "doğrulanmış",
    "bilimsel olarak kanitlanmis",
    "bilimsel olarak kanıtlanmış",
    "istatistiksel olarak anlamli",
    "istatistiksel olarak anlamlı",
    "gercek donusum",
    "gerçek dönüşüm",
    "goz takibi",
    "göz takibi",
    "eye tracking",
)

RESULT_DISCLAIMER = (
    "Bu sonuclar sentetik senaryo tahminidir; kalibre edilmemis (uncalibrated) "
    "bir heuristic modelden uretilmistir ve gercek kullanici verisi degildir. "
    "Erken asama fikir uretimi/hipotez olusturma icin kullanilabilir; gercek "
    "kullanilabilirlik testinin yerini almaz. Metodoloji icin bkz. "
    "docs/methodology.md."
)

COMPARISON_NOTE = (
    "Bu bir simulasyon farkidir; istatistiksel anlamlilik iddia edilmez. "
    "Kalibre edilmemis sentetik personalar uzerinden hesaplanmistir."
)


class SimulationInputError(ValueError):
    """input_snapshot heuristic motor icin gecersiz/eksik oldugunda firlatilir."""


def assert_no_banned_claims(text: str) -> None:
    """Verilen metnin yasakli iddia ifadelerinden birini icermedigini dogrular."""

    normalized = text.strip().lower()
    for phrase in BANNED_CLAIM_PHRASES:
        if phrase in normalized:
            raise SimulationInputError(
                f"Uretilen metin yasakli bir iddia iceriyor: '{phrase}' "
                "(bkz. docs/scientific-integrity.md)"
            )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _triangular_quantile(low: float, mode: float, high: float, p: float) -> float:
    if high <= low:
        return low
    mode = _clamp(mode, low, high)
    crossover = (mode - low) / (high - low)
    if p <= crossover:
        return low + math.sqrt(max(p, 0.0) * (high - low) * (mode - low))
    return high - math.sqrt(max(1.0 - p, 0.0) * (high - low) * (high - mode))


def _uncertainty_interval(point: float, half_width_ratio: float, lo_bound: float, hi_bound: float) -> dict:
    half_width = abs(point) * half_width_ratio
    low = _clamp(point - half_width, lo_bound, hi_bound)
    high = _clamp(point + half_width, lo_bound, hi_bound)
    return {
        "distribution": "triangular",
        "point_estimate": round(point, 4),
        "low": round(low, 4),
        "mode": round(point, 4),
        "high": round(high, 4),
    }


def _digital_literacy_weight(persona_sample: dict | None, weights: dict[str, float]) -> float:
    """Persona ornegindeki dijital yatkinlik dagilimina gore agirlikli carpan.

    Persona ornegi yoksa (segment secilmemisse) notr carpan (1.0) kullanilir.
    """

    if not persona_sample:
        return 1.0

    segments = persona_sample.get("segments") or []
    total = sum(s.get("count", 0) for s in segments)
    if total <= 0:
        return 1.0

    weighted_sum = 0.0
    matched = 0
    for segment in segments:
        literacy = segment.get("dimension_values", {}).get("digital_literacy")
        if literacy is None:
            continue
        multiplier = weights.get(literacy, 1.0)
        weighted_sum += multiplier * segment.get("count", 0)
        matched += segment.get("count", 0)

    if matched == 0:
        return 1.0
    # Boyut secilmemis (eslesmeyen) kalan pay notr (1.0) carpanla agirliklandirilir.
    unmatched = total - matched
    return (weighted_sum + unmatched * 1.0) / total


def _regional_interest_from_persona_sample(persona_sample: dict | None) -> list[dict]:
    """Bolgesel tahmini ilgiyi persona ornegindeki (varsa) scenario_interest'ten turetir.

    Bu bir tahmin/varsayimdir, dogrulanmis arastirma verisine dayanmaz (bkz.
    app.services.personas.REGIONAL_INTEREST_DISCLAIMER). Region boyutu ya da
    scenario_interest verisi yoksa bos liste dondurulur (iddia uretilmez).
    """

    if not persona_sample:
        return []

    distribution = persona_sample.get("distribution_snapshot", {}).get("distribution", {})
    region_buckets = {b["key"]: b for b in distribution.get("region", []) if "key" in b}
    if not region_buckets:
        return []

    segments = persona_sample.get("segments") or []
    total = sum(s.get("count", 0) for s in segments) or 1
    share_by_region: dict[str, int] = {}
    for segment in segments:
        region_key = segment.get("dimension_values", {}).get("region")
        if region_key is None:
            continue
        share_by_region[region_key] = share_by_region.get(region_key, 0) + segment.get("count", 0)

    results: list[dict] = []
    for region_key, count in share_by_region.items():
        bucket = region_buckets.get(region_key)
        scenario_interest = bucket.get("scenario_interest") if bucket else None
        if not scenario_interest:
            continue
        results.append(
            {
                "region_key": region_key,
                "region_label": bucket.get("label", region_key),
                "share": round(count / total, 4),
                "estimate": scenario_interest.get("estimate"),
                "confidence": scenario_interest.get("confidence"),
                "assumption": scenario_interest.get("assumption"),
                "disclaimer": scenario_interest.get("disclaimer"),
            }
        )
    results.sort(key=lambda r: r["region_key"])
    return results


def _hash_input_snapshot(input_snapshot: dict, deterministic_seed: int, rules_version: str) -> str:
    payload = json.dumps(
        {"input_snapshot": input_snapshot, "seed": deterministic_seed, "rules_version": rules_version},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_baseline_simulation(
    input_snapshot: dict,
    deterministic_seed: int,
    rules_version: str | None = None,
) -> dict:
    """Verilen girdiden deterministik bir sentetik simulasyon sonucu uretir.

    Sonuc, `SimulationRun.result` JSON kolonuna dogrudan yazilabilecek, salt
    JSON-uyumlu (str/int/float/bool/list/dict) bir sozluktur.
    """

    url = input_snapshot.get("url")
    if not url or not isinstance(url, str):
        raise SimulationInputError("input_snapshot.url gereklidir")
    role = input_snapshot.get("role", "primary")

    rules = get_rules_config(rules_version)
    page = fixtures.get_page_feature_snapshot(url, role)
    persona_sample = input_snapshot.get("persona_sample")

    literacy_completion_mult = _digital_literacy_weight(persona_sample, rules.digital_literacy_multiplier)
    literacy_duration_mult = _digital_literacy_weight(
        persona_sample, rules.digital_literacy_duration_multiplier
    )

    # --- Gorev tamamlama olasiligi ---
    completion_point = rules.base_completion_probability
    completion_point -= rules.completion_penalty_per_nav_step * page.nav_depth
    completion_point -= rules.completion_penalty_per_form_field * page.form_field_count
    if not page.above_fold_cta:
        completion_point -= rules.completion_penalty_missing_above_fold_cta
    completion_point = _clamp(completion_point * literacy_completion_mult, 0.01, 0.99)
    task_completion_probability = _uncertainty_interval(
        completion_point, rules.probability_uncertainty_half_width_ratio, 0.0, 1.0
    )

    # --- Gorev suresi (saniye) dagilimi ---
    duration_point = rules.base_duration_seconds
    duration_point += rules.duration_seconds_per_nav_step * page.nav_depth
    duration_point += rules.duration_seconds_per_form_field * page.form_field_count
    duration_point += rules.duration_seconds_per_100_words * (page.page_word_count / 100.0)
    duration_point *= literacy_duration_mult
    duration_point = max(3.0, duration_point)

    spread = rules.duration_relative_spread
    duration_low = duration_point * (1 - spread)
    duration_high = duration_point * (1 + spread)
    task_duration_seconds = {
        "unit": "seconds",
        "distribution": "triangular",
        "point_estimate": round(duration_point, 1),
        "low": round(duration_low, 1),
        "mode": round(duration_point, 1),
        "high": round(duration_high, 1),
        "p10": round(_triangular_quantile(duration_low, duration_point, duration_high, 0.10), 1),
        "p50": round(_triangular_quantile(duration_low, duration_point, duration_high, 0.50), 1),
        "p90": round(_triangular_quantile(duration_low, duration_point, duration_high, 0.90), 1),
    }

    # --- Yanlis tiklama olasiligi ---
    misclick_point = rules.base_misclick_probability
    misclick_point += rules.misclick_increase_per_extra_cta * max(0, page.primary_cta_count - 1)
    if page.avg_contrast_ratio < fixtures.WCAG_AA_CONTRAST_THRESHOLD:
        misclick_point += rules.misclick_increase_low_contrast
    misclick_point = _clamp(misclick_point, 0.0, 0.95)
    misclick_probability = _uncertainty_interval(
        misclick_point, rules.probability_uncertainty_half_width_ratio, 0.0, 1.0
    )

    # --- Terk (abandonment) olasiligi ---
    abandonment_point = rules.base_abandonment_probability
    abandonment_point += rules.abandonment_increase_per_nav_step * page.nav_depth
    abandonment_point += rules.abandonment_increase_per_form_field * page.form_field_count
    if not page.mobile_friendly:
        abandonment_point += rules.abandonment_increase_not_mobile_friendly
    abandonment_point = _clamp(abandonment_point, 0.0, 0.95)
    abandonment_probability = _uncertainty_interval(
        abandonment_point, rules.probability_uncertainty_half_width_ratio, 0.0, 1.0
    )

    # --- Okunabilirlik skoru (0-100) ---
    readability_point = rules.readability_base_score
    readability_point -= rules.readability_penalty_per_word_over_400 * max(0, page.page_word_count - 400)
    readability_point -= rules.readability_penalty_per_sentence_word_over_18 * max(
        0.0, page.avg_sentence_word_count - 18
    )
    readability_point += min(
        rules.readability_max_heading_bonus, rules.readability_bonus_per_heading * page.heading_count
    )
    readability_score = round(_clamp(readability_point, 0.0, 100.0), 1)

    # --- Kontrast kontrolu (WCAG AA yaklasik esigi) ---
    contrast_check = {
        "pass": page.avg_contrast_ratio >= fixtures.WCAG_AA_CONTRAST_THRESHOLD,
        "avg_ratio": page.avg_contrast_ratio,
        "min_ratio": page.min_contrast_ratio,
        "threshold": fixtures.WCAG_AA_CONTRAST_THRESHOLD,
    }

    regional_interest = _regional_interest_from_persona_sample(persona_sample)

    result = {
        "engine_version": BASELINE_ENGINE_VERSION,
        "rules_version": rules.version,
        "fixture_version": page.fixture_version,
        "generator_version": (persona_sample or {}).get("generator_version"),
        "calibration_status": "uncalibrated",
        "deterministic_seed": deterministic_seed,
        "input_snapshot_hash": _hash_input_snapshot(input_snapshot, deterministic_seed, rules.version),
        "variant_role": role,
        "url": url,
        "page_feature_snapshot": asdict(page),
        "metrics": {
            "task_completion_probability": task_completion_probability,
            "task_duration_seconds": task_duration_seconds,
            "misclick_probability": misclick_probability,
            "abandonment_probability": abandonment_probability,
            "readability_score": readability_score,
            "contrast_check": contrast_check,
            "regional_interest": regional_interest,
        },
        "disclaimer": RESULT_DISCLAIMER,
        "methodology_reference": "docs/methodology.md",
    }
    assert_no_banned_claims(json.dumps(result, default=str))
    return result


@dataclass(frozen=True)
class MetricComparison:
    key: str
    variant_a: float
    variant_b: float
    delta: float
    uncertainty_note: str


def compare_baseline_results(
    result_a: dict, result_b: dict, *, persona_count_a: int, persona_count_b: int
) -> dict:
    """Iki varyant sonucunu karsilastirir: mutlak deger + fark + belirsizlik.

    Istatistiksel anlamlilik iddia edilmez; yalnizca "simulasyon farki"
    olarak sunulur (bkz. docs/scientific-integrity.md).
    """

    def _point(metrics: dict, key: str) -> float:
        metric = metrics[key]
        return metric["point_estimate"] if isinstance(metric, dict) else metric

    scalar_keys = (
        "task_completion_probability",
        "task_duration_seconds",
        "misclick_probability",
        "abandonment_probability",
    )

    comparisons = {}
    for key in scalar_keys:
        a = _point(result_a["metrics"], key)
        b = _point(result_b["metrics"], key)
        comparisons[key] = {
            "variant_a": a,
            "variant_b": b,
            "delta": round(b - a, 4),
        }

    a_read = result_a["metrics"]["readability_score"]
    b_read = result_b["metrics"]["readability_score"]
    comparisons["readability_score"] = {
        "variant_a": a_read,
        "variant_b": b_read,
        "delta": round(b_read - a_read, 4),
    }

    return {
        "comparisons": comparisons,
        "sampled_synthetic_persona_count": {
            "variant_a": persona_count_a,
            "variant_b": persona_count_b,
        },
        "calibration_status": "uncalibrated",
        "note": COMPARISON_NOTE,
    }


__all__ = [
    "BASELINE_ENGINE_VERSION",
    "BANNED_CLAIM_PHRASES",
    "RESULT_DISCLAIMER",
    "COMPARISON_NOTE",
    "SimulationInputError",
    "assert_no_banned_claims",
    "run_baseline_simulation",
    "compare_baseline_results",
]
