"""Yeni test kurulum sihirbazi: taslak dogrulama ve baslatma (launch) is mantigi.

Taslak, bes adim arasinda kaybolmamasi icin organizasyona bagli tek bir
satirda (`TestWizardDraft.payload`) tek bir JSON belge olarak tutulur; her
PATCH cagrisi yalnizca gonderilen alanlari dogrulayip mevcut payload'a
birlestirir (merge), boylece geri/ileri gidilmesi veya sayfa yenilenmesi
veriyi kaybetmez.

Fiyatlandirma/teklif (quote) burada yeniden hesaplanmaz; her zaman
`app.services.quotes.build_quote` (Prompt 3 servisi) cagrilir.
"""

import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.simulations import SimulationRun, SimulationStatus
from app.models.test_wizard import TestWizardDraft, TestWizardDraftStatus
from app.models.tests import TestDefinition, TestVariant
from app.services import chip_ledger, module_catalog, persona_presets, personas, quotes
from app.services import entitlements as entitlements_service
from app.services.exceptions import InsufficientChipBalanceError
from app.services.pricing import FEATURE_ACCESSIBILITY_PRECHECK, FEATURE_BASIC_UX_TEST

# --- Test turleri (bkz. docs/product-rules.md) ------------------------------
EXISTING_SITE_BASIC_UX = "existing_site_basic_ux"
AB_COMPARISON = "ab_comparison"
ACCESSIBILITY_PRECHECK = "accessibility_precheck"
WIZARD_TEST_TYPES = (EXISTING_SITE_BASIC_UX, AB_COMPARISON, ACCESSIBILITY_PRECHECK)

# Sihirbazdaki test turu, teklif (quote) servisinin bildigi iki
# feature_key'den birine eslenir: A/B karsilastirmasi da persona sayisina
# dayali oldugu icin temel UX testi ile ayni ucretsiz hakki/fiyatlandirmayi
# paylasir (docs/product-rules.md yalnizca bu iki ucretsiz hakki tanimlar).
QUOTE_TEST_TYPE_BY_WIZARD_TYPE = {
    EXISTING_SITE_BASIC_UX: FEATURE_BASIC_UX_TEST,
    AB_COMPARISON: FEATURE_BASIC_UX_TEST,
    ACCESSIBILITY_PRECHECK: FEATURE_ACCESSIBILITY_PRECHECK,
}

# Sihirbazda secilebilen (aktif + `selectable_in_wizard`) gelismis modul
# anahtarlari, tek kaynak olan analiz modulu katalogundan turetilir (bkz.
# app.services.module_catalog) - burada ayrica hardcode edilmez.
ANALYSIS_MODULES = module_catalog.get_selectable_wizard_module_keys()

MIN_PERSONA_COUNT = 100
MAX_PERSONA_COUNT = 50_000

# Ayarlar ekraninin "Test Varsayilanlari" sekmesiyle paylasilan sabit liste
# (bkz. app.services.settings.DEVICE_PROFILES) - burada tekrar tanimlanir
# cunku bu modul, `app.services.settings`'e bagimlilik eklemeden (dongusel
# import riski olmadan) bagimsiz calisabilmelidir; iki liste kasitli olarak
# ayni degerlere sahiptir.
DEVICE_PROFILES = ("desktop", "mobile", "tablet")

PATCHABLE_FIELDS = {
    "project_id",
    "name",
    "target_task",
    "test_type",
    "current_url",
    "new_url",
    "persona_count",
    "target_audience",
    "persona_preset_id",
    "persona_distribution",
    "modules",
    "authorization_confirmed",
    "device_profile",
}

ENGINE_NOT_AVAILABLE_MESSAGE = (
    "Is 'queued' durumuna alindi; heuristic sentetik simulasyon motoru "
    "tarafindan islenecek (bkz. Simulasyonlar sayfasi). Sonuclar kalibre "
    "edilmemis sentetik tahminlerdir, gercek kullanici verisi degildir."
)


class DraftValidationError(ValueError):
    """Taslak alanlarindan biri gecersiz oldugunda (400) firlatilir."""


def _validate_url_syntax(value: str, field: str) -> None:
    """Yalnizca sozdizimi dogrulanir; URL hicbir sekilde ziyaret edilmez."""

    if not isinstance(value, str):
        raise DraftValidationError(f"'{field}' bir metin olmalidir")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DraftValidationError(f"'{field}' gecerli bir http(s) URL olmalidir")


