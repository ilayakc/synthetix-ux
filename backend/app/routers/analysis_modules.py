import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import settings
from app.dependencies import get_organization_id
from app.services import module_catalog
from app.services.pricing import (
    FEATURE_ACCESSIBILITY_PRECHECK,
    FEATURE_BASIC_UX_TEST,
    get_pricing_config,
)

router = APIRouter(prefix="/api/analysis-modules", tags=["analysis-modules"])


class AnalysisModuleResponse(BaseModel):
    key: str
    name: str
    description: str
    outputs: list[str]
    measurement_type: str
    chip_cost: int
    free_entitlement_feature_key: str | None
    post_entitlement_cost_label: str | None
    estimated_duration_minutes: int
    selectable_in_wizard: bool
    supported_source_types: list[str]


class AnalysisModuleCatalogResponse(BaseModel):
    catalog_version: str
    modules: list[AnalysisModuleResponse]


def _post_entitlement_cost_label(module_key: str) -> str | None:
    pricing = get_pricing_config()
    if module_key in {FEATURE_BASIC_UX_TEST, "ab_comparison"}:
        return f"Persona başına {pricing.basic_ux_test_chip_per_persona} Chip"
    if module_key == FEATURE_ACCESSIBILITY_PRECHECK:
        return f"{pricing.accessibility_precheck_chip_cost} Chip"
    return None


@router.get("/catalog", response_model=AnalysisModuleCatalogResponse)
async def get_catalog(
    # Katalog global/surumludur (organizasyona ozel degildir); yine de tum
    # API kimlik dogrulamali oldugu icin bu bagimlilik korunur.
    _organization_id: uuid.UUID = Depends(get_organization_id),
) -> AnalysisModuleCatalogResponse:
    """Aktif analiz modullerinin surumlu katalogunu dondurur.

    `ai_report` yalnizca calisma-zamani hazirlik bayragi (`settings.
    ai_report_enabled`) acikken listelenir - saglayici hazir degilken
    (varsayilan) katalogda GORUNMEZ (bkz. module_catalog.
    get_wizard_visible_modules); yalnizca frontend gizlemesine guvenilmez,
    launch dogrulamasi da ayrica zorunludur (bkz. app.services.test_wizard).
    """

    modules = module_catalog.get_wizard_visible_modules(
        ai_report_enabled=settings.ai_report_enabled,
        ai_interaction_heatmap_enabled=settings.ai_interaction_heatmap_enabled,
    )

    return AnalysisModuleCatalogResponse(
        catalog_version=module_catalog.CURRENT_MODULE_CATALOG_VERSION,
        modules=[
            AnalysisModuleResponse(
                key=module.key,
                name=module.name,
                description=module.description,
                outputs=list(module.outputs),
                measurement_type=module.measurement_type,
                chip_cost=module.chip_cost,
                free_entitlement_feature_key=module.free_entitlement_feature_key,
                post_entitlement_cost_label=_post_entitlement_cost_label(module.key),
                estimated_duration_minutes=module.estimated_duration_minutes,
                selectable_in_wizard=module.selectable_in_wizard,
                supported_source_types=list(module.supported_source_types),
            )
            for module in modules
        ],
    )
