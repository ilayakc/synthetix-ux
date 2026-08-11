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
from dataclasses import dataclass

from app.engine import fixtures
from app.engine.fixtures import PageFeatureSnapshot
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


@dataclass(frozen=True)
class _SourceIdentity:
    url: str | None
    source_reference: str | None
    role: str


def _require_source_identity(input_snapshot: dict) -> _SourceIdentity:
    """Sayfayi/tasarimi kimliklendiren alanlari dondurur.

    URL-kaynakli varyantlarda gercek `url` dolu, `source_reference` `None`dur.
    DesignAsset-kaynakli (screenshot/AI) varyantlarda gercek bir URL YOKTUR
    (bkz. app.services.test_wizard.launch_draft) - bu durumda `url` `None`,
    `source_reference` (ör. `design-asset:<id>`) dolu olur. Paket 4 Final
    Hardening'den ONCE bu iki alan `url` icinde bir sozde-URL (`design-asset:
    <id>`) olarak BIRLESTIRILIYORDU (workaround); artik acikca AYRI iki alan
    olarak tasinir, hicbir kosulda `url` sahte bir deger almaz. Ikisi de
    yoksa (gecersiz girdi) `ModuleInputError` firlatilir."""

    url = input_snapshot.get("url")
    has_url = isinstance(url, str) and bool(url)
    source_reference = input_snapshot.get("source_reference")
    has_source_reference = isinstance(source_reference, str) and bool(source_reference)
    if not has_url and not has_source_reference:
        raise ModuleInputError("input_snapshot.url veya input_snapshot.source_reference gereklidir")

    return _SourceIdentity(
        url=url if has_url else None,
        source_reference=source_reference if has_source_reference else None,
        role=input_snapshot.get("role", "primary"),
    )


