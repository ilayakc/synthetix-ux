"""Gelismis analiz modulleri icin deterministik, versiyonlu sentetik motorlar.

Bu dosya `app.engine.baseline` ile ayni tasarim ilkelerini paylasir (bkz. o
dosyanin dokstring'i ve docs/methodology.md): hicbir nokta tahmini rastgele
sayidan turetilmez, tum agirliklar `app.engine.rules_config` icinde
surumludur, ayni girdi her zaman ayni sonucu uretir. Kucuk ucgen-belirsizlik
yardimcilari (`_clamp`, `_uncertainty_interval`) burada `baseline.py`'den
IMPORT EDILMEZ; bu dosya kasitli olarak bagimsizdir cunku `baseline.py`
`scripts/check_critical_coverage.py` tarafindan %100 branch coverage
zorunlulugu tasiyan "kritik" dosyalardan biridir ve degistirilmemesi
istenmistir.

Iki fonksiyon burada tanimlanir:

- `run_campaign_cta_analysis`: kampanya sayfalarindaki CTA'lar icin sentetik
  tiklama olasiligi tahmini + mesaj netligi bulgulari (SYNTHETIC_ESTIMATE).
- `run_synthetic_attention_estimate`: sayfa duzeninden turetilen sentetik bir
  "dikkat" agirlik dagilimi (SYNTHETIC_ESTIMATE). Bu KESINLIKLE gercek goz
  izleme (eye-tracking) verisi degildir; yalnizca sayfa yapisi/gorsel
  belirginlik/yerlesim ozelliklerinden uretilmis bir heuristiktir.

Her iki fonksiyon da `app.engine.fixtures.get_page_feature_snapshot`
uzerinden calisir (analyzer'a gitmez) - bu, `network_device_test` modulunun
aksine (bkz. app.services.device_network_analysis, gercek analyzer olcumu),
bu iki modulun neden `measurement_type=SYNTHETIC_ESTIMATE` oldugunun
gerekcesidir (bkz. app.services.module_catalog).
"""

from __future__ import annotations

import hashlib
import json

from app.engine import fixtures
from app.engine.rules_config import HeuristicRulesConfig, get_rules_config

ADVANCED_MODULES_ENGINE_VERSION = "advanced-modules-2026.1"

CAMPAIGN_CTA_DISCLAIMER = (
    "Bu, CTA bazli tiklama olasiligi icin kalibre edilmemis bir sentetik senaryo "
    "tahminidir; gercek tiklama/etkilesim verisi degildir. Erken asama fikir "
    "uretimi icin kullanilabilir, gercek kullanilabilirlik testinin yerini almaz."
)

SYNTHETIC_ATTENTION_DISCLAIMER = (
    "Bu, sayfa yapisi/gorsel belirginlik/yerlesim ozelliklerinden turetilmis "
    "kalibre edilmemis bir SENTETIK dikkat tahminidir; GERCEK GOZ IZLEME "
    "(eye-tracking) verisi veya gercek kullanici davranisi DEGILDIR."
)

# Bilimsel durustluk: bu motorun urettigi hicbir metinde gecmesi yasak olan
# ifadeler (bkz. app.engine.baseline.BANNED_CLAIM_PHRASES - ayni liste burada
# da bagimsiz olarak tutulur, cunku baseline.py'ye bagimlilik eklenmez).
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


class ModuleInputError(ValueError):
    """input_snapshot bu modul icin gecersiz/eksik oldugunda firlatilir."""


def assert_no_banned_claims(text: str) -> None:
    normalized = text.strip().lower()
    for phrase in BANNED_CLAIM_PHRASES:
        if phrase in normalized:
            raise ModuleInputError(
                f"Uretilen metin yasakli bir iddia iceriyor: '{phrase}' "
                "(bkz. docs/scientific-integrity.md)"
            )


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


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


