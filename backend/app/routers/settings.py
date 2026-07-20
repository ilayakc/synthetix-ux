import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_current_principal, verify_csrf
from app.models.audit import AuditLog
from app.models.tenancy import Organization, User
from app.services import settings as settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Sirket adi/para birimi: yalnizca owner/admin. Test varsayilanlari: sihirbaz
# taslagi olusturabilen herkes (bkz. app.routers.test_wizard.WRITE_ROLES) -
# analistler zaten taslak olusturabildigi icin, bu taslaklarin baslangic
# degerlerini de belirleyebilmeleri tutarlidir.
COMPANY_EDIT_ROLES = ("admin", "owner")
DEFAULTS_EDIT_ROLES = ("analyst", "admin", "owner")


# --- /api/settings/me ---------------------------------------------------------


class MeSettingsResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    language: str
    timezone: str
    theme: str
    compact_view: bool
    notify_simulation_completed: bool
    notify_simulation_failed: bool
    notify_report_ready: bool
    notify_low_chip_balance: bool
    low_chip_balance_threshold: int | None
    updated_at: datetime


async def _me_response(session: AsyncSession, principal: Principal) -> MeSettingsResponse:
    prefs = await settings_service.get_or_create_user_preferences(session, principal.user_id)
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    return MeSettingsResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        language=prefs.language,
        timezone=prefs.timezone,
        theme=prefs.theme,
        compact_view=prefs.compact_view,
        notify_simulation_completed=prefs.notify_simulation_completed,
        notify_simulation_failed=prefs.notify_simulation_failed,
        notify_report_ready=prefs.notify_report_ready,
        notify_low_chip_balance=prefs.notify_low_chip_balance,
        low_chip_balance_threshold=prefs.low_chip_balance_threshold,
        updated_at=prefs.updated_at,
    )


