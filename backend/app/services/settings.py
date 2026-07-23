"""Kullanici tercihleri ve organizasyon varsayilanlari icin is mantigi.

Iki ayri "get or create" kaynak vardir (`UserPreferences`, `OrganizationSettings`);
her ikisi de `app.services.auth.register_organization_and_owner`'daki
"ilk erisimde olustur" desenini izler (bkz. `list_free_entitlements`). Guncelleme
fonksiyonlari yalnizca gercekten degisen alanlari yazar (diff-based) ki ayni
degerin yeniden gonderilmesi ne bir DB yazisi ne de bir audit log satiri
uretsin.
"""

import uuid
import zoneinfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import OrganizationSettings, UserPreferences
from app.models.tests import PersonaPresetStatus
from app.services import module_catalog, persona_presets, personas

ALLOWED_THEMES = ("system", "light", "dark")
ALLOWED_LANGUAGES = ("tr",)
ALLOWED_CURRENCIES = ("TRY", "USD", "EUR")
DEVICE_PROFILES = ("desktop", "mobile", "tablet")

MIN_PERSONA_COUNT = 100
MAX_PERSONA_COUNT = 50_000

_AVAILABLE_TIMEZONES = zoneinfo.available_timezones()


class SettingsValidationError(ValueError):
    """Ayar alanlarindan biri gecersiz oldugunda (400) firlatilir."""


# --- Kullanici tercihleri ----------------------------------------------------


async def get_or_create_user_preferences(session: AsyncSession, user_id: uuid.UUID) -> UserPreferences:
    """Ilk erisimde olusturur; iki es zamanli istek AYNI satiri ilk kez
    olusturmaya calisirsa (TOCTOU - `SELECT` ikisinde de `None` doner) ikinci
    `INSERT`, birincil anahtar cakismasi (`IntegrityError`) ile basarisiz
    olur. Bu, cagirilmadan once acikca bir SAVEPOINT (`begin_nested`) icinde
    denenir - cakisma olursa yalnizca bu SAVEPOINT geri alinir (disaridaki
    cagiranin transaction'i ETKILENMEZ) ve satir, DIGER istegin zaten
    olusturdugu haliyle GUVENLE yeniden okunur; boylece bu fonksiyon HICBIR
    zaman kullaniciya 500 olarak sizan bir IntegrityError firlatmaz."""

    prefs = await session.get(UserPreferences, user_id)
    if prefs is not None:
        return prefs
    try:
        async with session.begin_nested():
            prefs = UserPreferences(user_id=user_id)
            session.add(prefs)
            await session.flush()
    except IntegrityError:
        prefs = await session.get(UserPreferences, user_id)
        if prefs is None:
            raise
    return prefs


_UNSET = object()


async def update_user_preferences(
    session: AsyncSession,
    prefs: UserPreferences,
    *,
    language: str | None = _UNSET,
    timezone: str | None = _UNSET,
    theme: str | None = _UNSET,
    compact_view: bool | None = _UNSET,
    notify_simulation_completed: bool | None = _UNSET,
    notify_simulation_failed: bool | None = _UNSET,
    notify_report_ready: bool | None = _UNSET,
    notify_low_chip_balance: bool | None = _UNSET,
    low_chip_balance_threshold: int | None = _UNSET,
) -> list[str]:
    """Yalnizca gonderilen (ve mevcut degerden farkli) alanlari gunceller.

    Degisen alan adlarinin listesini dondurur; bos liste "hicbir yan etki
    olusmadi" anlamina gelir (cagiran taraf bu durumda flush/audit atlamalidir).
    """

    changed: list[str] = []

    if language is not _UNSET and language is not None:
        if language not in ALLOWED_LANGUAGES:
            raise SettingsValidationError(f"Gecersiz dil: {language}")
        if prefs.language != language:
            prefs.language = language
            changed.append("language")

    if timezone is not _UNSET and timezone is not None:
        if timezone not in _AVAILABLE_TIMEZONES:
            raise SettingsValidationError(f"Gecersiz saat dilimi: {timezone}")
        if prefs.timezone != timezone:
            prefs.timezone = timezone
            changed.append("timezone")

    if theme is not _UNSET and theme is not None:
        if theme not in ALLOWED_THEMES:
            raise SettingsValidationError(f"Gecersiz tema: {theme}")
        if prefs.theme != theme:
            prefs.theme = theme
            changed.append("theme")

    if compact_view is not _UNSET and compact_view is not None and prefs.compact_view != compact_view:
        prefs.compact_view = compact_view
        changed.append("compact_view")

    for field, value in (
        ("notify_simulation_completed", notify_simulation_completed),
        ("notify_simulation_failed", notify_simulation_failed),
        ("notify_report_ready", notify_report_ready),
        ("notify_low_chip_balance", notify_low_chip_balance),
    ):
        if value is not _UNSET and value is not None and getattr(prefs, field) != value:
            setattr(prefs, field, value)
            changed.append(field)

    if low_chip_balance_threshold is not _UNSET and low_chip_balance_threshold is not None:
        if low_chip_balance_threshold < 0:
            raise SettingsValidationError("low_chip_balance_threshold negatif olamaz")
        if prefs.low_chip_balance_threshold != low_chip_balance_threshold:
            prefs.low_chip_balance_threshold = low_chip_balance_threshold
            changed.append("low_chip_balance_threshold")

    if changed:
        await session.flush()

    return changed


