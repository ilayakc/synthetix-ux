"""Faz 2A: Stage 5 (deterministik aggregation) testleri - LLM/provider cagrisi YOK."""

import random
import uuid

import pytest

from app.services.ai_pipeline.aggregation import aggregate_persona_behavior
from app.services.ai_pipeline.errors import (
    ERROR_INVALID_HYPOTHESIS_REFERENCE,
    ERROR_PERSONA_RESULT_MISMATCH,
    ERROR_PERSONAS_REQUIRED,
    AIPipelineError,
)
from app.services.ai_pipeline.schemas import (
    FrictionHypothesis,
    LikelyCompletion,
    PersonaBehaviorEstimate,
    PersonaContext,
    ScenarioInterpretation,
    TaskStep,
)

pytestmark = pytest.mark.unit


def _scenario(hypothesis_ids: tuple[str, ...] = ("h1", "h2")) -> ScenarioInterpretation:
    return ScenarioInterpretation(
        steps=(
            TaskStep(
                step_id="s1",
                instruction="a",
                success_criterion="a",
                evidence_references=("metric:x",),
            ),
        ),
        success_criteria=("a",),
        friction_hypotheses=tuple(
            FrictionHypothesis(
                hypothesis_id=hid, description=f"desc-{hid}", evidence_references=("metric:x",)
            )
            for hid in hypothesis_ids
        ),
        limitations="ok",
    )


def _personas(weights: list[int]) -> list[PersonaContext]:
    return [
        PersonaContext(persona_id=uuid.uuid4(), index=i, label=f"P{i}", attributes={}, population_weight=w)
        for i, w in enumerate(weights)
    ]


def _result(
    index: int,
    completion: LikelyCompletion,
    confidence: float,
    hypotheses: tuple[str, ...] = (),
) -> PersonaBehaviorEstimate:
    return PersonaBehaviorEstimate(
        persona_index=index,
        likely_completion=completion,
        affected_hypothesis_ids=hypotheses,
        confidence=confidence,
        evidence_references=("metric:x",),
    )


def test_weighted_completion_bucket_user_counts_are_correct():
    personas = _personas([100, 200, 300, 400])
    results = [
        _result(0, LikelyCompletion.HIGH, 0.9),
        _result(1, LikelyCompletion.HIGH, 0.8),
        _result(2, LikelyCompletion.MEDIUM, 0.5),
        _result(3, LikelyCompletion.LOW, 0.2),
    ]
    agg = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))

    assert agg.total_population == 1000
    assert agg.completion_distribution.high.users == 300
    assert agg.completion_distribution.medium.users == 300
    assert agg.completion_distribution.low.users == 400
    assert agg.completion_distribution.high.share == 0.3
    assert agg.completion_distribution.medium.share == 0.3
    assert agg.completion_distribution.low.share == 0.4


def test_share_totals_sum_to_one_within_tolerance():
    personas = _personas([333, 333, 334])
    results = [
        _result(0, LikelyCompletion.HIGH, 0.5),
        _result(1, LikelyCompletion.MEDIUM, 0.5),
        _result(2, LikelyCompletion.LOW, 0.5),
    ]
    agg = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))
    total_share = (
        agg.completion_distribution.high.share
        + agg.completion_distribution.medium.share
        + agg.completion_distribution.low.share
    )
    assert abs(total_share - 1.0) < 0.01


def test_affected_users_and_share_are_correct():
    personas = _personas([100, 100, 100, 100])
    results = [
        _result(0, LikelyCompletion.LOW, 0.8, hypotheses=("h1",)),
        _result(1, LikelyCompletion.LOW, 0.6, hypotheses=("h1",)),
        _result(2, LikelyCompletion.HIGH, 0.9),
        _result(3, LikelyCompletion.HIGH, 0.9),
    ]
    agg = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario())

    assert len(agg.common_issues) == 1
    issue = agg.common_issues[0]
    assert issue.hypothesis_id == "h1"
    assert issue.affected_users == 200
    assert issue.affected_share == 0.5
    assert issue.affected_persona_indices == (0, 1)


def test_weighted_confidence_is_correct():
    personas = _personas([100, 300])
    results = [
        _result(0, LikelyCompletion.LOW, 0.2, hypotheses=("h1",)),
        _result(1, LikelyCompletion.LOW, 0.8, hypotheses=("h1",)),
    ]
    agg = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(("h1",)))

    issue = agg.common_issues[0]
    expected = (100 * 0.2 + 300 * 0.8) / 400
    assert issue.weighted_confidence == round(expected, 4)


def test_overall_confidence_is_weighted_average():
    personas = _personas([100, 300])
    results = [
        _result(0, LikelyCompletion.HIGH, 0.4),
        _result(1, LikelyCompletion.HIGH, 0.9),
    ]
    agg = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))
    expected = (100 * 0.4 + 300 * 0.9) / 400
    assert agg.overall_confidence == round(expected, 4)