def _seeded_unit_value(seed_material: str, index: int) -> float:
    """Verilen materyal + index'ten [0, 1) araliginda deterministik bir deger uretir."""

    digest = hashlib.sha256(f"{seed_material}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _require_url(input_snapshot: dict) -> tuple[str, str]:
    url = input_snapshot.get("url")
    if not url or not isinstance(url, str):
        raise ModuleInputError("input_snapshot.url gereklidir")
    role = input_snapshot.get("role", "primary")
    return url, role


def run_campaign_cta_analysis(
    input_snapshot: dict,
    deterministic_seed: int,
    rules_version: str | None = None,
) -> dict:
    """Sayfadaki birincil CTA'lar icin sentetik tiklama olasiligi tahmini uretir.

    CTA adaylari, `page_feature_snapshot.primary_cta_count` kadar, url+role'den
    sha256 ile turetilen deterministik siralamayla sentezlenir (gercek DOM'dan
    okunmaz - bkz. modul dokstring'i).
    """

    url, role = _require_url(input_snapshot)
    rules: HeuristicRulesConfig = get_rules_config(rules_version)
    page = fixtures.get_page_feature_snapshot(url, role)

    seed_material = f"{page.input_hash}:cta"
    ctas: list[dict] = []
    for index in range(page.primary_cta_count):
        # Ilk CTA, sayfanin kendi `above_fold_cta` bayragini tasir; sonraki
        # adaylar icin (varsa) deterministik bir seed'li deger kullanilir.
        if index == 0:
            above_fold = page.above_fold_cta
        else:
            above_fold = _seeded_unit_value(seed_material, index) > 0.55

        click_point = rules.cta_base_click_probability
        click_point -= rules.cta_rank_penalty * index
        click_point += rules.cta_above_fold_bonus if above_fold else -rules.cta_below_fold_penalty
        if page.avg_contrast_ratio < fixtures.WCAG_AA_CONTRAST_THRESHOLD:
            click_point -= rules.cta_low_contrast_penalty
        click_point = _clamp(click_point, 0.01, 0.95)

        ctas.append(
            {
                "key": f"cta_{index + 1}",
                "label": f"CTA {index + 1}",
                "rank": index + 1,
                "above_fold": above_fold,
                "click_probability": _uncertainty_interval(
                    click_point, rules.probability_uncertainty_half_width_ratio, 0.0, 1.0
                ),
            }
        )

    findings: list[dict] = []
    if page.primary_cta_count > rules.cta_too_many_ctas_threshold:
        findings.append(
            {
                "key": "too_many_ctas",
                "severity": "warning",
                "text": (
                    f"Sayfada {page.primary_cta_count} birincil CTA var (esik: "
                    f"{rules.cta_too_many_ctas_threshold}); bu, mesaj netligini azaltabilir."
                ),
            }
        )
    if not page.above_fold_cta:
        findings.append(
            {
                "key": "cta_not_above_fold",
                "severity": "warning",
                "text": "Birincil CTA ilk ekranda (above the fold) degil.",
            }
        )
    if page.avg_contrast_ratio < fixtures.WCAG_AA_CONTRAST_THRESHOLD:
        findings.append(
            {
                "key": "low_contrast",
                "severity": "warning",
                "text": (
                    f"Ortalama kontrast orani ({page.avg_contrast_ratio}) WCAG AA esiginin "
                    f"({fixtures.WCAG_AA_CONTRAST_THRESHOLD}) altinda; CTA gorunurlugunu azaltabilir."
                ),
            }
        )
    if not findings:
        findings.append(
            {
                "key": "no_threshold_triggered",
                "severity": "info",
                "text": "Tanimli esik tabanli bir mesaj netligi bulgusu tetiklenmedi.",
            }
        )

    result = {
        "module_key": "campaign_cta_test",
        "engine_version": ADVANCED_MODULES_ENGINE_VERSION,
        "rules_version": rules.version,
        "fixture_version": page.fixture_version,
        "calibration_status": "uncalibrated",
        "deterministic_seed": deterministic_seed,
        "variant_role": role,
        "url": url,
        "ctas": ctas,
        "message_clarity_findings": findings,
        "disclaimer": CAMPAIGN_CTA_DISCLAIMER,
        "methodology_reference": "docs/methodology.md",
    }
    assert_no_banned_claims(json.dumps(result, default=str))
    return result


_ATTENTION_REGIONS: tuple[tuple[str, str], ...] = (
    ("ust_navigasyon", "Üst navigasyon"),
    ("hero_baslik", "Hero / birincil başlık"),
    ("birincil_cta", "Birincil CTA alanı"),
    ("govde_metni", "Gövde metni"),
    ("alt_bilgi", "Alt bilgi (footer)"),
)


def run_synthetic_attention_estimate(
    input_snapshot: dict,
    deterministic_seed: int,
    rules_version: str | None = None,
) -> dict:
    """Sayfa duzeninden turetilen sentetik bir dikkat/gorsel aginlik agirlik dagilimi uretir.

    GERCEK goz izleme (eye-tracking) verisi DEGILDIR - bkz. `SYNTHETIC_ATTENTION_DISCLAIMER`.
    """

    url, role = _require_url(input_snapshot)
    rules: HeuristicRulesConfig = get_rules_config(rules_version)
    page = fixtures.get_page_feature_snapshot(url, role)

    heading_bonus = min(
        rules.attention_heading_bonus_max, rules.attention_heading_bonus_per_heading * page.heading_count
    )
    body_bonus = min(
        rules.attention_body_bonus_max,
        rules.attention_body_bonus_per_100_words * (page.page_word_count / 100.0),
    )
    fold_bonus = rules.attention_above_fold_bonus if page.above_fold_cta else 0.0

    base_weights = {
        "ust_navigasyon": rules.attention_base_weight_top_nav,
        "hero_baslik": rules.attention_base_weight_hero + heading_bonus + fold_bonus,
        "birincil_cta": rules.attention_base_weight_primary_cta + fold_bonus,
        "govde_metni": rules.attention_base_weight_body + body_bonus,
        "alt_bilgi": rules.attention_base_weight_footer,
    }

    seed_material = f"{page.input_hash}:attention"
    jittered_weights: dict[str, float] = {}
    for index, (key, _label) in enumerate(_ATTENTION_REGIONS):
        base = base_weights[key]
        jitter = (_seeded_unit_value(seed_material, index) - 0.5) * 2 * rules.attention_jitter_ratio
        jittered_weights[key] = max(0.01, base * (1 + jitter))

    total = sum(jittered_weights.values())
    regions: list[dict] = []
    grid: list[dict] = []
    for key, label in _ATTENTION_REGIONS:
        share = round(jittered_weights[key] / total, 4)
        regions.append({"key": key, "label": label, "attention_share": share})
        grid.append({"key": key, "label": label, "score": share})

    result = {
        "module_key": "synthetic_attention_estimate",
        "engine_version": ADVANCED_MODULES_ENGINE_VERSION,
        "rules_version": rules.version,
        "fixture_version": page.fixture_version,
        "calibration_status": "uncalibrated",
        "deterministic_seed": deterministic_seed,
        "variant_role": role,
        "url": url,
        "regions": regions,
        "grid": grid,
        "disclaimer": SYNTHETIC_ATTENTION_DISCLAIMER,
        "methodology_reference": "docs/methodology.md",
    }
    assert_no_banned_claims(json.dumps(result, default=str))
    return result


__all__ = [
    "ADVANCED_MODULES_ENGINE_VERSION",
    "CAMPAIGN_CTA_DISCLAIMER",
    "SYNTHETIC_ATTENTION_DISCLAIMER",
    "BANNED_CLAIM_PHRASES",
    "ModuleInputError",
    "assert_no_banned_claims",
    "run_campaign_cta_analysis",
    "run_synthetic_attention_estimate",
]
