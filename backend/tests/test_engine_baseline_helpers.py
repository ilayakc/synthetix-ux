"""`app.engine.baseline` icindeki yardimci fonksiyonlarin kenar durumlari.

Bu dosya, `run_baseline_simulation`in uctan uca akisini test.simulation_engine.py'ye
birakip, motorun ic sarmal (dijital yatkinlik agirligi, bolgesel ilgi) ve
matematik (ucgen dagilim) yardimcilarinin tum dallarini dogrudan cagirarak
kanitlar - bu yardimcilar `input_snapshot` icindeki opsiyonel/eksik alanlarla
tetiklenir ve uctan uca fixture'larla tesadufen hicbir zaman gorulmeyebilir.
"""

import pytest

from app.engine import baseline

pytestmark = pytest.mark.unit


# --- _triangular_quantile -----------------------------------------------------


def test_triangular_quantile_returns_low_when_high_equals_low():
    assert baseline._triangular_quantile(0.5, 0.5, 0.5, 0.5) == 0.5


def test_triangular_quantile_normal_range_is_between_low_and_high():
    value = baseline._triangular_quantile(0.0, 0.5, 1.0, 0.9)
    assert 0.0 <= value <= 1.0


# --- _digital_literacy_weight --------------------------------------------------


def test_digital_literacy_weight_defaults_to_neutral_without_sample():
    assert baseline._digital_literacy_weight(None, {"high": 1.2}) == 1.0


def test_digital_literacy_weight_defaults_to_neutral_when_total_is_zero():
    sample = {"segments": [{"count": 0, "dimension_values": {}}]}
    assert baseline._digital_literacy_weight(sample, {"high": 1.2}) == 1.0


def test_digital_literacy_weight_defaults_to_neutral_when_no_segment_matches_dimension():
    sample = {"segments": [{"count": 10, "dimension_values": {}}]}
    assert baseline._digital_literacy_weight(sample, {"high": 1.2}) == 1.0


def test_digital_literacy_weight_blends_matched_and_unmatched_segments():
    sample = {
        "segments": [
            {"count": 50, "dimension_values": {"digital_literacy": "high"}},
            {"count": 50, "dimension_values": {}},
        ]
    }
    # matched (agirlik 1.2, pay 0.5) + unmatched (notr 1.0, pay 0.5) = 1.1
    assert baseline._digital_literacy_weight(sample, {"high": 1.2}) == pytest.approx(1.1)


# --- _regional_interest_from_persona_sample ------------------------------------


def test_regional_interest_empty_without_sample():
    assert baseline._regional_interest_from_persona_sample(None) == []


def test_regional_interest_empty_without_region_dimension():
    sample = {"distribution_snapshot": {"distribution": {}}, "segments": []}
    assert baseline._regional_interest_from_persona_sample(sample) == []


def test_regional_interest_skips_segments_without_region_value():
    sample = {
        "distribution_snapshot": {
            "distribution": {"region": [{"key": "tr", "label": "Turkiye", "scenario_interest": {}}]}
        },
        "segments": [{"count": 10, "dimension_values": {}}],
    }
    assert baseline._regional_interest_from_persona_sample(sample) == []


def test_regional_interest_skips_region_without_matching_bucket():
    sample = {
        "distribution_snapshot": {
            "distribution": {"region": [{"key": "tr", "label": "Turkiye", "scenario_interest": {}}]}
        },
        "segments": [{"count": 10, "dimension_values": {"region": "unknown-region"}}],
    }
    assert baseline._regional_interest_from_persona_sample(sample) == []


def test_regional_interest_skips_bucket_without_scenario_interest():
    sample = {
        "distribution_snapshot": {"distribution": {"region": [{"key": "tr", "label": "Turkiye"}]}},
        "segments": [{"count": 10, "dimension_values": {"region": "tr"}}],
    }
    assert baseline._regional_interest_from_persona_sample(sample) == []


def test_regional_interest_returns_entry_when_scenario_interest_present():
    sample = {
        "distribution_snapshot": {
            "distribution": {
                "region": [
                    {
                        "key": "tr",
                        "label": "Turkiye",
                        "scenario_interest": {
                            "estimate": "medium",
                            "confidence": "low",
                            "assumption": "varsayim",
                            "disclaimer": "tahmindir",
                        },
                    }
                ]
            }
        },
        "segments": [{"count": 10, "dimension_values": {"region": "tr"}}],
    }
    result = baseline._regional_interest_from_persona_sample(sample)
    assert len(result) == 1
    assert result[0]["region_key"] == "tr"
    assert result[0]["share"] == 1.0