@router.get("/me", response_model=MeSettingsResponse)
async def get_my_settings(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> MeSettingsResponse:
    response = await _me_response(session, principal)
    await session.commit()
    return response


class PatchMeSettingsRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    language: str | None = None
    timezone: str | None = None
    theme: str | None = None
    compact_view: bool | None = None
    notify_simulation_completed: bool | None = None
    notify_simulation_failed: bool | None = None
    notify_report_ready: bool | None = None
    notify_low_chip_balance: bool | None = None
    low_chip_balance_threshold: int | None = None


@router.patch("/me", response_model=MeSettingsResponse)
async def patch_my_settings(
    body: PatchMeSettingsRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    _csrf: None = Depends(verify_csrf),
) -> MeSettingsResponse:
    prefs = await settings_service.get_or_create_user_preferences(session, principal.user_id)
    user = await session.get(User, principal.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Kullanici bulunamadi")

    changed: list[str] = []
    if body.display_name is not None and user.display_name != body.display_name:
        user.display_name = body.display_name
        changed.append("display_name")

    try:
        changed += await settings_service.update_user_preferences(
            session,
            prefs,
            language=body.language,
            timezone=body.timezone,
            theme=body.theme,
            compact_view=body.compact_view,
            notify_simulation_completed=body.notify_simulation_completed,
            notify_simulation_failed=body.notify_simulation_failed,
            notify_report_ready=body.notify_report_ready,
            notify_low_chip_balance=body.notify_low_chip_balance,
            low_chip_balance_threshold=body.low_chip_balance_threshold,
        )
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if changed:
        session.add(
            AuditLog(
                organization_id=principal.organization_id,
                actor_user_id=principal.user_id,
                action="user_preferences_updated",
                entity_type="user_preferences",
                entity_id=principal.user_id,
                entry_metadata={"changed_fields": changed},
            )
        )

    await session.commit()
    await session.refresh(prefs)
    await session.refresh(user)
    return MeSettingsResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        language=prefs.language,
        timezone=prefs.timezone,
        theme=prefs.theme,
        compact_view=prefs.compact_view,
        notify_simulation_completed=prefs.notify_simulation_completed,
        notify_simulation_failed=prefs.notify_simulation_failed,
        notify_report_ready=prefs.notify_report_ready,
        notify_low_chip_balance=prefs.notify_low_chip_balance,
        low_chip_balance_threshold=prefs.low_chip_balance_threshold,
        updated_at=prefs.updated_at,
    )


# --- /api/settings/organization -----------------------------------------------


class OrganizationSettingsResponse(BaseModel):
    organization_id: uuid.UUID
    name: str
    slug: str
    role: str
    created_at: datetime
    currency: str
    default_persona_count: int
    default_persona_preset_id: str | None
    default_device_profile: str | None
    default_modules: list[str]
    default_target_audience: str | None
    effective_default_persona_preset_id: str | None
    effective_default_device_profile: str | None
    effective_default_modules: list[str]
    warnings: list[str]
    can_edit_company: bool
    can_edit_defaults: bool


async def _organization_response(
    session: AsyncSession,
    principal: Principal,
    *,
    org: Organization | None = None,
    org_settings=None,
) -> OrganizationSettingsResponse:
    if org is None:
        org = await session.get(Organization, principal.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organizasyon bulunamadi")

    if org_settings is None:
        org_settings = await settings_service.get_or_create_organization_settings(
            session, principal.organization_id
        )
    effective, warnings = await settings_service.compute_effective_test_defaults(
        session, principal.organization_id, org_settings
    )

    return OrganizationSettingsResponse(
        organization_id=org.id,
        name=org.name,
        slug=org.slug,
        role=principal.role,
        created_at=org.created_at,
        currency=org_settings.currency,
        default_persona_count=org_settings.default_persona_count,
        default_persona_preset_id=org_settings.default_persona_preset_id,
        default_device_profile=org_settings.default_device_profile,
        default_modules=list(org_settings.default_modules),
        default_target_audience=org_settings.default_target_audience,
        effective_default_persona_preset_id=effective["persona_preset_id"],
        effective_default_device_profile=effective["device_profile"],
        effective_default_modules=effective["modules"],
        warnings=warnings,
        can_edit_company=principal.role in COMPANY_EDIT_ROLES,
        can_edit_defaults=principal.role in DEFAULTS_EDIT_ROLES,
    )


@router.get("/organization", response_model=OrganizationSettingsResponse)
async def get_organization_settings(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> OrganizationSettingsResponse:
    response = await _organization_response(session, principal)
    await session.commit()
    return response


class PatchOrganizationSettingsRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = None
    default_persona_count: int | None = None
    default_persona_preset_id: str | None = None
    default_device_profile: str | None = None
    default_modules: list[str] | None = None
    default_target_audience: str | None = None


@router.patch("/organization", response_model=OrganizationSettingsResponse)
async def patch_organization_settings(
    body: PatchOrganizationSettingsRequest,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
    _csrf: None = Depends(verify_csrf),
) -> OrganizationSettingsResponse:
    company_fields_touched = body.name is not None or body.currency is not None
    defaults_fields_touched = any(
        value is not None
        for value in (
            body.default_persona_count,
            body.default_persona_preset_id,
            body.default_device_profile,
            body.default_modules,
            body.default_target_audience,
        )
    )

    if company_fields_touched and principal.role not in COMPANY_EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Bu islem icin yetkiniz yok")
    if defaults_fields_touched and principal.role not in DEFAULTS_EDIT_ROLES:
        raise HTTPException(status_code=403, detail="Bu islem icin yetkiniz yok")

    org = await session.get(Organization, principal.organization_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organizasyon bulunamadi")

    changed: list[str] = []
    if body.name is not None and org.name != body.name:
        org.name = body.name
        changed.append("name")

    org_settings = await settings_service.get_or_create_organization_settings(
        session, principal.organization_id
    )
    try:
        changed += await settings_service.update_organization_settings(
            session,
            org_settings,
            principal.organization_id,
            currency=body.currency,
            default_persona_count=body.default_persona_count,
            default_persona_preset_id=body.default_persona_preset_id,
            default_device_profile=body.default_device_profile,
            default_modules=body.default_modules,
            default_target_audience=body.default_target_audience,
        )
    except settings_service.SettingsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if changed:
        session.add(
            AuditLog(
                organization_id=principal.organization_id,
                actor_user_id=principal.user_id,
                action="organization_settings_updated",
                entity_type="organization",
                entity_id=principal.organization_id,
                entry_metadata={"changed_fields": changed},
            )
        )

    await session.commit()
    await session.refresh(org)
    await session.refresh(org_settings)
    return await _organization_response(session, principal, org=org, org_settings=org_settings)