# --- Organizasyon ayarlari ----------------------------------------------------


async def get_or_create_organization_settings(
    session: AsyncSession, organization_id: uuid.UUID
) -> OrganizationSettings:
    """Ilk erisimde olusturur - ayni TOCTOU-guvenli SAVEPOINT deseni icin bkz.
    `get_or_create_user_preferences` docstring'i. Bu, gercek bir production
    hatasindan (bkz. sonuc raporu - React StrictMode'un dev'de effect'leri
    iki kez calistirmasi, es zamanli iki `POST /api/tests/drafts` isteginin
    ayni organizasyon icin bu satiri ayni anda olusturmaya calismasina ve
    500 Internal Server Error'a yol acmasindan) kaynaklanan bir duzeltmedir."""

    settings_row = await session.get(OrganizationSettings, organization_id)
    if settings_row is not None:
        return settings_row
    try:
        async with session.begin_nested():
            settings_row = OrganizationSettings(organization_id=organization_id)
            session.add(settings_row)
            await session.flush()
    except IntegrityError:
        settings_row = await session.get(OrganizationSettings, organization_id)
        if settings_row is None:
            raise
    return settings_row


async def _validate_persona_preset_id(
    session: AsyncSession, organization_id: uuid.UUID, preset_id: str
) -> None:
    if personas.is_builtin_preset_id(preset_id):
        try:
            personas.get_builtin_preset(preset_id)
        except personas.PersonaValidationError as exc:
            raise SettingsValidationError(f"Bilinmeyen persona preseti: {preset_id}") from exc
        return
    try:
        parsed = uuid.UUID(preset_id)
    except ValueError as exc:
        raise SettingsValidationError(f"Gecersiz persona preseti kimligi: {preset_id}") from exc
    try:
        preset = await persona_presets.get_owned_preset(session, organization_id, parsed)
    except persona_presets.PersonaPresetNotFoundError as exc:
        raise SettingsValidationError(f"Persona preseti bulunamadi: {preset_id}") from exc
    if preset.status != PersonaPresetStatus.ACTIVE:
        raise SettingsValidationError(f"Persona preseti arsivlenmis: {preset_id}")


async def update_organization_settings(
    session: AsyncSession,
    org_settings: OrganizationSettings,
    organization_id: uuid.UUID,
    *,
    currency: str | None = _UNSET,
    default_persona_count: int | None = _UNSET,
    default_persona_preset_id: str | None = _UNSET,
    default_device_profile: str | None = _UNSET,
    default_modules: list[str] | None = _UNSET,
    default_target_audience: str | None = _UNSET,
) -> list[str]:
    """Yalnizca gonderilen (ve mevcut degerden farkli) alanlari gunceller."""

    changed: list[str] = []

    if currency is not _UNSET and currency is not None:
        if currency not in ALLOWED_CURRENCIES:
            raise SettingsValidationError(f"Gecersiz para birimi: {currency}")
        if org_settings.currency != currency:
            org_settings.currency = currency
            changed.append("currency")

    if default_persona_count is not _UNSET and default_persona_count is not None:
        if not (MIN_PERSONA_COUNT <= default_persona_count <= MAX_PERSONA_COUNT):
            raise SettingsValidationError(
                f"default_persona_count {MIN_PERSONA_COUNT} ile {MAX_PERSONA_COUNT} arasinda olmalidir"
            )
        if org_settings.default_persona_count != default_persona_count:
            org_settings.default_persona_count = default_persona_count
            changed.append("default_persona_count")

    # Not: bu iki alan icin bos metin (`""`) kasitli olarak "temizle" (NULL)
    # anlamina gelir; `None`/alan hic gonderilmemis "dokunma" anlamina gelir
    # (bkz. routers/personas.py `UpdatePresetRequest` ile ayni "None=dokunma"
    # kurali - burada ayrica bir "temizle" sinyaline de ihtiyac oldugu icin
    # bos metin kullanilir).
    if default_persona_preset_id is not _UNSET and default_persona_preset_id is not None:
        normalized = default_persona_preset_id.strip() or None
        if normalized is not None:
            await _validate_persona_preset_id(session, organization_id, normalized)
        if org_settings.default_persona_preset_id != normalized:
            org_settings.default_persona_preset_id = normalized
            changed.append("default_persona_preset_id")

    if default_device_profile is not _UNSET and default_device_profile is not None:
        normalized_profile = default_device_profile.strip() or None
        if normalized_profile is not None and normalized_profile not in DEVICE_PROFILES:
            raise SettingsValidationError(f"Gecersiz cihaz profili: {normalized_profile}")
        if org_settings.default_device_profile != normalized_profile:
            org_settings.default_device_profile = normalized_profile
            changed.append("default_device_profile")

    if default_modules is not _UNSET and default_modules is not None:
        selectable = set(module_catalog.get_selectable_wizard_module_keys())
        unknown = set(default_modules) - selectable
        if unknown:
            raise SettingsValidationError(f"Bilinmeyen analiz modulu: {', '.join(sorted(unknown))}")
        if list(org_settings.default_modules) != list(default_modules):
            org_settings.default_modules = list(default_modules)
            changed.append("default_modules")

    if default_target_audience is not _UNSET and default_target_audience is not None:
        normalized_audience = default_target_audience.strip() or None
        if org_settings.default_target_audience != normalized_audience:
            org_settings.default_target_audience = normalized_audience
            changed.append("default_target_audience")

    if changed:
        await session.flush()

    return changed


