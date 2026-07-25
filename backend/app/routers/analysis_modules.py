import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.dependencies import get_organization_id
from app.services import module_catalog

router = APIRouter(prefix="/api/analysis-modules", tags=["analysis-modules"])


class AnalysisModuleResponse(BaseModel):
    key: str
    name: str
    description: str
    outputs: list[str]
    measurement_type: str
    chip_cost: int
    free_entitlement_feature_key: str | None
    estimated_duration_minutes: int
    selectable_in_wizard: bool
    supported_source_types: list[str]


class AnalysisModuleCatalogResponse(BaseModel):
    catalog_version: str
    modules: list[AnalysisModuleResponse]


@router.get("/catalog", response_model=AnalysisModuleCatalogResponse)
async def get_catalog(
    # Katalog global/surumludur (organizasyona ozel degildir); yine de tum
    # API kimlik dogrulamali oldugu icin bu bagimlilik korunur.
    _organization_id: uuid.UUID = Depends(get_organization_id),
) -> AnalysisModuleCatalogResponse:
    """Aktif analiz modullerinin surumlu katalogunu dondurur."""

    modules = module_catalog.get_active_module_catalog()

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
                estimated_duration_minutes=module.estimated_duration_minutes,
                selectable_in_wizard=module.selectable_in_wizard,
                supported_source_types=list(module.supported_source_types),
            )
            for module in modules
        ],
    )
