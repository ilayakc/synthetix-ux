"""Faz 3C.2B1: authoritative context snapshot + sanitization + Stage 3/6 hash
etkisi testleri (CONTEXT 32-45, REGRESSION hash).
"""

import pytest

from app.services.ai_pipeline import stage_sources
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.schemas import (
    AggregationResult,
    CompletionBucketStat,
    CompletionDistribution,
    FrictionHypothesis,
    ScenarioInterpretation,
    TaskStep,
)

pytestmark = pytest.mark.unit

_METRICS: dict[str, object] = {
    "task_completion_probability": {"point_estimate": 0.7},
    "abandonment_probability": {"point_estimate": 0.2},
}


def _evidence():
    return prepare_page_evidence(source_type="sim", metrics=dict(_METRICS))


def _scenario():
    return ScenarioInterpretation(
        steps=(
            TaskStep(
                step_id="step:1",
                instruction="a",
                success_criterion="a",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        success_criteria=("a",),
        friction_hypotheses=(
            FrictionHypothesis(
                hypothesis_id="hyp:1",
                description="desc",
                evidence_references=("metric:task_completion_probability",),
            ),
        ),
        limitations="ok",
    )


def _aggregation():
    dist = CompletionDistribution(
        high=CompletionBucketStat(users=50, share=0.5),
        medium=CompletionBucketStat(users=30, share=0.3),
        low=CompletionBucketStat(users=20, share=0.2),
    )
    return AggregationResult(
        total_population=100,
        completion_distribution=dist,
        common_issues=(),
        overall_confidence=0.5,
        disclaimer="d" * 10,
        aggregation_hash="0" * 64,
    )


# --- sanitize_context_text -------------------------------------------------


def test_sanitize_strips_html_and_control_chars():
    out = stage_sources.sanitize_context_text("<b>Sepete\x00 ekle</b>\tsimdi", max_len=500)
    assert "<" not in out and ">" not in out
    assert "\x00" not in out and "\t" not in out
    assert "Sepete" in out and "ekle" in out


def test_sanitize_enforces_max_length():
    out = stage_sources.sanitize_context_text("x" * 1000, max_len=200)
    assert len(out) == 200


def test_sanitize_rejects_non_string_types():
    assert stage_sources.sanitize_context_text({"a": 1}, max_len=500) is None
    assert stage_sources.sanitize_context_text(["a"], max_len=500) is None
    assert stage_sources.sanitize_context_text(123, max_len=500) is None
    assert stage_sources.sanitize_context_text(None, max_len=500) is None


def test_sanitize_empty_after_cleaning_returns_none():
    assert stage_sources.sanitize_context_text("   <br>  ", max_len=500) is None


# --- build_task_context_source: yeni vs eski snapshot ----------------------


def test_new_snapshot_uses_authoritative_fields():
    snap = {
        "target_task": "Sepete urun ekle",
        "test_name": "Odeme akisi testi",
        "test_description": None,  # gercek kaynak yok -> deterministik fallback
        "methodology_context": stage_sources.METHODOLOGY_CONTEXT_V1,
        # eski proxy alanlar da dursa bile yeni yolda KULLANILMAZ:
        "wizard_test_type": "ab_comparison",
        "target_audience": "Genel kullanicilar",
    }
    ctx = stage_sources.build_task_context_source(input_snapshot=snap)
    assert ctx.target_task == "Sepete urun ekle"
    assert ctx.test_name == "Odeme akisi testi"
    assert ctx.methodology_context == stage_sources.METHODOLOGY_CONTEXT_V1
    # test_description gercek kaynak olmadigi icin fallback'e duser
    assert ctx.test_description == stage_sources.DEFAULT_TEST_DESCRIPTION


def test_target_audience_is_not_used_as_test_description_in_new_snapshot():
    snap = {
        "methodology_context": stage_sources.METHODOLOGY_CONTEXT_V1,
        "target_audience": "B2B karar vericileri",
        "test_description": None,
    }
    ctx = stage_sources.build_task_context_source(input_snapshot=snap)
    assert ctx.test_description != "B2B karar vericileri"
    assert ctx.test_description == stage_sources.DEFAULT_TEST_DESCRIPTION


def test_legacy_snapshot_preserves_prior_derivation():
    """Eski run (methodology_context YOK): onceki turetme (test_name<-wizard_
    test_type, test_description<-target_audience) AYNEN korunur - hash stabil."""

    snap = {
        "wizard_test_type": "existing_site_basic_ux",
        "target_audience": "Genel kullanicilar",
    }
    ctx = stage_sources.build_task_context_source(input_snapshot=snap)
    assert ctx.test_name == "existing_site_basic_ux"
    assert ctx.test_description == "Genel kullanicilar"
    assert ctx.methodology_context == stage_sources.DEFAULT_METHODOLOGY_CONTEXT


def test_same_snapshot_yields_same_context_and_hash():
    snap = {
        "target_task": "Sepete ekle",
        "test_name": "T",
        "methodology_context": stage_sources.METHODOLOGY_CONTEXT_V1,
    }
    a = stage_sources.build_task_context_source(input_snapshot=snap)
    b = stage_sources.build_task_context_source(input_snapshot=dict(snap))
    ev = _evidence()
    assert stage_sources.scenario_stage_input_hash(
        evidence=ev, task_context=a
    ) == stage_sources.scenario_stage_input_hash(evidence=ev, task_context=b)


# --- Stage 3/6 hash duyarliligi --------------------------------------------


def test_different_target_task_changes_stage3_hash():
    ev = _evidence()
    base = {"methodology_context": stage_sources.METHODOLOGY_CONTEXT_V1, "test_name": "T"}
    ctx1 = stage_sources.build_task_context_source(input_snapshot={**base, "target_task": "Gorev A"})
    ctx2 = stage_sources.build_task_context_source(input_snapshot={**base, "target_task": "Gorev B"})
    h1 = stage_sources.scenario_stage_input_hash(evidence=ev, task_context=ctx1)
    h2 = stage_sources.scenario_stage_input_hash(evidence=ev, task_context=ctx2)
    assert h1 != h2


def test_different_methodology_context_changes_stage6_hash():
    ev = _evidence()
    agg = _aggregation()
    ctx1 = stage_sources.build_task_context_source(
        input_snapshot={"methodology_context": "Metodoloji A", "target_task": "g"}
    )
    ctx2 = stage_sources.build_task_context_source(
        input_snapshot={"methodology_context": "Metodoloji B", "target_task": "g"}
    )
    h1 = stage_sources.ux_report_stage_input_hash(
        evidence=ev,
        baseline_metrics=(),
        aggregation=agg,
        module_summary=(),
        task_context=ctx1,
    )
    h2 = stage_sources.ux_report_stage_input_hash(
        evidence=ev,
        baseline_metrics=(),
        aggregation=agg,
        module_summary=(),
        task_context=ctx2,
    )
    assert h1 != h2
