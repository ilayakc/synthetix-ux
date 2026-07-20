import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.tests import PersonaPreset
from app.services import persona_presets, personas

router = APIRouter(prefix="/api/personas", tags=["personas"])

# Preset olusturma/guncelleme/kopyalama/arsivleme: viewer haric tum roller
# (bkz. docs/architecture.md#roller-ve-yetkiler, "Proje/test/simulasyon olustur").
WRITE_ROLES = ("analyst", "admin", "owner")


# --- Boyut semasi (form olusturma icin) ---------------------------------------


class DimensionInfo(BaseModel):
    key: str
    label: str


@router.get("/dimensions", response_model=list[DimensionInfo])
async def list_dimensions() -> list[DimensionInfo]:
    """Sihirbaz/preset formunun olusturacagi persona boyutlarinin semasini dondurur."""

    return [
        DimensionInfo(key=key, label=personas.DIMENSION_LABELS[key]) for key in personas.PERSONA_DIMENSIONS
    ]


# --- Presetler (hazir + sirkete ozel) -----------------------------------------


class PresetResponse(BaseModel):
    id: str
    is_builtin: bool
    organization_id: uuid.UUID | None
    name: str
    description: str | None
    distribution: dict
    status: str
    source_builtin_key: str | None
    created_at: datetime | None
    updated_at: datetime | None
    archived_at: datetime | None


def _builtin_to_response(builtin: personas.BuiltinPreset) -> PresetResponse:
    return PresetResponse(
        id=personas.builtin_preset_id(builtin.key),
        is_builtin=True,
        organization_id=None,
        name=builtin.name,
        description=builtin.description,
        distribution=builtin.distribution,
        status="active",
        source_builtin_key=None,
        created_at=None,
        updated_at=None,
        archived_at=None,
    )


def _org_preset_to_response(preset: PersonaPreset) -> PresetResponse:
    return PresetResponse(
        id=str(preset.id),
        is_builtin=False,
        organization_id=preset.organization_id,
        name=preset.name,
        description=preset.description,
        distribution=preset.config,
        status=preset.status.value,
        source_builtin_key=preset.source_builtin_key,
        created_at=preset.created_at,
        updated_at=preset.updated_at,
        archived_at=preset.archived_at,
    )


