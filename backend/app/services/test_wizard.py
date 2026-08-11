"""Yeni test kurulum sihirbazi: taslak dogrulama ve baslatma (launch) is mantigi.

Taslak, bes adim arasinda kaybolmamasi icin organizasyona bagli tek bir
satirda (`TestWizardDraft.payload`) tek bir JSON belge olarak tutulur; her
PATCH cagrisi yalnizca gonderilen alanlari dogrulayip mevcut payload'a
birlestirir (merge), boylece geri/ileri gidilmesi veya sayfa yenilenmesi
veriyi kaybetmez.

Fiyatlandirma/teklif (quote) burada yeniden hesaplanmaz; her zaman
`app.services.quotes.build_quote` (Prompt 3 servisi) cagrilir.
"""

import math
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.design_assets import DesignAssetStatus
from app.models.design_generation import DesignGenerationStatus
from app.models.personas import Persona
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.test_wizard import TestWizardDraft, TestWizardDraftStatus
from app.models.tests import TestDefinition, TestVariant
from app.services import chip_ledger, module_catalog, persona_presets, personas, quotes
from app.services import design_assets as design_assets_service
from app.services import design_generation as design_generation_service
from app.services import entitlements as entitlements_service
from app.services import page_analysis as page_analysis_service
from app.services.ai_pipeline import stage_sources as ai_context
from app.services.exceptions import (
    DesignAssetNotFoundError,
    DesignGenerationJobNotFoundError,
    InsufficientChipBalanceError,
)
from app.services.pricing import FEATURE_ACCESSIBILITY_PRECHECK, FEATURE_BASIC_UX_TEST

# --- Test turleri (bkz. docs/product-rules.md) ------------------------------
EXISTING_SITE_BASIC_UX = "existing_site_basic_ux"
AB_COMPARISON = "ab_comparison"
ACCESSIBILITY_PRECHECK = "accessibility_precheck"
WIZARD_TEST_TYPES = (EXISTING_SITE_BASIC_UX, AB_COMPARISON, ACCESSIBILITY_PRECHECK)

# --- Tasarim kaynagi turleri: URL veya yuklenen ekran goruntusu (screenshot) --
#
# `current_source_type` alani eski taslaklarda hic bulunmayabilir; bu durumda
# `effective_source_type()` varsayilan olarak SOURCE_TYPE_URL dondurur (geriye
# donuk uyumluluk - mevcut URL akisi/testleri bozulmaz). Ekran goruntusu
# kaynagi henuz analyzer/simulasyon motoruna baglanmadigi icin bu kaynakla
# GERCEK bir test BASLATILAMAZ (bkz. `launch_draft` altindaki acik engel) -
# kullanici yalnizca gorseli taslaga kaydedebilir, onizleyebilir, degistirebilir
# ve kaldirabilir (bkz. app.routers.design_assets / Prompt 2 kapsam sinirlari).
SOURCE_TYPE_URL = "url"
SOURCE_TYPE_SCREENSHOT = "screenshot"
SOURCE_TYPES = (SOURCE_TYPE_URL, SOURCE_TYPE_SCREENSHOT)

# "Tasarim B" (new_*) tarafi icin ek bir ucuncu kaynak turu: AI ile Tasarim
# A'dan uretilen bir varyant. Yalnizca `new_source_type` icin gecerlidir -
# "Tasarim A" (current_*) tarafi hicbir zaman AI ile uretilemez (AI uretimi
# HER ZAMAN mevcut bir tasarimdan/referanstan yeni bir varyant uretir, bkz.
# app.services.design_generation). Bu kaynakla da (screenshot gibi) GERCEK
# bir test BASLATILAMAZ.
SOURCE_TYPE_AI_GENERATED = "ai_generated"
NEW_SOURCE_TYPES = (SOURCE_TYPE_URL, SOURCE_TYPE_SCREENSHOT, SOURCE_TYPE_AI_GENERATED)

# Ekran goruntusu kaynagi "mevcut site: temel UX testi" VE A/B
# karsilastirmasinin her iki tarafinda da acik (bkz. Paket 4 Final -
# `launch_draft` artik URL disi kaynaklari da baslatir); erisilebilirlik on
# kontrolu DOM/sayfa yapisi gerektirdigi icin daima yalnizca URL kabul eder.
SCREENSHOT_ALLOWED_TEST_TYPES = (EXISTING_SITE_BASIC_UX,)

ACCESSIBILITY_SCREENSHOT_REJECTION_MESSAGE = (
    "Erisilebilirlik on kontrolu DOM ve sayfa yapisi gerektirdigi icin yalnizca URL kaynagiyla calisir."
)
SCREENSHOT_ASSET_UNAVAILABLE_MESSAGE = (
    "Tasarim gorseli kullanilamiyor (silinmis, saklama suresi dolmus veya erisilemez olabilir); "
    "lutfen yeni bir gorsel yukleyin."
)
AI_GENERATION_UNAVAILABLE_MESSAGE = (
    "AI ile uretilen tasarim taslagi kullanilamiyor (bulunamadi, henuz tamamlanmamis veya "
    "baska bir organizasyona ait olabilir); lutfen sonucu yeniden kontrol edin."
)
AI_GENERATION_ASSET_MISMATCH_MESSAGE = "new_design_asset_id, kabul edilen AI uretim sonucuyla eslesmiyor."

# --- Kullanici CTA onayi (Paket 4C+4D) ---------------------------------------
#
# Annotation, DesignAsset'in GLOBAL bir ozelligi DEGILDIR - ayni asset farkli
# taslaklarda ve ayni taslagin hem A hem B tarafinda kullanilabilir. Bu yuzden
# `design_asset_id` uzerinde ayri/unique bir tablo yerine, mevcut
# `TestWizardDraft.payload` JSON'una `current_cta_annotation`/
# `new_cta_annotation` olarak (current_design_asset_id/new_design_asset_id ile
# AYNI slot desenini izleyerek) eklenir - yeni migration/tablo GEREKMEZ.
CTA_SELECTION_CANDIDATE_CONFIRMATION = "candidate_confirmation"
CTA_SELECTION_MANUAL_BOX = "manual_box"
CTA_SELECTION_SOURCES = (CTA_SELECTION_CANDIDATE_CONFIRMATION, CTA_SELECTION_MANUAL_BOX)

CTA_ANNOTATION_FIELDS = ("current_cta_annotation", "new_cta_annotation")
CTA_ANNOTATION_FIELD_TO_ASSET_FIELD = {
    "current_cta_annotation": "current_design_asset_id",
    "new_cta_annotation": "new_design_asset_id",
}
_SLOT_ASSET_FIELD_TO_ANNOTATION_FIELD = {
    asset_field: annotation_field
    for annotation_field, asset_field in CTA_ANNOTATION_FIELD_TO_ASSET_FIELD.items()
}

_CTA_ANNOTATION_MIN_AREA_FRACTION = 0.0005
CTA_ANNOTATION_LARGE_AREA_WARNING = "cta_annotation_covers_full_image"
_CTA_ANNOTATION_LARGE_AREA_THRESHOLD = 0.95

CTA_ANNOTATION_ASSET_MISMATCH_MESSAGE = (
    "cta_annotation.design_asset_id, bu tarafin (current/new) guncel design_asset_id'siyle eslesmiyor."
)

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
    "current_source_type",
    "current_design_asset_id",
    "new_url",
    "new_source_type",
    "new_design_asset_id",
    "new_ai_generation_id",
    "current_cta_annotation",
    "new_cta_annotation",
    "persona_count",
    "target_audience",
    "persona_preset_id",
    "persona_distribution",
    "modules",
    "authorization_confirmed",
    "device_profile",
}

