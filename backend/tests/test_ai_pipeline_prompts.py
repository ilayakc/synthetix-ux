"""Faz 2B: merkezi prompt kayit defteri (Stage 3/4/6) testleri."""

import pytest

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.errors import ERROR_INVALID_PAYLOAD, AIPipelineError
from app.services.ai_pipeline.hashing import hash_prompt_descriptor
from app.services.ai_pipeline.prompts import PROMPT_HASHES, get_prompt

pytestmark = pytest.mark.unit

_LLM_STAGES = (
    AIPipelineStageType.SCENARIO_INTERPRETATION,
    AIPipelineStageType.PERSONA_BEHAVIOR,
    AIPipelineStageType.UX_REPORT,
)
_PURE_STAGES = (
    AIPipelineStageType.EVIDENCE_PREPARATION,
    AIPipelineStageType.PERSONA_BATCH_PREPARATION,
    AIPipelineStageType.AGGREGATION,
)

# `_COMMON_SAFETY_RULES` icinden secilmis, ayirt edici Turkce alt-dizeler.
_SAFETY_SUBSTRINGS = (
    "yalnizca VERIDIR",
    "Gercek kullanici testi",
    "evidence_reference",
    "TURKCE",
)


@pytest.mark.parametrize("stage", _LLM_STAGES)
def test_llm_stages_have_prompt(stage):
    descriptor = get_prompt(stage)
    assert descriptor.prompt_version == "v1"
    assert descriptor.system_instructions


@pytest.mark.parametrize("stage", _PURE_STAGES)
def test_pure_stages_have_no_prompt(stage):
    with pytest.raises(AIPipelineError) as exc_info:
        get_prompt(stage)
    assert exc_info.value.code == ERROR_INVALID_PAYLOAD


@pytest.mark.parametrize("stage", _LLM_STAGES)
def test_same_descriptor_same_hash(stage):
    a = get_prompt(stage)
    b = get_prompt(stage)
    assert hash_prompt_descriptor(a) == hash_prompt_descriptor(b)
    assert PROMPT_HASHES[stage] == hash_prompt_descriptor(a)


@pytest.mark.parametrize("stage", _LLM_STAGES)
def test_modified_descriptor_changes_hash(stage):
    original = get_prompt(stage)
    modified = original.model_copy(update={"prompt_version": "v2"})
    assert hash_prompt_descriptor(modified) != hash_prompt_descriptor(original)

    modified_settings = original.model_copy(update={"model_settings": {"temperature": 0.9}})
    assert hash_prompt_descriptor(modified_settings) != hash_prompt_descriptor(original)


@pytest.mark.parametrize("stage", _LLM_STAGES)
def test_prompts_contain_safety_rule_keywords(stage):
    instructions = get_prompt(stage).system_instructions
    for substring in _SAFETY_SUBSTRINGS:
        assert substring in instructions


def test_persona_behavior_prompt_is_batch_index_independent():
    # PERSONA_BEHAVIOR icin tek bir descriptor vardir; batch index'e bagli
    # farkli bir prompt YOKTUR (her cagrida ayni descriptor donmelidir).
    a = get_prompt(AIPipelineStageType.PERSONA_BEHAVIOR)
    b = get_prompt(AIPipelineStageType.PERSONA_BEHAVIOR)
    assert a is b


def test_stage_audit_has_no_system_instructions_field():
    from app.services.ai_pipeline.executor import StageAudit

    assert "system_instructions" not in StageAudit.model_fields