@router.get("/presets", response_model=list[PresetResponse])
async def list_presets(
    include_archived: bool = False,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[PresetResponse]:
    """Hazir presetleri ve organizasyonun kendi (varsayilan olarak aktif) presetlerini listeler."""

    org_presets = await persona_presets.list_org_presets(
        session, organization_id, include_archived=include_archived
    )
    await session.commit()

    return [_builtin_to_response(b) for b in personas.list_builtin_presets()] + [
        _org_preset_to_response(p) for p in org_presets
    ]


@router.get("/presets/{preset_id}", response_model=PresetResponse)
async def get_preset(
    preset_id: str,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    if personas.is_builtin_preset_id(preset_id):
        try:
            return _builtin_to_response(personas.get_builtin_preset(preset_id))
        except personas.PersonaValidationError as exc:
            raise HTTPException(status_code=404, detail="Preset bulunamadi") from exc

    try:
        preset = await persona_presets.get_owned_preset(session, organization_id, uuid.UUID(preset_id))
    except (ValueError, persona_presets.PersonaPresetNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Preset bulunamadi") from exc
    await session.commit()
    return _org_preset_to_response(preset)


class CreatePresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    distribution: dict


@router.post("/presets", response_model=PresetResponse, status_code=201)
async def create_preset(
    body: CreatePresetRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    try:
        preset = await persona_presets.create_preset(
            session,
            principal.organization_id,
            name=body.name,
            description=body.description,
            distribution=body.distribution,
        )
    except personas.PersonaValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(preset)
    return _org_preset_to_response(preset)


class UpdatePresetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    distribution: dict | None = None


@router.patch("/presets/{preset_id}", response_model=PresetResponse)
async def update_preset(
    preset_id: uuid.UUID,
    body: UpdatePresetRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    try:
        preset = await persona_presets.get_owned_preset(session, principal.organization_id, preset_id)
    except persona_presets.PersonaPresetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Preset bulunamadi") from exc

    try:
        preset = await persona_presets.update_preset(
            session,
            preset,
            name=body.name,
            description=body.description,
            distribution=body.distribution,
        )
    except persona_presets.PersonaPresetArchivedError as exc:
        raise HTTPException(status_code=409, detail="Arsivlenmis bir preset guncellenemez") from exc
    except personas.PersonaValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(preset)
    return _org_preset_to_response(preset)


class CopyPresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


@router.post("/presets/{preset_id}/copy", response_model=PresetResponse, status_code=201)
async def copy_preset(
    preset_id: str,
    body: CopyPresetRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    """Hazir bir preseti ya da organizasyonun kendi bir presetini yeni, duzenlenebilir bir kopyaya cevirir."""

    try:
        preset = await persona_presets.copy_preset(
            session,
            principal.organization_id,
            source_preset_id=preset_id,
            new_name=body.name,
            new_description=body.description,
        )
    except personas.PersonaValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, persona_presets.PersonaPresetNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Kaynak preset bulunamadi") from exc

    await session.commit()
    await session.refresh(preset)
    return _org_preset_to_response(preset)


@router.post("/presets/{preset_id}/archive", response_model=PresetResponse)
async def archive_preset(
    preset_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> PresetResponse:
    try:
        preset = await persona_presets.get_owned_preset(session, principal.organization_id, preset_id)
    except persona_presets.PersonaPresetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Preset bulunamadi") from exc

    preset = await persona_presets.archive_preset(session, preset)
    await session.commit()
    await session.refresh(preset)
    return _org_preset_to_response(preset)


# --- Deterministik ornekleme onizlemesi ---------------------------------------


class SamplePreviewRequest(BaseModel):
    persona_count: int
    preset_id: str | None = None
    distribution: dict | None = None
    seed: int | None = None


class CohortSegmentResponse(BaseModel):
    key: str
    label: str
    dimension_values: dict
    count: int
    share: float


class SamplePreviewResponse(BaseModel):
    generator_version: str
    deterministic_seed: int
    distribution_snapshot: dict
    segments: list[CohortSegmentResponse]
    total_count: int
    sample_hash: str


@router.post("/sample-preview", response_model=SamplePreviewResponse)
async def sample_preview(
    body: SamplePreviewRequest,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> SamplePreviewResponse:
    """Bir dagilim (dogrudan ya da bir preset uzerinden) icin deterministik cohort ozetini onizler.

    Sihirbazin persona adimi, secilen preset/ozel dagilim degistikce bu
    uc noktayi cagirarak dagilim ozetini (segment kirilimi) canli gosterir.
    """

    if (body.preset_id is None) == (body.distribution is None):
        raise HTTPException(
            status_code=400, detail="Tam olarak biri belirtilmelidir: preset_id veya distribution"
        )

    if body.preset_id is not None:
        try:
            distribution = await persona_presets.resolve_distribution(
                session, organization_id, body.preset_id
            )
        except (
            ValueError,
            persona_presets.PersonaPresetNotFoundError,
            personas.PersonaValidationError,
        ) as exc:
            raise HTTPException(status_code=404, detail="Preset bulunamadi") from exc
    else:
        distribution = body.distribution or {}

    seed = body.seed if body.seed is not None else uuid.uuid4().int & ((1 << 63) - 1)

    try:
        result = personas.sample_cohorts(distribution, body.persona_count, seed)
    except personas.PersonaValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()

    return SamplePreviewResponse(
        generator_version=result.generator_version,
        deterministic_seed=result.deterministic_seed,
        distribution_snapshot=result.distribution_snapshot,
        segments=[
            CohortSegmentResponse(
                key=s.key, label=s.label, dimension_values=s.dimension_values, count=s.count, share=s.share
            )
            for s in result.segments
        ],
        total_count=result.total_count,
        sample_hash=result.sample_hash,
    )
