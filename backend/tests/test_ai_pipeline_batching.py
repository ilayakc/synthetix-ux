"""Faz 2A: Stage 2 (persona context batching) testleri."""

import random
import uuid

import pytest

from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.errors import (
    ERROR_BATCH_LIMIT_EXCEEDED,
    ERROR_DUPLICATE_PERSONA,
    ERROR_INVALID_PAYLOAD,
    ERROR_PERSONAS_REQUIRED,
    AIPipelineError,
)
from app.services.ai_pipeline.schemas import DEFAULT_PERSONA_BATCH_SIZE, PersonaContext

pytestmark = pytest.mark.unit


def _personas(count: int, *, weight: int = 10) -> list[PersonaContext]:
    return [
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=i,
            label=f"Persona {i}",
            attributes={"device_class": "mobile"},
            population_weight=weight,
        )
        for i in range(count)
    ]


def test_single_persona_yields_single_batch():
    batches = build_persona_batches(_personas(1))
    assert len(batches) == 1
    assert len(batches[0].personas) == 1


def test_default_batch_size_boundary_is_one_batch():
    batches = build_persona_batches(_personas(DEFAULT_PERSONA_BATCH_SIZE))
    assert len(batches) == 1
    assert len(batches[0].personas) == DEFAULT_PERSONA_BATCH_SIZE


def test_one_over_default_batch_size_is_two_batches():
    batches = build_persona_batches(_personas(DEFAULT_PERSONA_BATCH_SIZE + 1))
    assert len(batches) == 2
    assert len(batches[0].personas) == DEFAULT_PERSONA_BATCH_SIZE
    assert len(batches[1].personas) == 1


def test_100_personas_default_batch_size_yields_7_batches():
    batches = build_persona_batches(_personas(100))
    assert len(batches) == 7
    assert sum(len(b.personas) for b in batches) == 100


def test_batch_size_zero_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        build_persona_batches(_personas(5), batch_size=0)
    assert exc_info.value.code == ERROR_INVALID_PAYLOAD


def test_batch_size_21_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        build_persona_batches(_personas(5), batch_size=21)
    assert exc_info.value.code == ERROR_INVALID_PAYLOAD


def test_empty_persona_list_raises_personas_required():
    with pytest.raises(AIPipelineError) as exc_info:
        build_persona_batches([])
    assert exc_info.value.code == ERROR_PERSONAS_REQUIRED


def test_more_than_100_personas_is_rejected():
    with pytest.raises(AIPipelineError) as exc_info:
        build_persona_batches(_personas(101))
    assert exc_info.value.code == ERROR_BATCH_LIMIT_EXCEEDED


def test_duplicate_index_is_rejected():
    personas = _personas(3)
    personas[2] = personas[2].model_copy(update={"index": 0})
    with pytest.raises(AIPipelineError) as exc_info:
        build_persona_batches(personas)
    assert exc_info.value.code == ERROR_DUPLICATE_PERSONA


def test_duplicate_persona_id_is_rejected():
    personas = _personas(3)
    personas[1] = personas[1].model_copy(update={"persona_id": personas[0].persona_id})
    with pytest.raises(AIPipelineError) as exc_info:
        build_persona_batches(personas)
    assert exc_info.value.code == ERROR_DUPLICATE_PERSONA


def test_shuffled_input_order_produces_identical_canonical_batches():
    ordered = _personas(37)
    shuffled = list(ordered)
    random.Random(7).shuffle(shuffled)

    batches_a = build_persona_batches(ordered, batch_size=15)
    batches_b = build_persona_batches(shuffled, batch_size=15)

    assert [b.batch_hash for b in batches_a] == [b.batch_hash for b in batches_b]
    assert [tuple(p.index for p in b.personas) for b in batches_a] == [
        tuple(p.index for p in b.personas) for b in batches_b
    ]


def test_every_persona_appears_exactly_once():
    personas = _personas(43)
    batches = build_persona_batches(personas, batch_size=15)
    all_indices = [p.index for b in batches for p in b.personas]
    assert sorted(all_indices) == list(range(43))
    assert len(all_indices) == len(set(all_indices))


def test_total_population_weight_is_preserved():
    personas = _personas(43, weight=7)
    batches = build_persona_batches(personas, batch_size=15)
    assert sum(b.population_weight for b in batches) == 43 * 7
    for batch in batches:
        assert batch.population_weight == sum(p.population_weight for p in batch.personas)


def test_batch_hash_is_deterministic():
    personas = _personas(20)
    batches_a = build_persona_batches(personas, batch_size=15)
    batches_b = build_persona_batches(personas, batch_size=15)
    assert [b.batch_hash for b in batches_a] == [b.batch_hash for b in batches_b]


def test_unknown_persona_dimension_key_is_rejected():
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=0,
            label="P0",
            attributes={"unknown_dimension": "x"},
            population_weight=10,
        )


def test_banned_inference_attribute_key_is_rejected():
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError (PersonaValidationError icinde)
        PersonaContext(
            persona_id=uuid.uuid4(),
            index=0,
            label="P0",
            attributes={"income": "high"},
            population_weight=10,
        )
