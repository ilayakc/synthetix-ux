"""Faz 2A: canonical JSON/hash, prompt descriptor hash ve idempotency key testleri.

Bu katman saftir (DB/ag cagrisi yok) - `pytest.mark.unit`.
"""

import math
import uuid
from datetime import UTC, datetime
from enum import Enum

import pytest

from app.services.ai_pipeline.errors import ERROR_CANONICALIZATION_FAILED, AIPipelineError
from app.services.ai_pipeline.hashing import (
    IdempotencyKeyInput,
    PromptDescriptor,
    canonical_json,
    compute_idempotency_key,
    hash_payload,
    hash_prompt_descriptor,
)

pytestmark = pytest.mark.unit


# --- canonical_json / hash_payload ------------------------------------------------


def test_dict_key_order_does_not_change_hash():
    a = {"b": 1, "a": 2, "c": 3}
    b = {"c": 3, "a": 2, "b": 1}
    assert hash_payload(a) == hash_payload(b)


def test_nested_dict_key_order_does_not_change_hash():
    a = {"outer": {"z": 1, "a": 2}}
    b = {"outer": {"a": 2, "z": 1}}
    assert hash_payload(a) == hash_payload(b)


def test_list_order_changes_hash():
    a = {"items": [1, 2, 3]}
    b = {"items": [3, 2, 1]}
    assert hash_payload(a) != hash_payload(b)


def test_uuid_is_stable_and_hashable():
    value = uuid.uuid4()
    assert hash_payload({"id": value}) == hash_payload({"id": value})
    assert hash_payload({"id": value}) == hash_payload({"id": str(value)})


def test_datetime_is_stable_and_hashable():
    value = datetime(2026, 7, 31, 10, 0, 0, tzinfo=UTC)
    assert hash_payload({"ts": value}) == hash_payload({"ts": value.isoformat()})


def test_enum_is_stable_and_hashable():
    class Color(str, Enum):
        RED = "red"

    assert hash_payload({"color": Color.RED}) == hash_payload({"color": "red"})


def test_turkish_characters_are_stable():
    value = {"metin": "Şğüçöı İĞÜÇÖŞ"}
    assert hash_payload(value) == hash_payload(dict(value))
    text = canonical_json(value)
    assert "Şğüçöı" in text  # ensure_ascii=False -> escape edilmez


def test_nan_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        hash_payload({"value": math.nan})
    assert exc_info.value.code == ERROR_CANONICALIZATION_FAILED


def test_infinity_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        hash_payload({"value": math.inf})
    assert exc_info.value.code == ERROR_CANONICALIZATION_FAILED


def test_unsupported_object_is_rejected_not_stringified():
    class Unsupported:
        def __str__(self) -> str:
            return "should-not-be-used"

    with pytest.raises(AIPipelineError) as exc_info:
        hash_payload({"value": Unsupported()})
    assert exc_info.value.code == ERROR_CANONICALIZATION_FAILED


def test_hash_is_64_char_lowercase_hex():
    digest = hash_payload({"a": 1})
    assert len(digest) == 64
    assert digest == digest.lower()
    int(digest, 16)  # gecerli hex olmali, aksi halde ValueError


def test_semantically_equal_lists_of_dicts_are_order_sensitive_per_item():
    a = {"items": [{"a": 1, "b": 2}]}
    b = {"items": [{"b": 2, "a": 1}]}
    # ic dict anahtar sirasi onemsiz, liste eleman sirasi (tek eleman burada) ayni.
    assert hash_payload(a) == hash_payload(b)


# --- Prompt descriptor hash --------------------------------------------------------


def _descriptor(**overrides) -> PromptDescriptor:
    base = dict(
        prompt_key="scenario_interpretation",
        prompt_version="scenario-v1",
        system_instructions="Sen bir UX analistisin.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        model_settings={"temperature": 0.0},
    )
    base.update(overrides)
    return PromptDescriptor(**base)


def test_same_prompt_descriptor_yields_same_hash():
    assert hash_prompt_descriptor(_descriptor()) == hash_prompt_descriptor(_descriptor())


def test_prompt_text_change_yields_different_hash():
    a = hash_prompt_descriptor(_descriptor(system_instructions="A"))
    b = hash_prompt_descriptor(_descriptor(system_instructions="B"))
    assert a != b


def test_prompt_version_change_yields_different_hash():
    a = hash_prompt_descriptor(_descriptor(prompt_version="v1"))
    b = hash_prompt_descriptor(_descriptor(prompt_version="v2"))
    assert a != b


def test_prompt_schema_change_yields_different_hash():
    a = hash_prompt_descriptor(_descriptor(input_schema={"type": "object"}))
    b = hash_prompt_descriptor(_descriptor(input_schema={"type": "array"}))
    assert a != b


def test_prompt_model_settings_change_yields_different_hash():
    a = hash_prompt_descriptor(_descriptor(model_settings={"temperature": 0.0}))
    b = hash_prompt_descriptor(_descriptor(model_settings={"temperature": 0.5}))
    assert a != b


# --- Idempotency key ---------------------------------------------------------------


def _key_input(**overrides) -> IdempotencyKeyInput:
    base = dict(
        simulation_run_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        stage_type="persona_behavior",
        batch_index=0,
        prompt_version="persona-behavior-v1",
        prompt_hash="a" * 64,
        provider="deterministic",
        model_name="python",
        input_hash="b" * 64,
    )
    base.update(overrides)
    return IdempotencyKeyInput(**base)


def test_batch_index_none_and_zero_produce_different_keys():
    key_none = compute_idempotency_key(_key_input(batch_index=None))
    key_zero = compute_idempotency_key(_key_input(batch_index=0))
    assert key_none != key_zero


@pytest.mark.parametrize(
    "field,other_value",
    [
        ("stage_type", "aggregation"),
        ("provider", "openai"),
        ("model_name", "gpt-x"),
        ("input_hash", "c" * 64),
        ("prompt_version", "persona-behavior-v2"),
        ("prompt_hash", "d" * 64),
    ],
)
def test_changing_any_field_changes_idempotency_key(field: str, other_value: str):
    base_key = compute_idempotency_key(_key_input())
    changed_key = compute_idempotency_key(_key_input(**{field: other_value}))
    assert base_key != changed_key


def test_idempotency_key_is_64_char_lowercase_hex():
    key = compute_idempotency_key(_key_input())
    assert len(key) == 64
    assert key == key.lower()
    int(key, 16)


def test_idempotency_key_fits_in_string_128_column():
    key = compute_idempotency_key(_key_input())
    assert len(key) <= 128


def test_same_key_input_is_deterministic():
    assert compute_idempotency_key(_key_input()) == compute_idempotency_key(_key_input())