# --- AI raporu (`ai_report`) hazirlik/readiness sozlesmesi (Faz 3C.2B1) -------
#
# `ai_report` modulu, GERCEK bir AI rapor saglayicisi henuz olmadigi icin
# yalnizca `settings.ai_report_enabled` acikca `True` iken launch edilebilir.
# Hazirlik `False` (guvenli varsayilan) iken launch, HICBIR yan etki (Chip
# rezervasyonu, SimulationRun, PageAnalysis, pipeline) uretilmeden reddedilir -
# `validate_ai_report_readiness` `launch_draft`ta tum DB mutasyonlarindan ONCE
# cagrilir. Environment eksikligi bir Mock/yedek saglayiciya DUSMEZ.
AI_REPORT_NOT_READY_MESSAGE = (
    "AI raporu modulu su anda kullanilamiyor (saglayici henuz etkin degil); "
    "bu modulu kaldirip testi baslatabilirsiniz."
)


def validate_ai_report_readiness(payload: dict) -> None:
    """`ai_report` secili AMA hazirlik bayragi kapaliysa launch'i reddeder.

    `launch_draft` bunu, quote/rezervasyon dahil HICBIR yan etki uretilmeden
    ONCE cagirir; boylece kapali provider durumunda ne Chip rezervasyonu ne de
    SimulationRun/pipeline olusur (bkz. AI_REPORT_NOT_READY_MESSAGE)."""

    modules = payload.get("modules") or []
    if module_catalog.AI_REPORT_MODULE_KEY in modules and not settings.ai_report_enabled:
        raise DraftValidationError(AI_REPORT_NOT_READY_MESSAGE)


# --- AI etkilesim isi haritasi (`ai_interaction_heatmap`) hazirlik sozlesmesi ---
#
# `ai_report` ile AYNI, BAGIMSIZ desen: modul yalnizca
# `settings.interaction_heatmap_provider_ready` acikca `True` iken launch edilebilir.
# Hazirlik `False` iken launch HICBIR yan etki (Chip rezervasyonu, SimulationRun,
# PageAnalysis, heatmap job) uretilmeden reddedilir. `ai_report` persona
# zorunlulugunun AKSINE, heatmap persona GEREKTIRMEZ (persona-basli davranis
# uretmez; yalnizca hedef gorev + gercek etkilesim adaylarini kullanir).
AI_INTERACTION_HEATMAP_NOT_READY_MESSAGE = (
    "AI etkilesim isi haritasi modulu su anda kullanilamiyor (saglayici henuz "
    "etkin degil); bu modulu kaldirip testi baslatabilirsiniz."
)


def validate_interaction_heatmap_readiness(payload: dict) -> None:
    """`ai_interaction_heatmap` secili AMA hazirlik bayragi kapaliysa launch'i
    reddeder (bkz. AI_INTERACTION_HEATMAP_NOT_READY_MESSAGE). `launch_draft` bunu
    tum yan etkilerden ONCE cagirir."""

    modules = payload.get("modules") or []
    if (
        module_catalog.AI_INTERACTION_HEATMAP_MODULE_KEY in modules
        and not settings.interaction_heatmap_provider_ready
    ):
        raise DraftValidationError(AI_INTERACTION_HEATMAP_NOT_READY_MESSAGE)


# --- AI raporu (`ai_report`) persona zorunlulugu sozlesmesi ------------------
#
# `ai_report` pipeline'i, tanim geregi TEMSILI personalar uzerinde calisir:
# Stage 2+ persona-basli davranis uretimi icin en az bir kalici `Persona`
# satiri gerekir (bkz. app.services.ai_pipeline.orchestration._validate_persona_
# set - persona yoksa `invalid_persona_set` ile reddeder VE hicbir pipeline/
# stage olusmaz). Ancak persona satirlari YALNIZCA sihirbazda bir persona
# secimi (preset VEYA ozel dagilim) yapilmissa olusturulur (bkz.
# `_resolve_persona_sample` / `_payload_has_persona_selection`); "Belirtme"
# secildiginde (geriye donuk uyumluluk) hicbir persona yazilmaz.
#
# Bu yuzden `ai_report` secili AMA hicbir persona secimi yoksa launch, TUM yan
# etkilerden (baseline/AI Chip rezervasyonu, SimulationRun, PageAnalysis,
# pipeline) ONCE reddedilir - aksi halde (gozlenen hata) baseline calisir, 50
# AI Chip rezerve edilir, sonra pipeline init `invalid_persona_set` ile
# basarisiz olur ve AI ucreti gec iade edilir (kotu UX). "Belirtme" icin
# otomatik/varsayilan bir dagilim URETILMEZ - bu bir urun karari olurdu ve
# sihirbazin "isteğe bagli persona" tasariminla celisirdi. Persona-suz temel
# (ai_report'suz) testlerin geriye donuk uyumlulugu BOZULMAZ.
AI_REPORT_PERSONA_REQUIRED_MESSAGE = (
    "AI raporu modulu temsili personalar uzerinde calisir: once 3. adimda bir "
    "persona preset'i secin veya ozel bir persona dagilimi tanimlayin, sonra "
    "testi baslatin."
)


def _payload_has_persona_selection(payload: dict) -> bool:
    """Sihirbaz payload'inda gecerli bir persona secimi (preset veya ozel
    dagilim) var mi? `_resolve_persona_sample`in dallanma kosuluyla BIREBIR
    ayni truthiness'i kullanir - bos bir `persona_distribution={}` (custom mod
    secilip hicbir boyut girilmemis) da orada oldugu gibi BURADA da "secim yok"
    sayilir; boylece bu iki yer asla ayrisamaz (TEK dogruluk kaynagi)."""

    return bool(payload.get("persona_preset_id")) or bool(payload.get("persona_distribution"))


def validate_ai_report_persona_requirement(payload: dict) -> None:
    """`ai_report` secili AMA hicbir persona secimi yoksa launch'i reddeder.

    `launch_draft` bunu, `validate_ai_report_readiness`ten hemen SONRA ve
    quote/rezervasyon dahil HICBIR yan etki uretilmeden ONCE cagirir (bkz.
    AI_REPORT_PERSONA_REQUIRED_MESSAGE). Bu, frontend gizlemesinden BAGIMSIZ,
    zorunlu bir sunucu tarafi kapisidir - gecersiz kombinasyon pipeline init
    asamasina kadar HIC ilerlemez."""

    modules = payload.get("modules") or []
    if module_catalog.AI_REPORT_MODULE_KEY in modules and not _payload_has_persona_selection(payload):
        raise DraftValidationError(AI_REPORT_PERSONA_REQUIRED_MESSAGE)


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

    if "current_source_type" in patch and patch["current_source_type"] not in SOURCE_TYPES:
        raise DraftValidationError(f"Gecersiz current_source_type: {patch['current_source_type']}")

    if "current_design_asset_id" in patch and patch["current_design_asset_id"] is not None:
        try:
            uuid.UUID(str(patch["current_design_asset_id"]))
        except (ValueError, TypeError) as exc:
            raise DraftValidationError("current_design_asset_id gecerli bir UUID olmalidir") from exc

    if "new_source_type" in patch and patch["new_source_type"] not in NEW_SOURCE_TYPES:
        raise DraftValidationError(f"Gecersiz new_source_type: {patch['new_source_type']}")

    if "new_design_asset_id" in patch and patch["new_design_asset_id"] is not None:
        try:
            uuid.UUID(str(patch["new_design_asset_id"]))
        except (ValueError, TypeError) as exc:
            raise DraftValidationError("new_design_asset_id gecerli bir UUID olmalidir") from exc

    if "new_ai_generation_id" in patch and patch["new_ai_generation_id"] is not None:
        try:
            uuid.UUID(str(patch["new_ai_generation_id"]))
        except (ValueError, TypeError) as exc:
            raise DraftValidationError("new_ai_generation_id gecerli bir UUID olmalidir") from exc

    for field in CTA_ANNOTATION_FIELDS:
        if field in patch:
            _validate_cta_annotation_shape(patch[field], field=field)

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


