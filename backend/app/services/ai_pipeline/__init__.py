"""Asamali AI pipeline'in saf (DB'siz, ag cagrisiz, LLM'siz) temeli - Faz 2A.

Bu paket, ileride (Faz 2B+) eklenecek `AIProvider`/`MockAIProvider`,
worker, API ve Chip entegrasyonunun UZERINE insa edilecegi, tamamen saf
(hic bir yan etki uretmeyen) yapi taslarini icerir:

- `hashing`: canonical JSON + SHA-256 hash, prompt descriptor hash,
  idempotency key hesaplama.
- `schemas`: tum asamalarin (1-6) sikilastirilmis Pydantic sozlesmeleri.
- `evidence`: Stage 1 - page evidence hazirlama.
- `batching`: Stage 2 - persona context batching.
- `aggregation`: Stage 5 - deterministik (LLM'siz) agirlikli ozet.
- `validation`: asamalar-arasi (cross-stage) tutarlilik dogrulamasi.
- `errors`: sinirli, guvenli domain hata kodlari.

Bu pakette provider, prompt metni, worker, API endpoint'i veya DB
persistence YOKTUR (bkz. Faz 2A gorev talimati).
"""

from app.services.ai_pipeline.aggregation import aggregate_persona_behavior
from app.services.ai_pipeline.batching import build_persona_batches
from app.services.ai_pipeline.errors import AIPipelineError
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.executor import (
    AIPipelineExecutionInput,
    AIPipelineExecutionResult,
    PipelineStageError,
    StageAudit,
    execute_pipeline,
)
from app.services.ai_pipeline.hashing import (
    canonical_json,
    compute_idempotency_key,
    hash_payload,
    hash_prompt_descriptor,
)
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.prompts import get_prompt
from app.services.ai_pipeline.provider import AIProvider, ProviderResult
from app.services.ai_pipeline.validation import (
    validate_persona_behavior_batch,
    validate_scenario_interpretation,
    validate_ux_report,
)

__all__ = [
    "AIPipelineError",
    "canonical_json",
    "hash_payload",
    "hash_prompt_descriptor",
    "compute_idempotency_key",
    "prepare_page_evidence",
    "build_persona_batches",
    "aggregate_persona_behavior",
    "validate_scenario_interpretation",
    "validate_persona_behavior_batch",
    "validate_ux_report",
    "AIProvider",
    "ProviderResult",
    "MockAIProvider",
    "get_prompt",
    "execute_pipeline",
    "AIPipelineExecutionInput",
    "AIPipelineExecutionResult",
    "StageAudit",
    "PipelineStageError",
]