async def compute_effective_test_defaults(
    session: AsyncSession, organization_id: uuid.UUID, org_settings: OrganizationSettings
) -> tuple[dict, list[str]]:
    """Saklanan varsayilanlari guncel kataloglara karsi yeniden dogrular.

    Artik gecerli olmayan (arsivlenmis preset, katalogdan kalkmis modul,
    bilinmeyen cihaz profili) degerleri sessizce dusurur ve anlasilir bir
    Turkce uyari listesiyle birlikte "etkin" (guvenli) varsayilan kumesini
    dondurur. Hem GET yaniti hem de yeni taslak olusturma bu tek fonksiyonu
    kullanir.
    """

    warnings: list[str] = []
    effective: dict = {
        "persona_count": org_settings.default_persona_count,
        "persona_preset_id": None,
        "device_profile": None,
        "modules": [],
        "target_audience": org_settings.default_target_audience,
    }

    if org_settings.default_persona_preset_id:
        try:
            await _validate_persona_preset_id(
                session, organization_id, org_settings.default_persona_preset_id
            )
            effective["persona_preset_id"] = org_settings.default_persona_preset_id
        except SettingsValidationError:
            warnings.append("Varsayilan persona preseti artik mevcut degil; guvenli varsayilana donuldu.")

    if org_settings.default_device_profile:
        if org_settings.default_device_profile in DEVICE_PROFILES:
            effective["device_profile"] = org_settings.default_device_profile
        else:
            warnings.append("Varsayilan cihaz profili artik gecerli degil; guvenli varsayilana donuldu.")

    if org_settings.default_modules:
        selectable = set(module_catalog.get_selectable_wizard_module_keys())
        valid_modules = [m for m in org_settings.default_modules if m in selectable]
        if len(valid_modules) != len(org_settings.default_modules):
            warnings.append(
                "Varsayilan analiz modullerinden bir veya daha fazlasi artik secilebilir degil; "
                "guvenli varsayilana donuldu."
            )
        effective["modules"] = valid_modules

    return effective, warnings


async def build_initial_wizard_payload(session: AsyncSession, organization_id: uuid.UUID) -> dict:
    """Yeni bir sihirbaz taslaginin baslangic `payload`'ini organizasyon varsayilanlarindan kurar."""

    org_settings = await get_or_create_organization_settings(session, organization_id)
    effective, _warnings = await compute_effective_test_defaults(session, organization_id, org_settings)

    payload: dict = {"persona_count": effective["persona_count"]}
    if effective["persona_preset_id"]:
        payload["persona_preset_id"] = effective["persona_preset_id"]
    if effective["device_profile"]:
        payload["device_profile"] = effective["device_profile"]
    if effective["modules"]:
        payload["modules"] = effective["modules"]
    if effective["target_audience"]:
        payload["target_audience"] = effective["target_audience"]
    return payload


__all__ = [
    "ALLOWED_THEMES",
    "ALLOWED_LANGUAGES",
    "ALLOWED_CURRENCIES",
    "DEVICE_PROFILES",
    "MIN_PERSONA_COUNT",
    "MAX_PERSONA_COUNT",
    "SettingsValidationError",
    "get_or_create_user_preferences",
    "update_user_preferences",
    "get_or_create_organization_settings",
    "update_organization_settings",
    "compute_effective_test_defaults",
    "build_initial_wizard_payload",
]
