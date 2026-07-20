"""Sirkete ozel persona preset yonetimi: olusturma, kopyalama, arsivleme.

Hazir (builtin) presetler bu servisin disinda, sabit bir Python katalogunda
tanimlidir (bkz. `app.services.personas.BUILTIN_PRESETS`) ve hicbir tabloda
satir olarak tutulmaz. Bir organizasyon, hazir bir preseti veya kendi
mevcut bir preseti "kopyalayarak" duzenlenebilir, o organizasyona ait yeni
bir `PersonaPreset` satiri olusturabilir; sonrasinda bu satir normal CRUD
(guncelleme/arsivleme) akisina tabidir.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tests import PersonaPreset, PersonaPresetStatus
from app.services import personas


class PersonaPresetNotFoundError(Exception):
    """Preset bulunamadi veya cagiran organizasyona ait degil (404)."""


class PersonaPresetArchivedError(Exception):
    """Arsivlenmis bir preset uzerinde yazma islemi denendi (409)."""


async def list_org_presets(
    session: AsyncSession, organization_id: uuid.UUID, *, include_archived: bool = False
) -> list[PersonaPreset]:
    query = select(PersonaPreset).where(PersonaPreset.organization_id == organization_id)
    if not include_archived:
        query = query.where(PersonaPreset.status == PersonaPresetStatus.ACTIVE)
    query = query.order_by(PersonaPreset.created_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_owned_preset(
    session: AsyncSession, organization_id: uuid.UUID, preset_id: uuid.UUID
) -> PersonaPreset:
    preset = await session.get(PersonaPreset, preset_id)
    if preset is None or preset.organization_id != organization_id:
        raise PersonaPresetNotFoundError(str(preset_id))
    return preset


async def create_preset(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    name: str,
    description: str | None,
    distribution: dict,
) -> PersonaPreset:
    personas.validate_distribution(distribution)

    preset = PersonaPreset(
        organization_id=organization_id,
        name=name,
        description=description,
        config=distribution,
        status=PersonaPresetStatus.ACTIVE,
    )
    session.add(preset)
    await session.flush()
    return preset


async def update_preset(
    session: AsyncSession,
    preset: PersonaPreset,
    *,
    name: str | None = None,
    description: str | None = None,
    distribution: dict | None = None,
) -> PersonaPreset:
    if preset.status == PersonaPresetStatus.ARCHIVED:
        raise PersonaPresetArchivedError(str(preset.id))

    if name is not None:
        preset.name = name
    if description is not None:
        preset.description = description
    if distribution is not None:
        personas.validate_distribution(distribution)
        preset.config = distribution

    await session.flush()
    return preset


async def resolve_distribution(session: AsyncSession, organization_id: uuid.UUID, preset_id: str) -> dict:
    """Bir preset kimliginden (builtin ya da organizasyona ait) dagilimi cozer."""

    if personas.is_builtin_preset_id(preset_id):
        return dict(personas.get_builtin_preset(preset_id).distribution)

    preset = await get_owned_preset(session, organization_id, uuid.UUID(preset_id))
    return dict(preset.config)


async def copy_preset(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    source_preset_id: str,
    new_name: str,
    new_description: str | None = None,
) -> PersonaPreset:
    """Hazir (builtin) veya organizasyona ait bir preseti yeni, duzenlenebilir bir satira kopyalar."""

    source_builtin_key: str | None = None

    if personas.is_builtin_preset_id(source_preset_id):
        builtin = personas.get_builtin_preset(source_preset_id)
        distribution = dict(builtin.distribution)
        source_builtin_key = builtin.key
        source_description: str | None = builtin.description
    else:
        source = await get_owned_preset(session, organization_id, uuid.UUID(source_preset_id))
        distribution = dict(source.config)
        source_builtin_key = source.source_builtin_key
        source_description = source.description

    preset = PersonaPreset(
        organization_id=organization_id,
        name=new_name,
        description=new_description if new_description is not None else source_description,
        config=distribution,
        status=PersonaPresetStatus.ACTIVE,
        source_builtin_key=source_builtin_key,
    )
    session.add(preset)
    await session.flush()
    return preset


async def archive_preset(session: AsyncSession, preset: PersonaPreset) -> PersonaPreset:
    """Preseti arsivler (fiziksel silme yoktur). Idempotenttir."""

    if preset.status != PersonaPresetStatus.ARCHIVED:
        preset.status = PersonaPresetStatus.ARCHIVED
        preset.archived_at = datetime.now(UTC)
        await session.flush()
    return preset


__all__ = [
    "PersonaPresetArchivedError",
    "PersonaPresetNotFoundError",
    "archive_preset",
    "copy_preset",
    "create_preset",
    "get_owned_preset",
    "list_org_presets",
    "resolve_distribution",
    "update_preset",
]