def run_campaign_cta_analysis(
    input_snapshot: dict,
    deterministic_seed: int,
    rules_version: str | None = None,
    page_feature_snapshot: PageFeatureSnapshot | None = None,
    cta_evidence: list[dict] | None = None,
) -> dict:
    """Sayfadaki birincil CTA'lar icin sentetik tiklama olasiligi tahmini uretir.

    Legacy yol (varsayilan): CTA adaylari, `page_feature_snapshot
    .primary_cta_count` kadar, url+role'den sha256 ile turetilen deterministik
    siralamayla sentezlenir (gercek DOM'dan okunmaz).

    Paket 4B - `cta_evidence` verilirse (bkz. app.services.page_analysis_adapter
    .DomAdaptedInput.cta_evidence): CTA konumu/varligi artik URL/seed'den
    UYDURULMAZ, gercek DOM element rolu (`button`/`link`) kanitindan gelir;
    yalnizca tiklama olasiligi hala `rules_config` agirliklarindan hesaplanan
    bir sentetik senaryo tahminidir (konumsal/varlik iddiasi degil).
    """

    identity = _require_source_identity(input_snapshot)
    rules: HeuristicRulesConfig = get_rules_config(rules_version)
    if page_feature_snapshot is not None:
        page = page_feature_snapshot
    elif identity.url:
        page = fixtures.get_page_feature_snapshot(identity.url, identity.role)
    else:
        raise ModuleInputError(
            "page_feature_snapshot, source_reference tabanli (url olmayan) girdilerde zorunludur"
        )

    ctas: list[dict] = []
    if cta_evidence is not None:
        for index, evidence in enumerate(cta_evidence):
            # Ilk CTA, sayfanin kendi `above_fold_cta` bayragini tasir (gercek
            # kanit: layout_regions.birincil_cta above-the-fold viewport'ta
            # hesaplaniyor); sonraki adaylar icin gercek above-fold kaniti
            # YOK - URL/seed'den UYDURULMAZ, acikca bilinmiyor (None) birakilir.
            above_fold = page.above_fold_cta if index == 0 else None

            click_point = rules.cta_base_click_probability
            click_point -= rules.cta_rank_penalty * index
            click_point += rules.cta_above_fold_bonus if above_fold else -rules.cta_below_fold_penalty
            if page.avg_contrast_ratio < fixtures.WCAG_AA_CONTRAST_THRESHOLD:
                click_point -= rules.cta_low_contrast_penalty
            click_point = _clamp(click_point, 0.01, 0.95)

            ctas.append(
                {
                    "key": f"cta_{index + 1}",
                    "label": evidence.get("label") or f"CTA {index + 1}",
                    "rank": index + 1,
                    "above_fold": above_fold,
                    "classification": evidence["classification"],
                    "evidence": evidence["evidence"],
                    "role": evidence["role"],
                    "interaction_kind": evidence.get("interaction_kind"),
                    "geometry": {
                        "x": evidence["x"],
                        "y": evidence["y"],
                        "width": evidence["width"],
                        "height": evidence["height"],
                    },
                    "click_probability": _uncertainty_interval(
                        click_point, rules.probability_uncertainty_half_width_ratio, 0.0, 1.0
                    ),
                }
            )
    else:
        seed_material = f"{page.input_hash}:cta"
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
        "variant_role": identity.role,
        "url": identity.url,
        "source_reference": identity.source_reference,
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
    page_feature_snapshot: PageFeatureSnapshot | None = None,
) -> dict:
    """Sayfa duzeninden turetilen sentetik bir dikkat/gorsel aginlik agirlik dagilimi uretir.

    GERCEK goz izleme (eye-tracking) verisi DEGILDIR - bkz. `SYNTHETIC_ATTENTION_DISCLAIMER`.

    `page_feature_snapshot` verilirse (Paket 4B, bkz. app.services
    .page_analysis_adapter) agirliklar gercek DOM'dan turetilen skaler
    ozelliklerden (heading_count/page_word_count/above_fold_cta) hesaplanir;
    verilmezse eskisi gibi `fixtures.get_page_feature_snapshot` kullanilir.
    """

    identity = _require_source_identity(input_snapshot)
    rules: HeuristicRulesConfig = get_rules_config(rules_version)
    if page_feature_snapshot is not None:
        page = page_feature_snapshot
    elif identity.url:
        page = fixtures.get_page_feature_snapshot(identity.url, identity.role)
    else:
        raise ModuleInputError(
            "page_feature_snapshot, source_reference tabanli (url olmayan) girdilerde zorunludur"
        )

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

    # Ayri bir sentetik tiklama-ilgisi katmani uretilir. Bu katman gercek
    # tiklama kaydi degildir; etkileşimli alan, ilk ekran ve CTA sayisi gibi
    # sayfa yapisi ozelliklerini agirliklandiran deterministik bir heuristiktir.
    click_base_weights = {
        "ust_navigasyon": 1.0 + min(page.primary_cta_count, 5) * 0.04,
        "hero_baslik": 0.65 + heading_bonus,
        "birincil_cta": 2.25 + fold_bonus + min(page.primary_cta_count, 5) * 0.08,
        "govde_metni": 0.55 + min(body_bonus, 0.25),
        "alt_bilgi": 0.25,
    }
    click_seed_material = f"{page.input_hash}:click-attention"
    click_weights: dict[str, float] = {}
    for index, (key, _label) in enumerate(_ATTENTION_REGIONS):
        base = click_base_weights[key]
        jitter = (
            (_seeded_unit_value(click_seed_material, index) - 0.5)
            * 2
            * rules.attention_jitter_ratio
        )
        click_weights[key] = max(0.01, base * (1 + jitter))
    click_total = sum(click_weights.values())
    click_grid = [
        {
            "key": key,
            "label": label,
            "score": round(click_weights[key] / click_total, 4),
        }
        for key, label in _ATTENTION_REGIONS
    ]

    result = {
        "module_key": "synthetic_attention_estimate",
        "engine_version": ADVANCED_MODULES_ENGINE_VERSION,
        "rules_version": rules.version,
        "fixture_version": page.fixture_version,
        "calibration_status": "uncalibrated",
        "deterministic_seed": deterministic_seed,
        "variant_role": identity.role,
        "url": identity.url,
        "source_reference": identity.source_reference,
        "regions": regions,
        "grid": grid,
        "gaze_grid": grid,
        "click_grid": click_grid,
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