def validate_patch_fields(patch: dict) -> None:
    """PATCH govdesinde gonderilen alanlari (yalnizca gonderilenleri) dogrular."""

    unknown = set(patch) - PATCHABLE_FIELDS
    if unknown:
        raise DraftValidationError(f"Bilinmeyen alan(lar): {', '.join(sorted(unknown))}")

    if "test_type" in patch and patch["test_type"] not in WIZARD_TEST_TYPES:
        raise DraftValidationError(f"Gecersiz test_type: {patch['test_type']}")

    if "persona_count" in patch:
        count = patch["persona_count"]
        if not isinstance(count, int) or isinstance(count, bool):
            raise DraftValidationError("persona_count bir tam sayi olmalidir")
        if not (MIN_PERSONA_COUNT <= count <= MAX_PERSONA_COUNT):
            raise DraftValidationError(
                f"persona_count {MIN_PERSONA_COUNT} ile {MAX_PERSONA_COUNT} arasinda olmalidir"
            )

    for field in ("current_url", "new_url"):
        if field in patch and patch[field] is not None:
            _validate_url_syntax(patch[field], field)

    if "modules" in patch:
        modules = patch["modules"]
        if not isinstance(modules, list) or not all(isinstance(m, str) for m in modules):
            raise DraftValidationError("modules bir metin listesi olmalidir")
        unknown_modules = set(modules) - set(ANALYSIS_MODULES)
        if unknown_modules:
            raise DraftValidationError(f"Bilinmeyen analiz modulu: {', '.join(sorted(unknown_modules))}")

    if "authorization_confirmed" in patch and not isinstance(patch["authorization_confirmed"], bool):
        raise DraftValidationError("authorization_confirmed bir bool olmalidir")

    if "device_profile" in patch and patch["device_profile"] is not None:
        if patch["device_profile"] not in DEVICE_PROFILES:
            raise DraftValidationError(f"Gecersiz device_profile: {patch['device_profile']}")

    if "persona_preset_id" in patch and patch["persona_preset_id"] is not None:
        preset_id = patch["persona_preset_id"]
        if not isinstance(preset_id, str) or not preset_id.strip():
            raise DraftValidationError("persona_preset_id bos olmayan bir metin olmalidir")
        if not personas.is_builtin_preset_id(preset_id):
            try:
                uuid.UUID(preset_id)
            except ValueError as exc:
                raise DraftValidationError("persona_preset_id gecerli bir preset kimligi olmalidir") from exc

    if "persona_distribution" in patch and patch["persona_distribution"] is not None:
        try:
            personas.validate_distribution(patch["persona_distribution"])
        except personas.PersonaValidationError as exc:
            raise DraftValidationError(f"persona_distribution gecersiz: {exc}") from exc

    for field in ("name", "target_task", "target_audience"):
        if field in patch and patch[field] is not None:
            if not isinstance(patch[field], str) or not patch[field].strip():
                raise DraftValidationError(f"'{field}' bos olamaz")

    if "project_id" in patch and patch["project_id"] is not None:
        try:
            uuid.UUID(str(patch["project_id"]))
        except (ValueError, TypeError) as exc:
            raise DraftValidationError("project_id gecerli bir UUID olmalidir") from exc


def merge_payload(existing: dict, patch: dict) -> dict:
    merged = dict(existing)
    merged.update(patch)
    return merged


def missing_fields_for_launch(payload: dict) -> list[str]:
    """Baslatma icin eksik/gecersiz alanlarin listesini dondurur (bos ise hazir demektir)."""

    missing: list[str] = []

    for field in (
        "project_id",
        "name",
        "target_task",
        "test_type",
        "current_url",
        "persona_count",
        "target_audience",
    ):
        if payload.get(field) in (None, ""):
            missing.append(field)

    if payload.get("test_type") == AB_COMPARISON and not payload.get("new_url"):
        missing.append("new_url")

    if payload.get("authorization_confirmed") is not True:
        missing.append("authorization_confirmed")

    return missing


async def build_quote_for_payload(
    session: AsyncSession, organization_id: uuid.UUID, payload: dict
) -> quotes.QuoteResult:
    wizard_test_type = payload.get("test_type")
    if wizard_test_type not in WIZARD_TEST_TYPES:
        raise DraftValidationError(f"Gecersiz test_type: {wizard_test_type}")

    quote_test_type = QUOTE_TEST_TYPE_BY_WIZARD_TYPE[wizard_test_type]
    persona_count = payload.get("persona_count") or 0
    modules = payload.get("modules") or []

    return await quotes.build_quote(
        session,
        organization_id,
        persona_count=persona_count,
        test_type=quote_test_type,
        modules=modules,
    )


