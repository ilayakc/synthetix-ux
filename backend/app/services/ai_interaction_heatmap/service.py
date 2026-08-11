"""AI etkilesim isi haritasi: fingerprint, dogrulama ve saf orkestrasyon.

Bu katman SAFTIR (DB/ag yok). Cagiran taraf (bkz. app.routers.reports) sonucu
fingerprint'e gore cache'ler; ayni fingerprint icin ikinci bir uretim (ve
gercek provider dallaninca ikinci bir provider cagrisi) YAPILMAZ.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from app.services.ai_interaction_heatmap.ranking import rank_interaction_hotspots
from app.services.ai_interaction_heatmap.schemas import (
    INTERACTION_HEATMAP_DISCLAIMER,
    INTERACTION_HEATMAP_PROMPT_VERSION,
    INTERACTION_HEATMAP_SCHEMA_VERSION,
    MAX_HOTSPOTS,
    METHOD_DOM_VISUAL_RANKING,
    METHOD_LABELS,
    AIInteractionOutput,
    InteractionCandidate,
    InteractionHeatmapResult,
    ResolvedHotspot,
)
from app.services.ai_pipeline.hashing import hash_payload

# Bir secici (deterministik siralayici veya gercek provider adaptoru), aday
# listesi + hedef gorev + onaylı aday id'sini alip yapilandirilmis cikti dondurur.
Selector = Callable[[list[InteractionCandidate], str, str | None], AIInteractionOutput]


class InvalidInteractionOutputError(Exception):
    """AI/secici ciktisi sozlesmeyi (bilinen id, tekrarsizlik, sinir) ihlal
    ettiginde firlatilir. Cagiran taraf ciktiyi HARITAYA DONUSTURMEZ - sahte
    fallback uretmez (gorev talimati "Gecersiz cikti haritaya donusturulmemeli",
    "AI sonucu yoksa sahte fallback AI sonucu uretme")."""


def compute_interaction_heatmap_fingerprint(
    *,
    content_sha256: str | None,
    target_task: str,
    target_audience: str | None,
    persona_distribution: Mapping | None,
    candidates: Sequence[InteractionCandidate],
    user_confirmed_candidate_id: str | None,
    model_name: str,
) -> str:
    """Yeniden uretilebilirlik/cache fingerprint'i (gorev talimati
    "TEKRARLANABILIRLIK VE CACHE"). Asagidakilerden biri degisirse degisir:
    ekran goruntusu/icerik, hedef gorev, hedef kitle, persona dagilimi, gercek
    aday listesi, kullanicinin sectigi CTA, model, prompt/sema surumu."""

    payload = {
        "content_sha256": content_sha256,
        "target_task": target_task,
        "target_audience": target_audience,
        "persona_distribution": dict(persona_distribution) if persona_distribution else None,
        "candidates": [c.model_dump(mode="json") for c in candidates],
        "user_confirmed_candidate_id": user_confirmed_candidate_id,
        "model_name": model_name,
        "prompt_version": INTERACTION_HEATMAP_PROMPT_VERSION,
        "schema_version": INTERACTION_HEATMAP_SCHEMA_VERSION,
    }
    return hash_payload(payload)


def validate_interaction_output(
    output: AIInteractionOutput, candidates: Sequence[InteractionCandidate]
) -> None:
    """Ciktiyi gercek aday listesine gore dogrular; ihlalde
    `InvalidInteractionOutputError` firlatir.

    (Koordinat alanlari zaten `AIHotspotSelection`in `extra="forbid"`
    semasiyla reddedilir - AI koordinat dondurememelidir.)
    """

    known_ids = {c.candidate_id for c in candidates}
    if len(output.hotspots) > MAX_HOTSPOTS:
        raise InvalidInteractionOutputError(
            f"en fazla {MAX_HOTSPOTS} hotspot olabilir ({len(output.hotspots)} geldi)"
        )
    seen: set[str] = set()
    for hotspot in output.hotspots:
        if hotspot.candidate_id not in known_ids:
            raise InvalidInteractionOutputError(f"bilinmeyen candidate_id: {hotspot.candidate_id}")
        if hotspot.candidate_id in seen:
            raise InvalidInteractionOutputError(f"tekrar eden candidate_id: {hotspot.candidate_id}")
        seen.add(hotspot.candidate_id)


def _resolve(output: AIInteractionOutput, candidates: Sequence[InteractionCandidate]) -> list[ResolvedHotspot]:
    by_id = {c.candidate_id: c for c in candidates}
    resolved: list[ResolvedHotspot] = []
    for hotspot in output.hotspots:
        candidate = by_id[hotspot.candidate_id]
        resolved.append(
            ResolvedHotspot(
                candidate_id=candidate.candidate_id,
                label=candidate.label,
                interaction_kind=candidate.interaction_kind,
                role=candidate.role,
                x=candidate.x,
                y=candidate.y,
                w=candidate.w,
                h=candidate.h,
                above_fold=candidate.above_fold,
                score=hotspot.score,
                confidence=hotspot.confidence,
                reason=hotspot.reason,
                task_relevance=hotspot.task_relevance,
            )
        )
    return resolved


def build_interaction_heatmap(
    *,
    candidates: list[InteractionCandidate],
    target_task: str,
    target_audience: str | None,
    persona_distribution: Mapping | None,
    content_sha256: str | None,
    confirmed_candidate_id: str | None,
    model_name: str,
    provider_name: str,
    method: str = METHOD_DOM_VISUAL_RANKING,
    selector: Selector | None = None,
) -> InteractionHeatmapResult:
    """Adaylardan gorev-odakli AI etkilesim isi haritasi uretir (saf).

    `selector` verilmezse deterministik `rank_interaction_hotspots` kullanilir
    (varsayilan "DOM ve gorsel ozellik destekli AI siralamasi"). Gercek bir
    provider dallaninda ayni sozlesmeli bir `selector` gecirilir; her iki yol
    da AYNI dogrulamadan gecer.
    """

    fingerprint = compute_interaction_heatmap_fingerprint(
        content_sha256=content_sha256,
        target_task=target_task,
        target_audience=target_audience,
        persona_distribution=persona_distribution,
        candidates=candidates,
        user_confirmed_candidate_id=confirmed_candidate_id,
        model_name=model_name,
    )

    if selector is None:
        output = rank_interaction_hotspots(
            candidates, target_task=target_task, confirmed_candidate_id=confirmed_candidate_id
        )
    else:
        output = selector(candidates, target_task, confirmed_candidate_id)

    validate_interaction_output(output, candidates)

    return InteractionHeatmapResult(
        schema_version=INTERACTION_HEATMAP_SCHEMA_VERSION,
        prompt_version=INTERACTION_HEATMAP_PROMPT_VERSION,
        method=method,
        method_label=METHOD_LABELS.get(method, method),
        model_name=model_name,
        provider=provider_name,
        fingerprint=fingerprint,
        summary=output.summary,
        hotspots=_resolve(output, candidates),
        unmatched_task_warning=output.unmatched_task_warning,
        disclaimer=INTERACTION_HEATMAP_DISCLAIMER,
    )


__all__ = [
    "Selector",
    "InvalidInteractionOutputError",
    "compute_interaction_heatmap_fingerprint",
    "validate_interaction_output",
    "build_interaction_heatmap",
]
