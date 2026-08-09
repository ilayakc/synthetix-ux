"""Gelismis modul motorlari (kampanya CTA + sentetik dikkat tahmini) icin
determinism, belirsizlik araligi ve yasakli iddia testleri.

`app.engine.baseline`'in test desenine (bkz. test_simulation_engine.py)
paraleldir; bu iki modul de `app.engine.fixtures` uzerinden calisir, gercek
bir analyzer cagrisi yapmaz (bkz. docs/methodology.md "Gelismis moduller").
"""

import pytest

from app.engine import advanced_modules
from app.engine import fixtures as engine_fixtures

pytestmark = pytest.mark.unit


def _snapshot(*, url: str = "https://example.com/kampanya", role: str = "primary") -> dict:
    return {"url": url, "role": role}


# --- Kampanya CTA analizi -----------------------------------------------------


def test_cta_same_input_and_seed_produce_identical_result():
    snapshot = _snapshot()
    result_1 = advanced_modules.run_campaign_cta_analysis(snapshot, deterministic_seed=1)
    result_2 = advanced_modules.run_campaign_cta_analysis(snapshot, deterministic_seed=1)
    assert result_1 == result_2


def test_cta_different_seed_does_not_change_point_estimates():
    snapshot = _snapshot()
    result_a = advanced_modules.run_campaign_cta_analysis(snapshot, deterministic_seed=1)
    result_b = advanced_modules.run_campaign_cta_analysis(snapshot, deterministic_seed=999999)
    assert result_a["ctas"] == result_b["ctas"]
    assert result_a["deterministic_seed"] == 1
    assert result_b["deterministic_seed"] == 999999


def test_cta_count_matches_page_fixture_primary_cta_count():
    snapshot = _snapshot()
    page = engine_fixtures.get_page_feature_snapshot(snapshot["url"], snapshot["role"])
    result = advanced_modules.run_campaign_cta_analysis(snapshot, deterministic_seed=1)
    assert len(result["ctas"]) == page.primary_cta_count


def test_cta_click_probabilities_have_sane_uncertainty_intervals():
    result = advanced_modules.run_campaign_cta_analysis(_snapshot(), deterministic_seed=1)
    for cta in result["ctas"]:
        metric = cta["click_probability"]
        assert 0.0 <= metric["low"] <= metric["point_estimate"] <= metric["high"] <= 1.0
        assert metric["distribution"] == "triangular"


def test_cta_missing_url_raises_module_input_error():
    with pytest.raises(advanced_modules.ModuleInputError):
        advanced_modules.run_campaign_cta_analysis({"role": "primary"}, deterministic_seed=1)


def test_cta_message_clarity_findings_always_present():
    result = advanced_modules.run_campaign_cta_analysis(_snapshot(), deterministic_seed=1)
    assert len(result["message_clarity_findings"]) >= 1


@pytest.mark.security
def test_cta_disclaimer_frames_result_as_synthetic():
    lowered = advanced_modules.CAMPAIGN_CTA_DISCLAIMER.lower()
    assert "sentetik" in lowered
    assert "gercek" in lowered or "gerçek" in lowered


@pytest.mark.security
def test_cta_result_always_reports_uncalibrated_status():
    result = advanced_modules.run_campaign_cta_analysis(_snapshot(), deterministic_seed=1)
    assert result["calibration_status"] == "uncalibrated"
    assert result["module_key"] == "campaign_cta_test"


# --- Sentetik dikkat tahmini ---------------------------------------------------


def test_attention_same_input_and_seed_produce_identical_result():
    snapshot = _snapshot()
    result_1 = advanced_modules.run_synthetic_attention_estimate(snapshot, deterministic_seed=1)
    result_2 = advanced_modules.run_synthetic_attention_estimate(snapshot, deterministic_seed=1)
    assert result_1 == result_2


def test_attention_regions_sum_to_one():
    result = advanced_modules.run_synthetic_attention_estimate(_snapshot(), deterministic_seed=1)
    total_share = sum(region["attention_share"] for region in result["regions"])
    assert total_share == pytest.approx(1.0, abs=0.01)


def test_attention_grid_matches_regions():
    result = advanced_modules.run_synthetic_attention_estimate(_snapshot(), deterministic_seed=1)
    assert len(result["grid"]) == len(result["regions"])
    grid_keys = {cell["key"] for cell in result["grid"]}
    region_keys = {region["key"] for region in result["regions"]}
    assert grid_keys == region_keys


def test_attention_produces_distinct_normalized_click_interest_grid():
    result = advanced_modules.run_synthetic_attention_estimate(_snapshot(), deterministic_seed=1)
    assert result["gaze_grid"] == result["grid"]
    assert len(result["click_grid"]) == len(result["grid"])
    assert sum(cell["score"] for cell in result["click_grid"]) == pytest.approx(1.0, abs=0.01)
    assert result["click_grid"] != result["grid"]


def test_attention_missing_url_raises_module_input_error():
    with pytest.raises(advanced_modules.ModuleInputError):
        advanced_modules.run_synthetic_attention_estimate({"role": "primary"}, deterministic_seed=1)


@pytest.mark.security
def test_attention_disclaimer_explicitly_denies_real_eye_tracking():
    lowered = advanced_modules.SYNTHETIC_ATTENTION_DISCLAIMER.lower()
    assert "sentetik" in lowered
    assert "goz izleme" in lowered or "eye-tracking" in lowered or "eye tracking" in lowered
    assert "degildir" in lowered or "değildir" in lowered


@pytest.mark.security
@pytest.mark.parametrize(
    "phrase",
    [
        "Bu sonuclar bilimsel olarak kanitlanmistir.",
        "Bu, gercek goz takibi verisiyle olculmustur.",
        "Bu sonuclar eye tracking ile dogrulanmistir.",
    ],
)
def test_assert_no_banned_claims_rejects_forbidden_phrases(phrase):
    with pytest.raises(advanced_modules.ModuleInputError):
        advanced_modules.assert_no_banned_claims(phrase)
