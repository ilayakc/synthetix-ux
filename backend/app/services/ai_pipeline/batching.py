"""Stage 2 - Persona context batching (saf, DB'siz, ag cagrisiz).

Kalici `Persona` satirlarindan (bkz. app.models.personas.Persona) BAGIMSIZ
calisir - `build_persona_batches` bir ORM nesnesi degil, zaten olusturulmus
`PersonaContext` DTO'lari alir. 100 Persona icin (varsayilan batch_size=15
ile) en fazla 7 batch olusur; hicbir Persona kaybolmaz veya iki kez
eklenmez (bkz. testler).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.services.ai_pipeline.errors import (
    ERROR_BATCH_LIMIT_EXCEEDED,
    ERROR_DUPLICATE_PERSONA,
    ERROR_INVALID_PAYLOAD,
    ERROR_PERSONAS_REQUIRED,
    AIPipelineError,
)
from app.services.ai_pipeline.schemas import (
    MAX_PERSONA_BATCH_SIZE,
    MAX_PERSONAS_PER_PIPELINE,
    MIN_PERSONA_BATCH_SIZE,
    PersonaBatch,
    PersonaContext,
)
from app.services.ai_pipeline.stage_hashing import compute_batch_hash


def build_persona_batches(
    personas: Sequence[PersonaContext],
    batch_size: int = 15,
) -> tuple[PersonaBatch, ...]:
    """`personas`i (kanonik `index` ASC sirada) sabit boyutlu batch'lere ayirir.

    Girdi (`personas`) sirasi SONUCU ETKILEMEZ - cikti daima `index` ASC
    kanonik siraya gore olusturulur (bkz. testler "karisik girdi sirasi
    ayni canonical batch sonucu"). Girdi listesi mutate EDILMEZ.
    """

    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise AIPipelineError(ERROR_INVALID_PAYLOAD, "batch_size bir tam sayi olmalidir")
    if not (MIN_PERSONA_BATCH_SIZE <= batch_size <= MAX_PERSONA_BATCH_SIZE):
        raise AIPipelineError(
            ERROR_INVALID_PAYLOAD,
            f"batch_size {MIN_PERSONA_BATCH_SIZE} ile {MAX_PERSONA_BATCH_SIZE} arasinda olmalidir",
        )

    if not personas:
        raise AIPipelineError(ERROR_PERSONAS_REQUIRED, "en az bir persona gereklidir")

    if len(personas) > MAX_PERSONAS_PER_PIPELINE:
        raise AIPipelineError(
            ERROR_BATCH_LIMIT_EXCEEDED,
            f"en fazla {MAX_PERSONAS_PER_PIPELINE} persona kabul edilir " f"(gelen: {len(personas)})",
        )

    indices = [p.index for p in personas]
    if len(indices) != len(set(indices)):
        raise AIPipelineError(ERROR_DUPLICATE_PERSONA, "yinelenen persona index degeri bulundu")

    persona_ids = [p.persona_id for p in personas]
    if len(persona_ids) != len(set(persona_ids)):
        raise AIPipelineError(ERROR_DUPLICATE_PERSONA, "yinelenen persona_id degeri bulundu")

    canonical_order = tuple(sorted(personas, key=lambda p: p.index))

    batch_count = math.ceil(len(canonical_order) / batch_size)
    batches: list[PersonaBatch] = []
    for batch_index in range(batch_count):
        start = batch_index * batch_size
        chunk = canonical_order[start : start + batch_size]
        population_weight = sum(p.population_weight for p in chunk)

        batch_hash = compute_batch_hash(
            batch_index=batch_index,
            personas=chunk,
            population_weight=population_weight,
        )

        batches.append(
            PersonaBatch(
                batch_index=batch_index,
                personas=chunk,
                population_weight=population_weight,
                batch_hash=batch_hash,
            )
        )

    return tuple(batches)


__all__ = ["build_persona_batches"]
