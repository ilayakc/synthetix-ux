"""Heuristic baseline motorunun surumlu kural agirliklari.

`app.services.pricing` ile ayni desen: agirliklar kodun geneline dagitik
sabitler olarak gomulmez, tek bir surumlu yapida (`RULES_VERSIONS`) tutulur.
Bir calistirma, uretildigi anda gecerli olan surum kimligini
`SimulationRun.rules_version` alaninda saklar; boylece daha once uretilmis
bir sonuc, kurallar sonradan degisse bile hangi surume gore hesaplandigini
kaybetmez.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HeuristicRulesConfig:
    version: str

    # --- Gorev tamamlama olasiligi ---
    # Taban tamamlama olasiligi (nav_depth=0, form_field_count=0 varsayimiyla).
    base_completion_probability: float
    # Her ek gezinme adimi (nav_depth) basina olasilik cezasi.
    completion_penalty_per_nav_step: float
    # Her form alani basina olasilik cezasi.
    completion_penalty_per_form_field: float
    # Birincil CTA ilk ekranda degilse uygulanan sabit ceza.
    completion_penalty_missing_above_fold_cta: float
    # Dijital yatkinlik seviyesine gore carpan (segment agirlikli ortalama).
    digital_literacy_multiplier: dict = field(
        default_factory=lambda: {"low": 0.80, "medium": 1.0, "high": 1.12}
    )

    # --- Gorev suresi (saniye) ---
    base_duration_seconds: float = 25.0
    duration_seconds_per_nav_step: float = 9.0
    duration_seconds_per_form_field: float = 6.0
    duration_seconds_per_100_words: float = 4.0
    # Sureyi de etkileyen dijital yatkinlik carpani (dusuk yatkinlik = daha yavas).
    digital_literacy_duration_multiplier: dict = field(
        default_factory=lambda: {"low": 1.35, "medium": 1.0, "high": 0.85}
    )
    # Belirsizlik: nokta tahmininin +-yuzdesi (dagilim genisligini belirler).
    duration_relative_spread: float = 0.35

    # --- Yanlis tiklama olasiligi ---
    base_misclick_probability: float = 0.04
    misclick_increase_per_extra_cta: float = 0.035
    misclick_increase_low_contrast: float = 0.05

    # --- Terk (abandonment) olasiligi ---
    base_abandonment_probability: float = 0.05
    abandonment_increase_per_nav_step: float = 0.02
    abandonment_increase_per_form_field: float = 0.015
    abandonment_increase_not_mobile_friendly: float = 0.06

    # --- Okunabilirlik skoru (0-100, yuksek=daha okunabilir) ---
    readability_base_score: float = 100.0
    readability_penalty_per_word_over_400: float = 0.02
    readability_penalty_per_sentence_word_over_18: float = 1.6
    readability_bonus_per_heading: float = 1.2
    readability_max_heading_bonus: float = 12.0

    # --- Belirsizlik uretimi (tum olasilik metrikleri icin ortak) ---
    # Nokta tahmini etrafinda ucgen (triangular) dagilimla simetrik olmayan
    # bir belirsizlik araligi uretilir; genislik nokta tahminine gore olceklenir.
    probability_uncertainty_half_width_ratio: float = 0.22


RULES_VERSIONS: dict[str, HeuristicRulesConfig] = {
    "2026.1": HeuristicRulesConfig(
        version="2026.1",
        base_completion_probability=0.93,
        completion_penalty_per_nav_step=0.045,
        completion_penalty_per_form_field=0.02,
        completion_penalty_missing_above_fold_cta=0.08,
    ),
}

CURRENT_RULES_VERSION = "2026.1"


def get_rules_config(version: str | None = None) -> HeuristicRulesConfig:
    key = version or CURRENT_RULES_VERSION
    try:
        return RULES_VERSIONS[key]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen kural surumu: {key}") from exc


__all__ = [
    "HeuristicRulesConfig",
    "RULES_VERSIONS",
    "CURRENT_RULES_VERSION",
    "get_rules_config",
]