@dataclass(frozen=True)
class LaunchResult:
    test_definition_id: uuid.UUID
    simulation_run_ids: tuple[uuid.UUID, ...]
    used_free_entitlement: bool
    reserved_chips: int
    engine_status_message: str = ENGINE_NOT_AVAILABLE_MESSAGE


async def _resolve_persona_sample(
    session: AsyncSession,
    organization_id: uuid.UUID,
    payload: dict,
    deterministic_seed: int,
) -> dict | None:
    """Sihirbazda secilen preset/ozel dagilimdan deterministik bir cohort ozeti kurar.

    Persona secimi bu adimda zorunlu tutulmaz (geriye donuk uyumluluk icin):
    ne `persona_preset_id` ne de `persona_distribution` verilmisse `None`
    dondurulur ve `input_snapshot`'a hicbir persona ornekleme verisi eklenmez.
    """

    distribution: dict | None = None
    if payload.get("persona_preset_id"):
        try:
            distribution = await persona_presets.resolve_distribution(
                session, organization_id, payload["persona_preset_id"]
            )
        except (
            ValueError,
            persona_presets.PersonaPresetNotFoundError,
            personas.PersonaValidationError,
        ) as exc:
            raise DraftValidationError(f"persona_preset_id cozumlenemedi: {exc}") from exc
    elif payload.get("persona_distribution"):
        distribution = payload["persona_distribution"]

    if distribution is None:
        return None

    result = personas.sample_cohorts(distribution, payload["persona_count"], deterministic_seed)
    return {
        "generator_version": result.generator_version,
        "distribution_snapshot": result.distribution_snapshot,
        "segments": [
            {
                "key": s.key,
                "label": s.label,
                "dimension_values": s.dimension_values,
                "count": s.count,
                "share": s.share,
            }
            for s in result.segments
        ],
        "sample_hash": result.sample_hash,
    }


def _variant_specs(payload: dict) -> list[tuple[str, dict]]:
    if payload["test_type"] == AB_COMPARISON:
        return [
            ("Mevcut Tasarim", {"role": "existing", "url": payload["current_url"]}),
            ("Yeni Tasarim", {"role": "new", "url": payload["new_url"]}),
        ]
    return [("Ana Senaryo", {"role": "primary", "url": payload["current_url"]})]


