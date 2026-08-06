"""Stage 1 - Page evidence preparation (saf, DB'siz, ag cagrisiz).

`prepare_page_evidence`, SQLAlchemy session/ORM lazy-loading KULLANMAZ -
cagiran taraf (ileride Faz 2B+'daki orkestrasyon katmani) zaten yuklenmis
`SimulationRun.result["metrics"]`, `SimulationRun.result.get(
"page_feature_snapshot")` ve `SimulationRun.result.get("modules")`
sozluklerini (veya bunlarin alt kumelerini) acikca parametre olarak
gecirir. Bu fonksiyon TUM `input_snapshot`'i veya TUM `SimulationRun.result`
dict'ini asla parametre olarak kabul ETMEZ - yalnizca allowlist edilmis,
adi/tipi burada sabit olan alanlari okur (bkz. gorev talimati madde 6).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.services.ai_pipeline.errors import ERROR_INVALID_EVIDENCE, AIPipelineError
from app.services.ai_pipeline.hashing import hash_payload
from app.services.ai_pipeline.schemas import PAGE_EVIDENCE_VERSION, PageEvidence, PageEvidenceItem

# `SimulationResult.metrics`teki (bkz. app.engine.baseline) hangi alt
# alanlarin evidence'a alinacagini sabitler - keyfi/rastgele bir alt kume
# DEGIL, motorun ureptigi sabit semaya karsilik gelir.
_METRIC_POINT_ESTIMATE_KEYS: tuple[str, ...] = (
    "task_completion_probability",
    "misclick_probability",
    "abandonment_probability",
    "task_duration_seconds",
)

# `PageFeatureSnapshot` (bkz. app.engine.fixtures) alanlarindan evidence'a
# alinacak, tamamen yapilandirilmis/sayisal alt kume - ham DOM/HTML asla
# buraya girmez (zaten `PageFeatureSnapshot` byle bir alan tasimaz).
_PAGE_FEATURE_KEYS: tuple[str, ...] = (
    "nav_depth",
    "primary_cta_count",
    "form_field_count",
    "above_fold_cta",
    "heading_count",
    "mobile_friendly",
    "min_contrast_ratio",
    "avg_contrast_ratio",
)


def _metric_items(metrics: Mapping[str, object]) -> list[PageEvidenceItem]:
    items: list[PageEvidenceItem] = []
    for key in _METRIC_POINT_ESTIMATE_KEYS:
        entry = metrics.get(key)
        if not isinstance(entry, Mapping):
            continue
        point_estimate = entry.get("point_estimate")
        if not isinstance(point_estimate, int | float) or isinstance(point_estimate, bool):
            continue
        items.append(
            PageEvidenceItem(
                evidence_id=f"metric:{key}",
                category="metric",
                label=key,
                value=float(point_estimate),
            )
        )

    readability = metrics.get("readability_score")
    if isinstance(readability, int | float) and not isinstance(readability, bool):
        items.append(
            PageEvidenceItem(
                evidence_id="metric:readability_score",
                category="metric",
                label="readability_score",
                value=float(readability),
            )
        )

    contrast = metrics.get("contrast_check")
    if isinstance(contrast, Mapping):
        passed = contrast.get("pass")
        if isinstance(passed, bool):
            items.append(
                PageEvidenceItem(
                    evidence_id="metric:contrast_check_pass",
                    category="metric",
                    label="contrast_check_pass",
                    value=passed,
                )
            )
        avg_ratio = contrast.get("avg_ratio")
        if isinstance(avg_ratio, int | float) and not isinstance(avg_ratio, bool):
            items.append(
                PageEvidenceItem(
                    evidence_id="metric:contrast_avg_ratio",
                    category="metric",
                    label="contrast_avg_ratio",
                    value=float(avg_ratio),
                )
            )

    return items


def _page_feature_items(page_features: Mapping[str, object] | None) -> list[PageEvidenceItem]:
    if not page_features:
        return []
    items: list[PageEvidenceItem] = []
    for key in _PAGE_FEATURE_KEYS:
        if key not in page_features:
            continue
        value = page_features[key]
        if isinstance(value, bool) or isinstance(value, int | float):
            items.append(
                PageEvidenceItem(
                    evidence_id=f"page:{key}",
                    category="page",
                    label=key,
                    value=value,
                )
            )
    return items


def _module_items(
    selected_modules: Sequence[str], module_results: Mapping[str, Mapping[str, object]] | None
) -> list[PageEvidenceItem]:
    """Secili modullerin YALNIZCA "bu modul icin sonuc mevcut" bilgisini
    tasir - her modulun kendi ic sema alanlarina (ornegin CTA listesi,
    dikkat izgarasi) burada GIRILMEZ; bu, modullerin heterojen/degisken
    ic yapisina bagli yanlis varsayim riskini ortadan kaldiran BILINCLI,
    muhafazakar bir sinirlamadir (bkz. Faz 2A raporu)."""

    if not selected_modules or not module_results:
        return []
    items: list[PageEvidenceItem] = []
    for module_key in selected_modules:
        if module_key in module_results:
            items.append(
                PageEvidenceItem(
                    evidence_id=f"module:{module_key}",
                    category="module",
                    label=module_key,
                    value=True,
                )
            )
    return items


def prepare_page_evidence(
    *,
    source_type: str,
    metrics: Mapping[str, object],
    page_features: Mapping[str, object] | None = None,
    selected_modules: Sequence[str] = (),
    module_results: Mapping[str, Mapping[str, object]] | None = None,
) -> PageEvidence:
    """`metrics`/`page_features`/`module_results` sozluklerinden (allowlisted
    alt kumeleriyle) sinirli/temizlenmis bir `PageEvidence` uretir.

    `metrics` ZORUNLUDUR - hicbir taninan metrik alani yoksa (bos/gecersiz
    girdi) `ERROR_INVALID_EVIDENCE` firlatilir. `page_features`/
    `module_results` OPSIYONELDIR - eksik olmalari fonksiyonu COKERTMEZ,
    yalnizca ilgili kategori bos kalir.
    """

    if not isinstance(source_type, str) or not source_type.strip():
        raise AIPipelineError(ERROR_INVALID_EVIDENCE, "source_type bos olamaz")
    if not isinstance(metrics, Mapping):
        raise AIPipelineError(ERROR_INVALID_EVIDENCE, "metrics bir sozluk olmalidir")

    items = [
        *_metric_items(metrics),
        *_page_feature_items(page_features),
        *_module_items(selected_modules, module_results),
    ]
    if not items:
        raise AIPipelineError(
            ERROR_INVALID_EVIDENCE, "evidence icin hicbir taninan zorunlu/opsiyonel alan bulunamadi"
        )

    ordered_items = tuple(sorted(items, key=lambda item: item.evidence_id))

    hashable_payload = {
        "evidence_version": PAGE_EVIDENCE_VERSION,
        "source_type": source_type,
        "items": [item.model_dump(mode="json") for item in ordered_items],
    }
    evidence_hash = hash_payload(hashable_payload)

    return PageEvidence(
        evidence_version=PAGE_EVIDENCE_VERSION,
        source_type=source_type,
        items=ordered_items,
        evidence_hash=evidence_hash,
    )


__all__ = ["prepare_page_evidence"]