def _validate_cta_annotation_shape(value: object, *, field: str) -> None:
    """`current_cta_annotation`/`new_cta_annotation` icin SAF/senkron sekil
    dogrulamasi - `design_asset_id` sahiplik/kullanilabilirlik dogrulamasi
    (DB erisimi gerektirir) burada YAPILMAZ, bkz. `resolve_cta_annotation_patch`.

    `null`, bir slot'un annotation'ini acikca temizlemek icin gecerlidir."""

    if value is None:
        return
    if not isinstance(value, dict):
        raise DraftValidationError(f"'{field}' bir sozluk olmalidir")

    try:
        uuid.UUID(str(value.get("design_asset_id")))
    except (ValueError, TypeError) as exc:
        raise DraftValidationError(f"'{field}.design_asset_id' gecerli bir UUID olmalidir") from exc

    box = value.get("box")
    if not isinstance(box, dict):
        raise DraftValidationError(f"'{field}.box' bir sozluk olmalidir")

    numeric_box: dict[str, float] = {}
    for key in ("x", "y", "w", "h"):
        raw = box.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise DraftValidationError(f"'{field}.box.{key}' sayisal olmalidir")
        number = float(raw)
        if not math.isfinite(number):
            raise DraftValidationError(f"'{field}.box.{key}' sonlu bir sayi degil (NaN/Infinity)")
        if not (0.0 <= number <= 1.0):
            raise DraftValidationError(f"'{field}.box.{key}' 0 ile 1 arasinda olmalidir")
        numeric_box[key] = number

    area_fraction = numeric_box["w"] * numeric_box["h"]
    if area_fraction < _CTA_ANNOTATION_MIN_AREA_FRACTION:
        raise DraftValidationError(f"'{field}' icin secilen alan anlamsiz derecede kucuk")

    if value.get("selection_source") not in CTA_SELECTION_SOURCES:
        raise DraftValidationError(
            f"'{field}.selection_source', {', '.join(CTA_SELECTION_SOURCES)} degerlerinden biri olmalidir"
        )

    source_candidate_index = value.get("source_candidate_index")
    if source_candidate_index is not None:
        if (
            isinstance(source_candidate_index, bool)
            or not isinstance(source_candidate_index, int)
            or source_candidate_index < 0
        ):
            raise DraftValidationError(
                f"'{field}.source_candidate_index' negatif olmayan bir tam sayi olmalidir"
            )