async def launch_draft(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    draft: TestWizardDraft,
) -> LaunchResult:
    """Taslagi baslatir; entitlement/Chip rezervasyonu yapar ve `queued` calistirmalar olusturur.

    Cagiran taraf, bu fonksiyonu cagirmadan once `draft` satirini
    `SELECT ... FOR UPDATE` ile kilitlemis olmalidir (bkz.
    `app.routers.test_wizard`); bu, ayni taslagin cift tiklama/yaris
    durumunda iki kez baslatilmasini engeller. Taslak zaten `launched`
    ise hicbir yeni yan etki uretilmeden onceki sonuc dondurulur.
    """

    if draft.status == TestWizardDraftStatus.LAUNCHED:
        result = draft.launch_result or {}
        return LaunchResult(
            test_definition_id=uuid.UUID(result["test_definition_id"]),
            simulation_run_ids=tuple(uuid.UUID(r) for r in result["simulation_run_ids"]),
            used_free_entitlement=result["used_free_entitlement"],
            reserved_chips=result["reserved_chips"],
        )

    missing = missing_fields_for_launch(draft.payload)
    if missing:
        raise DraftValidationError(
            f"Sihirbaz tamamlanmadan baslatilamaz, eksik/gecersiz alan(lar): {', '.join(missing)}"
        )

    payload = draft.payload
    quote = await build_quote_for_payload(session, organization_id, payload)

    if draft.launch_run_id is None:
        draft.launch_run_id = uuid.uuid4()
        await session.flush()
    run_id = draft.launch_run_id

    used_free_entitlement = quote.free_entitlement_applicable
    reserved_chips = 0
    free_entitlement_feature_key: str | None = None
    chip_reservation_id: uuid.UUID | None = None

    if used_free_entitlement:
        assert quote.free_entitlement_feature_key is not None
        await entitlements_service.reserve_entitlement(
            session, organization_id, quote.free_entitlement_feature_key, run_id
        )
        free_entitlement_feature_key = quote.free_entitlement_feature_key

    # NOT: bu bilerek `elif` degil, bagimsiz bir `if`dir. Ucretsiz hak yalnizca
    # temel test satirini kapsar (bkz. app.services.quotes._quote_basic_ux_test
    # / _quote_accessibility_precheck: `covered_by_free_entitlement=True` olan
    # satirin `chip_cost`'u zaten 0'dir). Sihirbazin 4. adiminda secilen
    # gelismis moduller (`network_device_test` vb.) her zaman Chip gerektirir
    # (bkz. app.services.pricing.advanced_module_chip_costs) ve
    # `quote.required_chips` bu modul maliyetlerini ucretsiz hak durumundan
    # bagimsiz olarak icerir; bu nedenle temel test ucretsiz olsa bile secili
    # modullerin Chip'i AYRICA rezerve edilmelidir - aksi halde modul secimi
    # fiyat teklifinde gorunup gercekte hicbir Chip harcamadan calisirdi.
    if quote.required_chips > 0:
        # InsufficientChipBalanceError burada yakalanmaz: cagiran router bunu
        # 402'ye cevirir ve hicbir kayit olusturulmamis, hicbir rezervasyon
        # yapilmamis olarak (commit edilmemis islem geri alinarak) biter.
        reservation = await chip_ledger.reserve_chips(
            session,
            organization_id,
            quote.required_chips,
            f"Test baslatma: {payload.get('name')}",
            run_id=run_id,
            idempotency_key=f"test-wizard-draft:{draft.id}",
        )
        reserved_chips = quote.required_chips
        chip_reservation_id = reservation.id

    test_definition = TestDefinition(
        organization_id=organization_id,
        project_id=uuid.UUID(str(payload["project_id"])),
        name=payload["name"],
        description=payload.get("target_task"),
    )
    session.add(test_definition)
    await session.flush()

    simulation_run_ids: list[uuid.UUID] = []
    for variant_name, base_config in _variant_specs(payload):
        config = dict(base_config)
        config["persona_count"] = payload["persona_count"]
        config["target_audience"] = payload["target_audience"]
        config["modules"] = payload.get("modules") or []

        variant = TestVariant(
            organization_id=organization_id,
            test_definition_id=test_definition.id,
            name=variant_name,
            config=config,
        )
        session.add(variant)
        await session.flush()

        run_deterministic_seed = uuid.uuid4().int & ((1 << 63) - 1)
        persona_sample = await _resolve_persona_sample(
            session, organization_id, payload, run_deterministic_seed
        )

        input_snapshot = {
            "wizard_test_type": payload["test_type"],
            "persona_count": payload["persona_count"],
            "target_audience": payload["target_audience"],
            "modules": payload.get("modules") or [],
            "url": config["url"],
            "role": config["role"],
            "pricing_version": quote.pricing_version,
        }
        if persona_sample is not None:
            input_snapshot["persona_sample"] = persona_sample

        run = SimulationRun(
            organization_id=organization_id,
            test_variant_id=variant.id,
            status=SimulationStatus.QUEUED,
            deterministic_seed=run_deterministic_seed,
            model_version="pending-engine",
            input_snapshot=input_snapshot,
            launch_run_id=run_id,
            free_entitlement_feature_key=free_entitlement_feature_key,
            chip_reservation_id=chip_reservation_id,
        )
        session.add(run)
        await session.flush()
        simulation_run_ids.append(run.id)

    draft.status = TestWizardDraftStatus.LAUNCHED
    draft.launch_result = {
        "test_definition_id": str(test_definition.id),
        "simulation_run_ids": [str(rid) for rid in simulation_run_ids],
        "used_free_entitlement": used_free_entitlement,
        "reserved_chips": reserved_chips,
    }
    await session.flush()

    return LaunchResult(
        test_definition_id=test_definition.id,
        simulation_run_ids=tuple(simulation_run_ids),
        used_free_entitlement=used_free_entitlement,
        reserved_chips=reserved_chips,
    )


__all__ = [
    "ACCESSIBILITY_PRECHECK",
    "AB_COMPARISON",
    "ANALYSIS_MODULES",
    "EXISTING_SITE_BASIC_UX",
    "ENGINE_NOT_AVAILABLE_MESSAGE",
    "MAX_PERSONA_COUNT",
    "MIN_PERSONA_COUNT",
    "PATCHABLE_FIELDS",
    "QUOTE_TEST_TYPE_BY_WIZARD_TYPE",
    "WIZARD_TEST_TYPES",
    "DraftValidationError",
    "LaunchResult",
    "InsufficientChipBalanceError",
    "build_quote_for_payload",
    "launch_draft",
    "merge_payload",
    "missing_fields_for_launch",
    "validate_patch_fields",
]
