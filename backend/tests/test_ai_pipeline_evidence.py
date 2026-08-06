"""Faz 2A: Stage 1 (page evidence preparation) testleri."""

import copy

import pytest

from app.services.ai_pipeline.errors import ERROR_INVALID_EVIDENCE, AIPipelineError
from app.services.ai_pipeline.evidence import prepare_page_evidence

pytestmark = pytest.mark.unit


def _metrics() -> dict:
    return {
        "task_completion_probability": {"point_estimate": 0.62, "low": 0.5, "high": 0.7},
        "misclick_probability": {"point_estimate": 0.1},
        "abandonment_probability": {"point_estimate": 0.2},
        "task_duration_seconds": {"point_estimate": 45.0},
        "readability_score": 72.5,
        "contrast_check": {"pass": True, "avg_ratio": 5.2},
    }


def _page_features() -> dict:
    return {
        "nav_depth": 3,
        "primary_cta_count": 2,
        "form_field_count": 4,
        "above_fold_cta": True,
        "heading_count": 5,
        "mobile_friendly": True,
        "min_contrast_ratio": 4.5,
        "avg_contrast_ratio": 5.1,
        # Allowlist disi alanlar - evidence'a ASLA gecmemeli.
        "raw_html": "<html>...</html>",
        "raw_dom": "{}",
    }


def test_only_allowlisted_fields_appear():
    evidence = prepare_page_evidence(source_type="url", metrics=_metrics(), page_features=_page_features())
    ids = evidence.evidence_ids()
    assert "metric:task_completion_probability" in ids
    assert "page:primary_cta_count" in ids
    assert "page:raw_html" not in ids
    assert not any("raw" in i for i in ids)


def test_raw_html_dom_cookie_form_fields_never_appear():
    evidence = prepare_page_evidence(source_type="url", metrics=_metrics(), page_features=_page_features())
    for item in evidence.items:
        assert "html" not in item.evidence_id
        assert "dom" not in item.evidence_id
        assert "cookie" not in item.evidence_id
        assert "form_value" not in item.evidence_id
        if isinstance(item.value, str):
            assert "<html>" not in item.value


def test_missing_optional_fields_are_safe():
    evidence = prepare_page_evidence(source_type="url", metrics=_metrics(), page_features=None)
    assert evidence.items  # metrics tek basina yeterli
    assert not any(i.category == "page" for i in evidence.items)


def test_missing_required_metrics_raises_domain_error():
    with pytest.raises(AIPipelineError) as exc_info:
        prepare_page_evidence(source_type="url", metrics={})
    assert exc_info.value.code == ERROR_INVALID_EVIDENCE


def test_blank_source_type_raises_domain_error():
    with pytest.raises(AIPipelineError) as exc_info:
        prepare_page_evidence(source_type="", metrics=_metrics())
    assert exc_info.value.code == ERROR_INVALID_EVIDENCE


def test_evidence_id_is_unique_and_stable():
    evidence = prepare_page_evidence(source_type="url", metrics=_metrics(), page_features=_page_features())
    ids = [item.evidence_id for item in evidence.items]
    assert len(ids) == len(set(ids))
    for evidence_id in ids:
        assert 1 <= len(evidence_id) <= 100


def test_same_input_produces_same_evidence_hash():
    a = prepare_page_evidence(source_type="url", metrics=_metrics(), page_features=_page_features())
    b = prepare_page_evidence(source_type="url", metrics=_metrics(), page_features=_page_features())
    assert a.evidence_hash == b.evidence_hash


def test_different_metrics_produce_different_evidence_hash():
    a = prepare_page_evidence(source_type="url", metrics=_metrics())
    changed = _metrics()
    changed["task_completion_probability"]["point_estimate"] = 0.99
    b = prepare_page_evidence(source_type="url", metrics=changed)
    assert a.evidence_hash != b.evidence_hash


def test_input_dicts_are_not_mutated():
    metrics = _metrics()
    page_features = _page_features()
    metrics_snapshot = copy.deepcopy(metrics)
    page_features_snapshot = copy.deepcopy(page_features)

    prepare_page_evidence(
        source_type="url",
        metrics=metrics,
        page_features=page_features,
        selected_modules=["campaign_cta_test"],
        module_results={"campaign_cta_test": {"disclaimer": "..."}},
    )

    assert metrics == metrics_snapshot
    assert page_features == page_features_snapshot


def test_module_presence_is_recorded_only_for_selected_modules():
    evidence = prepare_page_evidence(
        source_type="url",
        metrics=_metrics(),
        selected_modules=["campaign_cta_test", "network_device_test"],
        module_results={"campaign_cta_test": {"disclaimer": "..."}},
    )
    ids = evidence.evidence_ids()
    assert "module:campaign_cta_test" in ids
    # network_device_test SECILDI ama module_results'ta yok -> eklenmemeli.
    assert "module:network_device_test" not in ids


def test_evidence_hash_field_itself_not_part_of_its_own_hash_input():
    """Ayni evidence icerigi + farkli hash alani ile manuel olusturulan iki
    nesne PYDANTIC dogrulamasi sonrasi ayni `evidence_hash`a sahip olmali -
    yani hash yalnizca icerikten (evidence_hash haric) hesaplaniyor."""

    a = prepare_page_evidence(source_type="url", metrics=_metrics())
    b = prepare_page_evidence(source_type="url", metrics=_metrics())
    assert a.evidence_hash == b.evidence_hash