def cta_annotation_area_warning(value: dict | None) -> str | None:
    """Buyuk/tam-ekran bir CTA secimi ZORLA reddedilmez, yalnizca uyari
    dondurulur (bkz. plan - "uyari verilmeli", engelleme degil)."""

    if value is None:
        return None
    box = value.get("box") or {}
    try:
        area_fraction = float(box["w"]) * float(box["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if area_fraction > _CTA_ANNOTATION_LARGE_AREA_THRESHOLD:
        return CTA_ANNOTATION_LARGE_AREA_WARNING
    return None


async def resolve_cta_annotation_patch(
    session: AsyncSession, organization_id: uuid.UUID, raw_value: dict | None
) -> dict | None:
    """PATCH govdesinde gelen HAM annotation dict'ini sunucu tarafinda nihai
    sekle cevirir: `design_asset_id` sahiplik + kullanilabilirlik (aktif/
    silinmemis/suresi dolmamis/ikili verisi mevcut) burada BAGIMSIZ olarak
    dogrulanir (ayni `validate_screenshot_asset_ownership` kurallari), ve
    `verified_content_sha256` HER ZAMAN sunucuda `DesignAsset.checksum_sha256`
    okunarak yazilir - client'in gonderdigi HERHANGI bir hash degeri (byle bir
    alan gonderilmis olsa bile) sessizce YOK SAYILIR, asla guvenilmez."""

    if raw_value is None:
        return None

    asset_id = uuid.UUID(str(raw_value["design_asset_id"]))
    try:
        asset = await design_assets_service.get_owned_asset(session, organization_id, asset_id)
    except DesignAssetNotFoundError as exc:
        raise DraftValidationError(SCREENSHOT_ASSET_UNAVAILABLE_MESSAGE) from exc

    if (
        asset.status != DesignAssetStatus.ACTIVE
        or asset.image_data is None
        or design_assets_service.is_expired(asset)
    ):
        raise DraftValidationError(SCREENSHOT_ASSET_UNAVAILABLE_MESSAGE)

    box = raw_value["box"]
    return {
        "design_asset_id": str(asset.id),
        "box": {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "w": float(box["w"]),
            "h": float(box["h"]),
        },
        "selection_source": raw_value["selection_source"],
        "source_candidate_index": raw_value.get("source_candidate_index"),
        # Client'tan asla kabul edilmez - her zaman sunucuda DesignAsset'in
        # GUNCEL checksum'indan okunur.
        "verified_content_sha256": asset.checksum_sha256,
    }


def invalidate_stale_cta_annotations(existing: dict, merged: dict, patch: dict) -> dict:
    """Bir slot'un `design_asset_id`si (var olan degerden farkli olarak)
    degistiginde, O SLOT'UN annotation'i otomatik olarak `None`'a cekilir -
    diger slot ve diger taslaklar ETKILENMEZ. Istemci bu PATCH cagrisinda o
    slot'un annotation alanini ZATEN acikca belirtmisse (yeni bir deger veya
    acik `null` ile temizleme), o deger ONCELIKLIDIR - burada UZERINE
    YAZILMAZ (bu, ayni cagrida hem asset degisimini hem yeni asset icin
    annotation onayini birlikte gonderen istemciyi kirmaz)."""

    result = dict(merged)
    for asset_field, annotation_field in _SLOT_ASSET_FIELD_TO_ANNOTATION_FIELD.items():
        if annotation_field in patch:
            continue
        if merged.get(asset_field) != existing.get(asset_field):
            result[annotation_field] = None
    return result


def merge_payload(existing: dict, patch: dict) -> dict:
    merged = dict(existing)
    merged.update(patch)
    return merged


def effective_source_type(payload: dict) -> str:
    """Taslagin etkin tasarim kaynagi turunu dondurur.

    Eski taslaklarda `current_source_type` hic bulunmaz; bu durumda geriye
    donuk uyumluluk icin daima SOURCE_TYPE_URL varsayilir (mevcut URL akisi/
    testleri bozulmaz).
    """

    return payload.get("current_source_type") or SOURCE_TYPE_URL


def effective_new_source_type(payload: dict) -> str:
    """Taslagin etkin "Tasarim B" (new) kaynak turunu dondurur.

    `effective_source_type` ile ayni geriye donuk uyumluluk kurali gecerlidir:
    `new_source_type` hic bulunmuyorsa daima SOURCE_TYPE_URL varsayilir (eski
    A/B taslaklari `new_url` alanini korur, bu nedenle bozulmaz).
    """

    return payload.get("new_source_type") or SOURCE_TYPE_URL


NETWORK_DEVICE_TEST_SOURCE_REJECTION_MESSAGE = (
    "Ağ ve cihaz testi yalnızca tüm tasarım kaynakları canlı URL olduğunda kullanılabilir. "
    "Ekran görüntüsü kullanıyorsanız bu modülü kaldırın."
)


def validate_module_source_compatibility(payload: dict) -> None:
    """Secili gelismis modullerin (bkz. `payload["modules"]`), taslagin (tum
    varyantlarindaki) etkin tasarim kaynagi turleriyle (url/screenshot/
    ai_generated) uyumlu oldugunu dogrular - tek gercek kaynak olan modul
    katalogu capability alanina (`AnalysisModuleDefinition.
    supported_source_types`, bkz. app.services.module_catalog) dayanir.

    `launch_draft` bu fonksiyonu, HICBIR side effect (TestDefinition/
    SimulationRun/PageAnalysis/Chip/entitlement rezervasyonu) uretilmeden
    ONCE cagirir - bkz. `launch_draft` docstring'i. A/B karsilastirmasinda
    HER IKI taraf (current/new) da ayri ayri kontrol edilir: taraflardan
    biri bile modulun desteklemedigi bir kaynak turundeyse tum launch
    reddedilir (ornegin `network_device_test` + A/B URL/screenshot ->
    reddedilir, cunku screenshot tarafinda gercek bir ag/yukleme olcumu
    YAPILAMAZ).
    """

    modules: list[str] = payload.get("modules") or []
    if not modules:
        return

    source_types = [effective_source_type(payload)]
    if payload.get("test_type") == AB_COMPARISON:
        source_types.append(effective_new_source_type(payload))

    for module_key in modules:
        try:
            module = module_catalog.get_module_definition(module_key)
        except ValueError:
            # Bilinmeyen modul anahtari zaten `validate_patch_fields` tarafindan
            # reddedilir; burada savunma amacli sessizce atlanir.
            continue
        if any(source_type not in module.supported_source_types for source_type in source_types):
            if module_key == "network_device_test":
                raise DraftValidationError(NETWORK_DEVICE_TEST_SOURCE_REJECTION_MESSAGE)
            raise DraftValidationError(
                f"'{module.name}' modulu, secili tasarim kaynagi turuyle uyumlu degil."
            )


def validate_source_type_for_test_type(payload: dict) -> None:
    """Taslagin (birlestirilmis/merged) tam gorunumune gore kaynak turu <-> test
    turu uyumunu dogrular; format disi (cross-field) bir kural oldugu icin
    `validate_patch_fields`'ten (tek basina PATCH govdesini goren, saf/senkron
    fonksiyon) ayri tutulur - cagiran taraf (router) bunu birlestirilmis
    payload uzerinde ayrica cagirmalidir.

    A/B karsilastirmasi bu paketten itibaren her iki tarafta da ekran
    goruntusu kaynagini kabul eder (bkz. `launch_draft` - baslatma yine de
    gorsel karsilastirma motoru baglanana kadar engellenir). Erisilebilirlik
    on kontrolu ise DOM/sayfa yapisi gerektirdigi icin daima yalnizca URL
    kabul eder.
    """

    test_type = payload.get("test_type")
    if test_type == ACCESSIBILITY_PRECHECK and effective_source_type(payload) == SOURCE_TYPE_SCREENSHOT:
        raise DraftValidationError(ACCESSIBILITY_SCREENSHOT_REJECTION_MESSAGE)


async def validate_screenshot_asset_ownership(
    session: AsyncSession, organization_id: uuid.UUID, asset_id: uuid.UUID
) -> None:
    """Ekran goruntusu kaynagi olarak referans verilen `DesignAsset`in GERCEKTEN
    bu organizasyona ait, aktif, ikili verisi hala mevcut ve saklama suresi
    dolmamis oldugunu DB'ye sorarak dogrular (yalnizca UUID bicimi kontrolu
    DEGIL). Baska bir organizasyonun asset'i icin de AYNI genel mesaj
    donduruler - "yok" ile "baskasina ait" arasinda hicbir bilgi sizdirilmaz
    (bkz. app.services.design_assets.get_owned_asset ile ayni "404 sizdirmaz"
    deseni, burada DraftValidationError/400 olarak yeniden yukselir).
    """

    try:
        asset = await design_assets_service.get_owned_asset(session, organization_id, asset_id)
    except DesignAssetNotFoundError as exc:
        raise DraftValidationError(SCREENSHOT_ASSET_UNAVAILABLE_MESSAGE) from exc

    if (
        asset.status != DesignAssetStatus.ACTIVE
        or asset.image_data is None
        or design_assets_service.is_expired(asset)
    ):
        raise DraftValidationError(SCREENSHOT_ASSET_UNAVAILABLE_MESSAGE)


async def validate_ai_generation_ownership(
    session: AsyncSession, organization_id: uuid.UUID, job_id: uuid.UUID, design_asset_id: uuid.UUID
) -> None:
    """Draft'a `new_source_type=ai_generated` olarak referans verilen is (job)
    icin: bu organizasyona ait, BASARILI (succeeded) ve `design_asset_id`
    parametresi tam olarak isin `result_asset_id`siyle eslesiyor mu diye
    dogrular. Boylece kullanici, kabul ETMEDIGI (ör. hala 'running' veya
    reddedilmis) bir AI sonucunu draft'a "sahte kabul" ile baglayamaz - tek
    gecerli baglanti yolu, gercekte basarili bir isin GERCEK sonuc
    asset'idir. Baska bir organizasyona ait is icin de AYNI genel mesaj
    donduruler (bkz. validate_screenshot_asset_ownership ile ayni "sizdirmaz"
    deseni).
    """

    try:
        job = await design_generation_service.get_owned_job(session, organization_id, job_id)
    except DesignGenerationJobNotFoundError as exc:
        raise DraftValidationError(AI_GENERATION_UNAVAILABLE_MESSAGE) from exc

    if job.status != DesignGenerationStatus.SUCCEEDED or job.result_asset_id is None:
        raise DraftValidationError(AI_GENERATION_UNAVAILABLE_MESSAGE)

    if job.result_asset_id != design_asset_id:
        raise DraftValidationError(AI_GENERATION_ASSET_MISMATCH_MESSAGE)

    # Sonuc asset'i, screenshot kaynagiyla AYNI (aktif/suresi dolmamis) sahiplik
    # kontrolunden de gecirilir - is basarili olsa bile asset ayrica suresi
    # dolmus/silinmis olabilir (ör. is uzun sure kabul edilmeden beklemis).
    await validate_screenshot_asset_ownership(session, organization_id, design_asset_id)


async def revalidate_cta_annotation(
    session: AsyncSession, organization_id: uuid.UUID, payload: dict, *, slot: str
) -> bool:
    """Ileriye donuk: bir slot'un kaydedilmis annotation'inin HALA gecerli
    olup olmadigini (asset hala kullanilabilir mi, `verified_content_sha256`
    hala DesignAsset'in guncel checksum'iyla eslesiyor mu) dogrular. Bu turda
    `launch_draft()`a BAGLANMAZ (screenshot/AI launch zaten engelli) - yalnizca
    birim testte egzersiz edilir, gelecekteki launch-zamani entegrasyonu icin
    hazir bir yapi tasidir. `slot` "current" veya "new" olmalidir."""

    annotation_field = f"{slot}_cta_annotation"
    annotation = payload.get(annotation_field)
    if annotation is None:
        return True

    try:
        asset = await design_assets_service.get_owned_asset(
            session, organization_id, uuid.UUID(str(annotation["design_asset_id"]))
        )
    except DesignAssetNotFoundError:
        return False

    if (
        asset.status != DesignAssetStatus.ACTIVE
        or asset.image_data is None
        or design_assets_service.is_expired(asset)
    ):
        return False

    return asset.checksum_sha256 == annotation.get("verified_content_sha256")


def missing_fields_for_launch(payload: dict) -> list[str]:
    """Baslatma icin eksik/gecersiz alanlarin listesini dondurur (bos ise hazir demektir)."""

    missing: list[str] = []

    for field in (
        "project_id",
        "name",
        "target_task",
        "test_type",
        "persona_count",
        "target_audience",
    ):
        if payload.get(field) in (None, ""):
            missing.append(field)

    # Aktif olmayan kaynak alani (secilmeyen kaynak turune ait) launch
    # dogrulamasinda HIC KULLANILMAZ - yalnizca etkin kaynak turune gore
    # gerekli alan kontrol edilir.
    if effective_source_type(payload) == SOURCE_TYPE_SCREENSHOT:
        if not payload.get("current_design_asset_id"):
            missing.append("current_design_asset_id")
    else:
        if payload.get("current_url") in (None, ""):
            missing.append("current_url")

    if payload.get("test_type") == AB_COMPARISON:
        if effective_new_source_type(payload) in (SOURCE_TYPE_SCREENSHOT, SOURCE_TYPE_AI_GENERATED):
            if not payload.get("new_design_asset_id"):
                missing.append("new_design_asset_id")
            if effective_new_source_type(payload) == SOURCE_TYPE_AI_GENERATED and not payload.get(
                "new_ai_generation_id"
            ):
                missing.append("new_ai_generation_id")
        else:
            if payload.get("new_url") in (None, ""):
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
    # Paket 4 Final Hardening: stale CTA annotation'lar launch aninda artik
    # SESSIZCE temizlenmez - hangi tarafin (current/new) annotation'i
    # temizlendigi burada acik, dogru tarafa bagli bir uyari olarak tasinir
    # (bkz. `stale_cta_annotation_warning_message`).
    warnings: tuple[dict, ...] = ()


async def _resolve_persona_sample(
    session: AsyncSession,
    organization_id: uuid.UUID,
    payload: dict,
    deterministic_seed: int,
) -> tuple[dict | None, personas.CohortSampleResult | None]:
    """Sihirbazda secilen preset/ozel dagilimdan deterministik bir cohort ozeti kurar.

    Persona secimi bu adimda zorunlu tutulmaz (geriye donuk uyumluluk icin):
    ne `persona_preset_id` ne de `persona_distribution` verilmisse `(None, None)`
    dondurulur ve `input_snapshot`'a hicbir persona ornekleme verisi eklenmez.

    Iki deger dondurur: (1) `input_snapshot["persona_sample"]`e AYNEN yazilan,
    degismeyen JSON-uyumlu sozluk (motorun kaynak gercekligi) ve (2) ayni
    ornekleme sonucunun HAM `CohortSampleResult`'i - `build_representative_personas`
    (bkz. `launch_draft`) BU ikinci degeri kullanir, boylece ayni dagilim
    ikinci kez sample edilmez/yeni bir seed uretilmez.
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
        return None, None

    result = personas.sample_cohorts(distribution, payload["persona_count"], deterministic_seed)
    persona_sample_snapshot = {
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
    return persona_sample_snapshot, result


def _build_persona_projection(
    cohort_sample: personas.CohortSampleResult,
    expected_persona_count: int,
) -> tuple[personas.PersonaProjection, ...]:
    """Bir `CohortSampleResult`i en fazla `MAX_REPRESENTATIVE_PERSONAS` saf
    persona DTO'suna indirger - launch basina TAM OLARAK BIR KEZ cagrilir
    (bkz. `launch_draft`: varyant dongusunden ONCE) ve donen tuple, o launch'in
    TUM varyantlari arasinda PAYLASILIR (her varyant kendi ORM `Persona`
    nesnelerini bu AYNI DTO listesinden turetir, bkz. `_persona_rows_from_projection`)
    - boylece A/B karsilastirmasinin iki tarafi da birebir ayni cohort'u
    (index/label/attributes/population_weight) gorur, yalnizca test edilen
    tasarim degisir.

    `build_representative_personas` zaten agirlik toplaminin `cohort_sample.
    total_count`e esit oldugunu dogrular/garanti eder (bkz. o fonksiyonun
    docstring'i); burada bunu AYRICA, acik bir sekilde `expected_persona_count`
    (yani `payload["persona_count"]`) degerine karsi yeniden dogrularuz -
    boylece projeksiyon fonksiyonu ileride (ornegin test amacli monkeypatch
    veya gelecekteki bir degisiklikle) kendi ic garantisini saglamasa bile
    launch hicbir zaman tutarsiz Persona satirlari kaydetmez.

    Gecersiz bir projeksiyon (`PersonaValidationError` veya toplam uyusmazligi)
    burada sessizce yutulmaz/duzeltilmez - acik bir `DraftValidationError`
    olarak cagiran tarafa (`launch_draft`) yukseltilir; bu, launch'in mevcut
    hata/rollback sozlesmesiyle (bkz. `app.routers.test_wizard.launch_draft`)
    ayni sekilde ele alinmasini saglar.
    """

    try:
        projection = personas.build_representative_personas(cohort_sample)
    except personas.PersonaValidationError as exc:
        raise DraftValidationError(f"persona projeksiyonu olusturulamadi: {exc}") from exc

    total_weight = sum(persona.population_weight for persona in projection)
    if total_weight != expected_persona_count:
        raise DraftValidationError(
            "persona projeksiyonu agirlik toplami persona_count ile eslesmiyor "
            f"(beklenen: {expected_persona_count}, hesaplanan: {total_weight})"
        )

    return projection


def _persona_rows_from_projection(
    simulation_run_id: uuid.UUID,
    projection: tuple[personas.PersonaProjection, ...],
) -> list[Persona]:
    """Paylasilan (launch basina bir kez hesaplanmis) projeksiyon DTO listesinden,
    VERILEN run'a ozel, TAZE `Persona` ORM nesneleri uretir (henuz session'a
    eklenmemis) - ayni ORM nesneleri asla iki run arasinda paylasilmaz; her
    cagri kendi `attributes` sozluk kopyasini (`dict(persona.attributes)`)
    olusturur ki bir run'a ait satirin ORM durumu diger run'inkini
    (kazayla paylasilan referans uzerinden) etkilemesin.
    """

    return [
        Persona(
            simulation_run_id=simulation_run_id,
            index=persona.index,
            label=persona.label,
            attributes=dict(persona.attributes),
            population_weight=persona.population_weight,
        )
        for persona in projection
    ]


def _current_side_spec(payload: dict, *, role: str) -> dict:
    return {
        "role": role,
        "source_type": effective_source_type(payload),
        "url": payload.get("current_url"),
        "design_asset_id": payload.get("current_design_asset_id"),
    }


def _new_side_spec(payload: dict) -> dict:
    return {
        "role": "new",
        "source_type": effective_new_source_type(payload),
        "url": payload.get("new_url"),
        "design_asset_id": payload.get("new_design_asset_id"),
    }


def _variant_specs(payload: dict) -> list[tuple[str, dict]]:
    """Her varyant icin `{role, source_type, url|design_asset_id}` uretir.

    Paket 4 Final: kaynak URL ile sinirli degildir - `source_type`
    (url/screenshot/ai_generated) hangi alanin (`url` veya
    `design_asset_id`) gecerli oldugunu belirler; `launch_draft` bunu
    `page_analysis_service.create_analysis`'e dogru anahtar kelime
    argumaniyla ( `url=` veya `design_asset_id=`) gecirir. "ai_generated"
    (yalnizca Tasarim B icin gecerli) da `design_asset_id` uzerinden
    calisir - AI'nin urettigi sonuc, kabul edildiginde zaten sıradan bir
    `DesignAsset`e donusmustur (bkz. app.services.design_generation);
    burada URL/screenshot'tan farkli bir isleme YOKTUR.
    """

    if payload["test_type"] == AB_COMPARISON:
        return [
            ("Mevcut Tasarim", _current_side_spec(payload, role="existing")),
            ("Yeni Tasarim", _new_side_spec(payload)),
        ]
    return [("Ana Senaryo", _current_side_spec(payload, role="primary"))]


async def _revalidate_launch_sources(
    session: AsyncSession, organization_id: uuid.UUID, payload: dict
) -> None:
    """Launch aninda, PATCH sirasinda zaten yapilan sahiplik/kullanilabilirlik
    kontrollerini (bkz. app.routers.test_wizard.patch_draft) AYNEN tekrar
    calistirir - son PATCH ile launch arasinda asset silinmis/expire olmus
    olabilir, create-time/PATCH-time kontrolune TEK BASINA guvenilmez (ayni
    ilke `design_assets_service.ensure_asset_usable` docstring'inde de var).
    Herhangi bir taraf gecersizse `DraftValidationError` firlatilir ve
    `launch_draft` cagirani BU NOKTADAN SONRAKI hicbir side effect'i
    (TestDefinition/SimulationRun/Chip/entitlement rezervasyonu) uretmez."""

    sides = [("current_design_asset_id", effective_source_type(payload))]
    if payload.get("test_type") == AB_COMPARISON:
        sides.append(("new_design_asset_id", effective_new_source_type(payload)))

    for field_name, source_type in sides:
        asset_id_raw = payload.get(field_name)
        if source_type in (SOURCE_TYPE_SCREENSHOT, SOURCE_TYPE_AI_GENERATED) and asset_id_raw:
            await validate_screenshot_asset_ownership(session, organization_id, uuid.UUID(str(asset_id_raw)))

    if (
        payload.get("test_type") == AB_COMPARISON
        and effective_new_source_type(payload) == SOURCE_TYPE_AI_GENERATED
    ):
        new_asset_id_raw = payload.get("new_design_asset_id")
        new_generation_id_raw = payload.get("new_ai_generation_id")
        if not (new_asset_id_raw and new_generation_id_raw):
            raise DraftValidationError(AI_GENERATION_UNAVAILABLE_MESSAGE)
        await validate_ai_generation_ownership(
            session, organization_id, uuid.UUID(str(new_generation_id_raw)), uuid.UUID(str(new_asset_id_raw))
        )


STALE_CTA_ANNOTATION_CLEARED_CODE = "stale_cta_annotation_cleared"


def stale_cta_annotation_warning_message(slot: str) -> str:
    side = "Tasarım A" if slot == "current" else "Tasarım B"
    return (
        f"{side} için tasarım değiştiği/kullanılamaz hale geldiği için önceki CTA "
        "seçiminiz temizlendi. Yeni bir CTA seçmeden de analizi başlatabilirsiniz - "
        "CTA'ya özel değerlendirmeler bu durumda aday düzeyinde kalır."
    )


async def _clean_stale_cta_annotations_for_launch(
    session: AsyncSession, organization_id: uuid.UUID, payload: dict
) -> tuple[dict, list[dict]]:
    """Launch aninda gecersizlesmis (asset degismis/silinmis veya hash artik
    guncel checksum'la eslesmeyen) bir CTA onayini `None`'a ceker - launch'i
    ENGELLEMEZ (annotation zorunlu degildir, genel dikkat analizi zaten
    fallback'tir - bkz. `adapt_visual_page_analysis`), ama SESSIZCE de
    yapmaz: hangi tarafin (`current`/`new`) annotation'inin temizlendigi,
    dogru tarafa bagli acik bir uyari olarak (bkz.
    `stale_cta_annotation_warning_message`) dondurulur - cagiran taraf
    (`launch_draft`) bunu `LaunchResult.warnings`e ekler. `payload`
    degistirilmez, temizlenmis bir kopya + uyari listesi dondurulur."""

    cleaned = dict(payload)
    warnings: list[dict] = []
    for slot in ("current", "new"):
        field = f"{slot}_cta_annotation"
        if cleaned.get(field) is not None and not await revalidate_cta_annotation(
            session, organization_id, cleaned, slot=slot
        ):
            cleaned[field] = None
            warnings.append(
                {
                    "code": STALE_CTA_ANNOTATION_CLEARED_CODE,
                    "slot": slot,
                    "message": stale_cta_annotation_warning_message(slot),
                }
            )
    return cleaned, warnings


async def launch_draft(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    requested_by_user_id: uuid.UUID,
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
            warnings=tuple(result.get("warnings") or ()),
        )

    missing = missing_fields_for_launch(draft.payload)
    if missing:
        raise DraftValidationError(
            f"Sihirbaz tamamlanmadan baslatilamaz, eksik/gecersiz alan(lar): {', '.join(missing)}"
        )

    # Kaynak-modul uyumluluk dogrulamasi (ör. `network_device_test` +
    # screenshot/AI kaynagi) HER SEYDEN ONCE, hicbir side effect
    # uretilmeden yapilir - bkz. `validate_module_source_compatibility`
    # docstring'i. Worker'daki (`app.services.simulation_worker.
    # _process_selected_modules`) ayni kontrol savunma amacli olarak
    # KALIR (eski/bozuk/manuel olusturulmus kayitlar icin).
    validate_module_source_compatibility(draft.payload)

    # AI raporu hazirlik (readiness) kontrolu: `ai_report` secili ama saglayici
    # etkin degilse launch BURADA, hicbir yan etki (Chip rezervasyonu/
    # SimulationRun/PageAnalysis/pipeline) uretilmeden reddedilir (bkz.
    # `validate_ai_report_readiness`). Yalnizca frontend gizlemesine
    # guvenilmez - bu backend kontrolu ZORUNLUDUR.
    validate_ai_report_readiness(draft.payload)

    # AI raporu persona zorunlulugu: `ai_report` secili AMA hicbir persona
    # secimi (preset/ozel dagilim) yoksa launch BURADA, `validate_ai_report_
    # readiness`ten hemen sonra ve yine hicbir yan etki (Chip/AI rezervasyonu,
    # SimulationRun, PageAnalysis, pipeline) uretilmeden reddedilir (bkz.
    # `validate_ai_report_persona_requirement`). Aksi halde baseline calisir,
    # AI Chip rezerve edilir ve pipeline init `invalid_persona_set` ile
    # basarisiz olurdu (gozlenen hata). Yalnizca frontend gizlemesine
    # guvenilmez - bu backend kontrolu ZORUNLUDUR.
    validate_ai_report_persona_requirement(draft.payload)

    # AI etkilesim isi haritasi hazirlik kontrolu: `ai_interaction_heatmap`
    # secili ama saglayici/bayrak etkin degilse launch BURADA, hicbir yan etki
    # uretilmeden reddedilir (bkz. `validate_interaction_heatmap_readiness`).
    # `ai_report`tan BAGIMSIZ bir kapidir.
    validate_interaction_heatmap_readiness(draft.payload)

    # Paket 4 Final: her tarafin kaynagi (URL/screenshot/AI) launch aninda
    # BAGIMSIZ olarak yeniden dogrulanir - gecersiz bir taraf (silinmis/
    # expired/cross-tenant asset, kabul edilmemis AI isi) BU NOKTADAN SONRAKI
    # hicbir side effect'i (TestDefinition/SimulationRun/Chip/entitlement
    # rezervasyonu) uretmeden reddedilir (bkz. `_revalidate_launch_sources`).
    await _revalidate_launch_sources(session, organization_id, draft.payload)
    payload, stale_cta_warnings = await _clean_stale_cta_annotations_for_launch(
        session, organization_id, draft.payload
    )
    draft.payload = payload

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

    # AYRI AI Chip rezervasyonu (bkz. quote.ai_report_chips / pricing.
    # AI_REPORT_CHIP_COST): baseline ucretinden BAGIMSIZ, launch grubu basina
    # TEK bir rezervasyon (A/B'de iki varyant AYNI rezervasyonu paylasir - ayni
    # idempotency-key `ai-report-launch:{draft.id}` ayni satiri dondurur).
    #
    # ATOMIK bakiye: bu ikinci `reserve_chips` cagrisi AYNI transaction icinde,
    # `_lock_organization`in `SELECT ... FOR UPDATE`i ile AYNI kilitli
    # organizasyon satirini gorur; bakiye (ilk rezervasyon zaten dusuruldukten
    # SONRA) yetersizse `InsufficientChipBalanceError` firlatir ve router tum
    # transaction'i geri alir - YARIM (yalnizca baseline rezerve edilmis)
    # durum kalmaz, cift ucret olusmaz. AI tamamlanana kadar RESERVED kalir;
    # bu fazda consume EDILMEZ (bkz. simulation_worker._resolve_launch_group -
    # yalnizca baseline-basarisizligi durumunda release edilir).
    ai_chip_reservation_id: uuid.UUID | None = None
    if quote.ai_report_chips > 0:
        ai_reservation = await chip_ledger.reserve_chips(
            session,
            organization_id,
            quote.ai_report_chips,
            f"AI raporu rezervasyonu: {payload.get('name')}",
            run_id=run_id,
            idempotency_key=f"ai-report-launch:{draft.id}",
        )
        ai_chip_reservation_id = ai_reservation.id

    # AYRI, BAGIMSIZ heatmap Chip rezervasyonu (bkz. quote.interaction_heatmap_
    # chips / pricing.AI_INTERACTION_HEATMAP_CHIP_COST). `ai_report` rezervasyonu
    # ile AYNI atomik-bakiye/idempotency deseni (`ai-heatmap-launch:{draft.id}`),
    # ama TAMAMEN AYRI bir yasam dongusu (bkz. app.services.ai_interaction_heatmap.
    # worker settlement). Iki modul birlikte secilirse iki AYRI rezervasyon olusur;
    # ayni kilitli organizasyon satiri uzerinde bakiye atomik dogrulanir - yetersiz
    # bakiyede tum transaction geri alinir, yarim/cift rezervasyon kalmaz. Heatmap
    # tamamlanana kadar RESERVED kalir; consume/release worker settlement'inda olur.
    heatmap_chip_reservation_id: uuid.UUID | None = None
    if quote.interaction_heatmap_chips > 0:
        heatmap_reservation = await chip_ledger.reserve_chips(
            session,
            organization_id,
            quote.interaction_heatmap_chips,
            f"AI etkilesim isi haritasi rezervasyonu: {payload.get('name')}",
            run_id=run_id,
            idempotency_key=f"ai-heatmap-launch:{draft.id}",
        )
        heatmap_chip_reservation_id = heatmap_reservation.id

    # Launch aninda `SimulationRun.input_snapshot`a yazilacak, ALLOWLIST'li,
    # sunucu tarafinda normalize edilmis AUTHORITATIVE baglam alanlari (bkz.
    # app.services.ai_pipeline.stage_sources.build_task_context_source). Bu
    # alanlar promptlarda GUVENILMEYEN kullanici verisi olarak islenmelidir
    # (bkz. sanitize_context_text yorumu) - burada yalnizca duz-metin/kontrol-
    # karakteri/HTML/uzunluk guvenligi uygulanir, prompt injection'a karsi tam
    # koruma IDDIA EDILMEZ. `test_description`: sihirbaz payload'inda GERCEK bir
    # "test aciklamasi" alani YOKTUR; `target_audience` (kitle) YANLIS bir proxy
    # olacagi icin BILEREK kullanilmaz - alan None birakilir ve stage_sources
    # deterministik bir fallback'e duser. `methodology_context`: sunucu
    # kontrollu, surumlenmis SABIT (METHODOLOGY_CONTEXT_V1).
    authoritative_context = {
        "target_task": ai_context.sanitize_context_text(payload.get("target_task"), max_len=500),
        "test_name": ai_context.sanitize_context_text(payload.get("name"), max_len=200),
        "test_description": ai_context.sanitize_context_text(payload.get("test_description"), max_len=1000),
        "methodology_context": ai_context.METHODOLOGY_CONTEXT_V1,
    }

    test_definition = TestDefinition(
        organization_id=organization_id,
        project_id=uuid.UUID(str(payload["project_id"])),
        name=payload["name"],
        description=payload.get("target_task"),
    )
    session.add(test_definition)
    await session.flush()

    # Persona girdisi (persona_preset_id/persona_distribution/persona_count),
    # `_variant_specs`in ayirdigi tek sey olan kaynak (URL/DesignAsset) turunun
    # aksine, TUM varyantlar icin ORTAKTIR - bu yuzden cohort ornekleme, varyant
    # dongusunden ONCE, TEK bir sabit `persona_sample_seed` ile TAM OLARAK BIR
    # KEZ yapilir. Bu seed, motor determinizmi icin varyant basina KASITLI
    # olarak farkli uretilen `SimulationRun.deterministic_seed`den TAMAMEN
    # BAGIMSIZDIR (bkz. asagidaki `run_deterministic_seed`) - ikisi karistirilirsa
    # (largest-remainder'in eslik-kalan tie-break'i, bkz. app.services.personas.
    # _allocate_counts) A/B'nin iki tarafi FARKLI cohort segment sayimlarina
    # sahip olabilirdi; bu, "A/B karsilastirmasinda yalnizca test edilen tasarim
    # degisir" ilkesini ihlal ederdi.
    persona_sample_seed = uuid.uuid4().int & ((1 << 63) - 1)
    persona_sample, cohort_sample = await _resolve_persona_sample(
        session, organization_id, payload, persona_sample_seed
    )
    persona_projection: tuple[personas.PersonaProjection, ...] | None = None
    if cohort_sample is not None:
        persona_projection = _build_persona_projection(cohort_sample, payload["persona_count"])

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

        # Motor (baseline/advanced_modules) determinizmi icin: her run kendi
        # BAGIMSIZ seed'ini alir - bu, persona cohort ornekleme seed'inden
        # (yukarida) AYRI kalmaya devam eder; degistirilmedi.
        run_deterministic_seed = uuid.uuid4().int & ((1 << 63) - 1)

        # Paket 4B/4 Final: bu varyantin kaynagi (URL VEYA DesignAsset) icin
        # sunucu tarafinda otomatik bir PageAnalysis capture'i olusturulur ve
        # SimulationRun buna kalici olarak baglanir - client `page_analysis_id`
        # enjekte edemez (launch endpoint'i zaten govde almiyor). SSRF/
        # normalizasyon, yetki onayi VE DesignAsset sahiplik/kullanilabilirlik
        # zorunlulugu `create_analysis` uzerinden AYNEN yeniden kullanilir
        # (kopyalanmaz). A/B'de ayni kaynak her iki tarafta kullanilsa bile
        # HER VARYANT icin AYRI bir PageAnalysis satiri olusturulur - bu, A ve
        # B'nin provenance'inin asla karismamasini garanti eder.
        if config["source_type"] == SOURCE_TYPE_URL:
            analysis = await page_analysis_service.create_analysis(
                session,
                organization_id=organization_id,
                requested_by_user_id=requested_by_user_id,
                url=config["url"],
                authorization_confirmed=bool(payload.get("authorization_confirmed")),
            )
        else:
            analysis = await page_analysis_service.create_analysis(
                session,
                organization_id=organization_id,
                requested_by_user_id=requested_by_user_id,
                design_asset_id=uuid.UUID(str(config["design_asset_id"])),
            )

        # Paket 4 Final Hardening: `input_snapshot["url"]`e artik hicbir
        # kosulda sahte bir deger (`design-asset:<id>`) yazilmaz. URL
        # kaynaginda gercek `url` dolu, `source_reference`/`design_asset_id`
        # `None`dur; DesignAsset kaynaginda (screenshot/AI) `url` acikca
        # `None`dur ve bunun yerine ayri, acikca isimlendirilmis
        # `source_reference` alani (motorun "bos olmayan kimlik" sartini
        # additive bicimde karsilayan `design-asset:<id>` metni - bkz.
        # app.engine.baseline.run_baseline_simulation /
        # app.engine.advanced_modules._require_source_identity) kullanilir.
        # Motor gercek DOM/gorsel `PageFeatureSnapshot`'i zaten
        # `page_feature_snapshot` parametresinden alir (bkz.
        # simulation_worker._load_page_feature_input); `source_reference`
        # yalnizca kimliklendirme/etiketleme icindir, hicbir skor hesabina
        # girmez (skor icin gercek kaynak: `page.input_hash`, DesignAsset
        # kaynaginda `PageAnalysis.content_sha256` - bkz. adapt_visual_page_analysis).
        input_snapshot = {
            "wizard_test_type": payload["test_type"],
            "persona_count": payload["persona_count"],
            "target_audience": payload["target_audience"],
            "modules": payload.get("modules") or [],
            "url": config["url"],
            "role": config["role"],
            "source_type": config["source_type"],
            "pricing_version": quote.pricing_version,
            # Authoritative AI baglam alanlari (Faz 3C.2B1) - tum varyantlarda
            # AYNI (kaynaktan bagimsiz, launch-genelinde ortak). `methodology_
            # context`in varligi build_task_context_source icin "yeni snapshot"
            # ayirt edici isaretidir.
            "target_task": authoritative_context["target_task"],
            "test_name": authoritative_context["test_name"],
            "test_description": authoritative_context["test_description"],
            "methodology_context": authoritative_context["methodology_context"],
        }
        if config["design_asset_id"]:
            input_snapshot["design_asset_id"] = str(config["design_asset_id"])
            input_snapshot["source_reference"] = f"design-asset:{config['design_asset_id']}"
            input_snapshot["page_analysis_id"] = str(analysis.id)
        if config["source_type"] == SOURCE_TYPE_AI_GENERATED:
            input_snapshot["ai_generation_id"] = payload.get("new_ai_generation_id")
        if persona_sample is not None:
            input_snapshot["persona_sample_seed"] = persona_sample_seed
            input_snapshot["persona_sample"] = persona_sample

        # Kullanicinin sihirbazda onayladigi CTA kutusu (varsa, yalnizca
        # DesignAsset kaynakli taraflar icin anlamlidir - bkz.
        # CTA_ANNOTATION_FIELD_TO_ASSET_FIELD) run'in KENDI degismez
        # input_snapshot kopyasina yazilir; asset sonradan silinse/degisse
        # bile tamamlanmis run/rapor bundan ETKILENMEZ (bkz.
        # app.services.page_analysis_adapter.adapt_visual_page_analysis).
        annotation_field = "current_cta_annotation" if config["role"] != "new" else "new_cta_annotation"
        annotation = payload.get(annotation_field)
        if annotation is not None:
            input_snapshot["user_confirmed_cta"] = annotation

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
            ai_chip_reservation_id=ai_chip_reservation_id,
            heatmap_chip_reservation_id=heatmap_chip_reservation_id,
            page_analysis_id=analysis.id,
        )
        session.add(run)
        await session.flush()
        simulation_run_ids.append(run.id)

        # Ayni transaction'in parcasi olarak (ayri bir commit/transaction
        # ACILMAZ): persona_projection varsa (persona secimi yapilmissa),
        # TUM varyantlar icin (yukarida) BIR KEZ hesaplanmis, PAYLASILAN
        # projeksiyon DTO listesinden bu run'a OZEL, taze `Persona` ORM
        # nesneleri turetilir (bkz. `_persona_rows_from_projection`) - ayni
        # ORM nesneleri iki run arasinda paylasilmaz, ama A/B'nin iki tarafi
        # da AYNI DTO kaynagindan geldigi icin index/label/attributes/
        # population_weight birebir aynidir. Bir sonraki adimda (Chip/
        # entitlement rezervasyonu zaten bu noktadan ONCE yapildi) herhangi
        # bir hata olursa, `session.commit()` HICBIR ZAMAN cagrilmadigi icin
        # (bkz. app.routers.test_wizard.launch_draft / app.db.get_session) bu
        # Persona satirlari da run/variant/definition ile birlikte geri
        # alinir (rollback) - yetim Persona satiri kalmaz.
        if persona_projection is not None:
            persona_rows = _persona_rows_from_projection(run.id, persona_projection)
            session.add_all(persona_rows)
            await session.flush()

    draft.status = TestWizardDraftStatus.LAUNCHED
    draft.launch_result = {
        "test_definition_id": str(test_definition.id),
        "simulation_run_ids": [str(rid) for rid in simulation_run_ids],
        "used_free_entitlement": used_free_entitlement,
        "reserved_chips": reserved_chips,
        "warnings": stale_cta_warnings,
    }
    await session.flush()

    return LaunchResult(
        test_definition_id=test_definition.id,
        simulation_run_ids=tuple(simulation_run_ids),
        used_free_entitlement=used_free_entitlement,
        reserved_chips=reserved_chips,
        warnings=tuple(stale_cta_warnings),
    )


__all__ = [
    "ACCESSIBILITY_PRECHECK",
    "AB_COMPARISON",
    "AI_GENERATION_ASSET_MISMATCH_MESSAGE",
    "AI_GENERATION_UNAVAILABLE_MESSAGE",
    "AI_INTERACTION_HEATMAP_NOT_READY_MESSAGE",
    "ANALYSIS_MODULES",
    "CTA_ANNOTATION_ASSET_MISMATCH_MESSAGE",
    "CTA_ANNOTATION_FIELD_TO_ASSET_FIELD",
    "CTA_ANNOTATION_FIELDS",
    "CTA_ANNOTATION_LARGE_AREA_WARNING",
    "CTA_SELECTION_CANDIDATE_CONFIRMATION",
    "CTA_SELECTION_MANUAL_BOX",
    "CTA_SELECTION_SOURCES",
    "EXISTING_SITE_BASIC_UX",
    "ENGINE_NOT_AVAILABLE_MESSAGE",
    "STALE_CTA_ANNOTATION_CLEARED_CODE",
    "MAX_PERSONA_COUNT",
    "MIN_PERSONA_COUNT",
    "NETWORK_DEVICE_TEST_SOURCE_REJECTION_MESSAGE",
    "NEW_SOURCE_TYPES",
    "PATCHABLE_FIELDS",
    "QUOTE_TEST_TYPE_BY_WIZARD_TYPE",
    "SCREENSHOT_ALLOWED_TEST_TYPES",
    "SCREENSHOT_ASSET_UNAVAILABLE_MESSAGE",
    "SOURCE_TYPE_AI_GENERATED",
    "SOURCE_TYPE_SCREENSHOT",
    "SOURCE_TYPE_URL",
    "SOURCE_TYPES",
    "WIZARD_TEST_TYPES",
    "DraftValidationError",
    "LaunchResult",
    "InsufficientChipBalanceError",
    "build_quote_for_payload",
    "cta_annotation_area_warning",
    "effective_new_source_type",
    "effective_source_type",
    "invalidate_stale_cta_annotations",
    "launch_draft",
    "merge_payload",
    "missing_fields_for_launch",
    "resolve_cta_annotation_patch",
    "revalidate_cta_annotation",
    "validate_ai_generation_ownership",
    "validate_interaction_heatmap_readiness",
    "validate_module_source_compatibility",
    "validate_patch_fields",
    "validate_screenshot_asset_ownership",
    "validate_source_type_for_test_type",
]