def test_input_order_does_not_affect_result():
    personas = _personas([50, 60, 70, 80, 90])
    results = [
        _result(0, LikelyCompletion.HIGH, 0.9, hypotheses=("h1",)),
        _result(1, LikelyCompletion.MEDIUM, 0.6),
        _result(2, LikelyCompletion.LOW, 0.3, hypotheses=("h2",)),
        _result(3, LikelyCompletion.HIGH, 0.8),
        _result(4, LikelyCompletion.LOW, 0.4, hypotheses=("h1", "h2")),
    ]
    scenario = _scenario()

    agg_a = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=scenario)

    shuffled_personas = list(personas)
    shuffled_results = list(results)
    random.Random(11).shuffle(shuffled_personas)
    random.Random(13).shuffle(shuffled_results)

    agg_b = aggregate_persona_behavior(
        personas=shuffled_personas, behavior_results=shuffled_results, scenario=scenario
    )

    assert agg_a.aggregation_hash == agg_b.aggregation_hash
    assert agg_a == agg_b


def test_common_issues_are_sorted_deterministically():
    personas = _personas([10, 10, 10, 10])
    results = [
        _result(0, LikelyCompletion.LOW, 0.5, hypotheses=("h1",)),
        _result(1, LikelyCompletion.LOW, 0.5, hypotheses=("h2",)),
        _result(2, LikelyCompletion.LOW, 0.5, hypotheses=("h2",)),
        _result(3, LikelyCompletion.LOW, 0.5, hypotheses=("h1", "h2")),
    ]
    agg = aggregate_persona_behavior(
        personas=personas, behavior_results=results, scenario=_scenario(("h1", "h2"))
    )
    # h2: 30 (1,2,3), h1: 20 (0,3) -> affected_users DESC.
    assert [issue.hypothesis_id for issue in agg.common_issues] == ["h2", "h1"]


def test_empty_persona_result_is_rejected():
    personas = _personas([10])
    with pytest.raises(AIPipelineError) as exc_info:
        aggregate_persona_behavior(personas=personas, behavior_results=[], scenario=_scenario(()))
    assert exc_info.value.code == ERROR_PERSONA_RESULT_MISMATCH


def test_missing_persona_result_is_rejected():
    personas = _personas([10, 10])
    results = [_result(0, LikelyCompletion.HIGH, 0.5)]
    with pytest.raises(AIPipelineError) as exc_info:
        aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))
    assert exc_info.value.code == ERROR_PERSONA_RESULT_MISMATCH


def test_extra_persona_result_is_rejected():
    personas = _personas([10])
    results = [_result(0, LikelyCompletion.HIGH, 0.5), _result(1, LikelyCompletion.HIGH, 0.5)]
    with pytest.raises(AIPipelineError) as exc_info:
        aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))
    assert exc_info.value.code == ERROR_PERSONA_RESULT_MISMATCH


def test_no_personas_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        aggregate_persona_behavior(personas=[], behavior_results=[], scenario=_scenario(()))
    assert exc_info.value.code == ERROR_PERSONAS_REQUIRED


def test_invalid_hypothesis_reference_is_rejected():
    personas = _personas([10])
    results = [_result(0, LikelyCompletion.HIGH, 0.5, hypotheses=("does_not_exist",))]
    with pytest.raises(AIPipelineError) as exc_info:
        aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))
    assert exc_info.value.code == ERROR_INVALID_HYPOTHESIS_REFERENCE


def test_same_input_produces_same_aggregation_hash():
    personas = _personas([10, 20, 30])
    results = [
        _result(0, LikelyCompletion.HIGH, 0.9, hypotheses=("h1",)),
        _result(1, LikelyCompletion.MEDIUM, 0.5),
        _result(2, LikelyCompletion.LOW, 0.2, hypotheses=("h2",)),
    ]
    scenario = _scenario()
    a = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=scenario)
    b = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=scenario)
    assert a.aggregation_hash == b.aggregation_hash


def test_disclaimer_is_present_and_non_empty():
    personas = _personas([10])
    results = [_result(0, LikelyCompletion.HIGH, 0.5)]
    agg = aggregate_persona_behavior(personas=personas, behavior_results=results, scenario=_scenario(()))
    assert agg.disclaimer.strip()


def test_no_llm_or_provider_call_is_involved():
    """Bu modulun hicbir yerinde `httpx`/ag cagrisi yapan bir import
    OLMAMALIDIR - dosyanin kaynak kodunu statik olarak dogrular."""

    import inspect

    from app.services.ai_pipeline import aggregation

    source = inspect.getsource(aggregation)
    assert "httpx" not in source
    assert "requests" not in source
    assert "import asyncio" not in source
