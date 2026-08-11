"""AI etkilesim isi haritasi (ai_interaction_heatmap) servis paketi.

Gorev-odakli, koordinat-guvenli AI tiklama tahmini. `ai_report`ten BAGIMSIZDIR:
kendi modul anahtari, kendi fingerprint/cache'i ve kendi (deterministik veya
provider destekli) siralama motoru vardir. Ortak kanit hazirligi (evidence) ve
- mumkunse - ayni provider analizi paylasilabilir, ama bu paket ai_report
pipeline'ina bagimli DEGILDIR.
"""

from app.services.ai_interaction_heatmap.candidates import build_interaction_candidates
from app.services.ai_interaction_heatmap.ranking import rank_interaction_hotspots
from app.services.ai_interaction_heatmap.schemas import (
    INTERACTION_HEATMAP_DISCLAIMER,
    INTERACTION_HEATMAP_PROMPT_VERSION,
    INTERACTION_HEATMAP_SCHEMA_VERSION,
    MAX_HOTSPOTS,
    METHOD_DOM_VISUAL_RANKING,
    METHOD_LABELS,
    METHOD_OPENAI_SELECTION,
    NO_STRONG_MATCH_WARNING,
    AIHotspotSelection,
    AIInteractionOutput,
    InteractionCandidate,
    InteractionHeatmapResult,
    ResolvedHotspot,
)
from app.services.ai_interaction_heatmap.service import (
    InvalidInteractionOutputError,
    build_interaction_heatmap,
    compute_interaction_heatmap_fingerprint,
    validate_interaction_output,
)

__all__ = [
    "INTERACTION_HEATMAP_DISCLAIMER",
    "INTERACTION_HEATMAP_PROMPT_VERSION",
    "INTERACTION_HEATMAP_SCHEMA_VERSION",
    "MAX_HOTSPOTS",
    "METHOD_DOM_VISUAL_RANKING",
    "METHOD_LABELS",
    "METHOD_OPENAI_SELECTION",
    "NO_STRONG_MATCH_WARNING",
    "AIHotspotSelection",
    "AIInteractionOutput",
    "InteractionCandidate",
    "InteractionHeatmapResult",
    "ResolvedHotspot",
    "InvalidInteractionOutputError",
    "build_interaction_candidates",
    "build_interaction_heatmap",
    "compute_interaction_heatmap_fingerprint",
    "rank_interaction_hotspots",
    "validate_interaction_output",
]
