"""Merkezi, surumlenebilir analiz modulu kataloğu.

`app.services.pricing`'in surumlenme deseniyle aynidir: modul metadata'si
(ad, aciklama, ciktilar, olcum turu, tahmini sure, vb.) kodun geneline
dagitik sabitler olarak gomulmez; tek bir surumlu yapida
(`MODULE_CATALOG_VERSIONS`) tutulur. Yeni bir katalog surumu yapmak icin
`MODULE_CATALOG_VERSIONS`'a yeni bir surum eklenir ve
`CURRENT_MODULE_CATALOG_VERSION` guncellenir; eski surumler geriye donuk
olarak degistirilmez.

Bir modulun `active=False` yapilmasi (veya `MODULE_CATALOG_VERSIONS`'a yeni
bir surumde kaldirilmasi), onu API'den ve sihirbazdan gizler; bu sekilde
modul katalogu, kod dagitmadan (yalniz yapilandirma degisikligiyle)
aktif/pasif yonetilebilir.

Uc modul (temel UX testi, erisilebilirlik on kontrolu, A/B karsilastirmasi),
sihirbazin 1. adiminda secilen `test_type`'a karsilik gelir; bunlarin
fiyatlandirmasi/ucretsiz hak uygunlugu zaten `app.services.quotes` tarafindan
yonetilir - katalogdaki karsiliklari yalnizca ACIKLAYICIDIR
(`selectable_in_wizard=False`). "AI destekli aciklama" da rapor sayfasindan
tetiklenen, her zaman ucretsiz/olcumsuz bir ozelliktir (bkz.
`app.services.ai_explanations`) ve sihirbazda secilemez. Geriye kalan uc
modul (`network_device_test`, `campaign_cta_test`,
`synthetic_attention_estimate`) sihirbazin 4. adiminda secilebilen,
Chip gerektiren "gelismis modullerdir" (bkz. `pricing.advanced_module_chip_costs`).
"""

from dataclasses import dataclass

from app.services.pricing import (
    AI_INTERACTION_HEATMAP_CHIP_COST,
    AI_INTERACTION_HEATMAP_MODULE_KEY,
    AI_REPORT_CHIP_COST,
    AI_REPORT_MODULE_KEY,
    FEATURE_ACCESSIBILITY_PRECHECK,
    FEATURE_BASIC_UX_TEST,
)

TECHNICAL_MEASUREMENT = "technical_measurement"
SYNTHETIC_ESTIMATE = "synthetic_estimate"

# --- Tasarim kaynagi turleri (bkz. app.services.test_wizard) --------------
#
# Bu degerler `app.services.test_wizard.SOURCE_TYPE_*` ile AYNIDIR - burada
# tekrar tanimlanir cunku modul katalogu, sihirbaz servisine bagimlilik
# eklemeden (dongusel import riski olmadan) bagimsiz kalabilmelidir; iki
# tanim kasitli olarak ayni string degerlere sahiptir.
SOURCE_TYPE_URL = "url"
SOURCE_TYPE_SCREENSHOT = "screenshot"
SOURCE_TYPE_AI_GENERATED = "ai_generated"
ALL_SOURCE_TYPES = (SOURCE_TYPE_URL, SOURCE_TYPE_SCREENSHOT, SOURCE_TYPE_AI_GENERATED)


@dataclass(frozen=True)
class AnalysisModuleDefinition:
    """Tek bir analiz modulunun katalog metadata'sini tutar.

    `supported_source_types`: bu modulun HANGI tasarim kaynagi turleriyle
    (url/screenshot/ai_generated) calisabilecegini bildiren, tek gercek
    kaynak (single source of truth) capability alanidir. Ornegin
    `network_device_test` gercek bir canli URL'ye HTTP istegi atarak
    olcum yaptigi icin yalnizca `("url",)` destekler - statik bir
    screenshot/AI taslagi uzerinde gercek ag/yukleme olcumu YAPILAMAZ.
    Bu alan, launch-zamani dogrulamasinin (bkz. app.services.test_wizard.
    validate_module_source_compatibility) VE frontend'in (bkz.
    Step4Modules/ModuleCatalogCard) modul-kaynak uyumluluk kararinin tek
    ortak kaynagidir - iki tarafta da ayrica hardcode edilmez.
    """

    key: str
    name: str
    description: str
    outputs: tuple[str, ...]
    measurement_type: str
    chip_cost: int
    free_entitlement_feature_key: str | None
    estimated_duration_minutes: int
    selectable_in_wizard: bool
    active: bool = True
    supported_source_types: tuple[str, ...] = ALL_SOURCE_TYPES

    def __post_init__(self) -> None:
        if self.measurement_type not in (TECHNICAL_MEASUREMENT, SYNTHETIC_ESTIMATE):
            raise ValueError(f"Bilinmeyen measurement_type: {self.measurement_type}")
        unknown_source_types = set(self.supported_source_types) - set(ALL_SOURCE_TYPES)
        if unknown_source_types:
            raise ValueError(f"Bilinmeyen supported_source_types: {sorted(unknown_source_types)}")


MODULE_CATALOG_VERSIONS: dict[str, tuple[AnalysisModuleDefinition, ...]] = {
    "2026.1": (
        AnalysisModuleDefinition(
            key="basic_ux_test",
            name="Temel UX testi",
            description=(
                "Mevcut bir sitenin hedef gorev akisini sentetik persona kohortlariyla "
                "degerlendiren temel kullanilabilirlik testi."
            ),
            outputs=(
                "Gorev tamamlama olasiligi",
                "Tahmini gorev suresi",
                "Yanlis tiklama/terk etme olasiligi",
            ),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=0,
            free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
            estimated_duration_minutes=5,
            selectable_in_wizard=False,
        ),
        AnalysisModuleDefinition(
            key="accessibility_precheck",
            name="Erisilebilirlik on kontrolu",
            description=(
                "Renk kontrasti ve temel WCAG kurallarina dayali, deterministik bir "
                "erisilebilirlik on taramasi."
            ),
            outputs=("Kontrast kontrol sonucu", "Erisilebilirlik bulgulari listesi"),
            measurement_type=TECHNICAL_MEASUREMENT,
            chip_cost=0,
            free_entitlement_feature_key=FEATURE_ACCESSIBILITY_PRECHECK,
            estimated_duration_minutes=3,
            selectable_in_wizard=False,
        ),
        AnalysisModuleDefinition(
            key="ab_comparison",
            name="A/B tasarim karsilastirmasi",
            description="Mevcut ve yeni tasarimin ayni gorev icin sentetik olarak karsilastirilmasi.",
            outputs=("Varyant bazli metrik karsilastirmasi", "Delta ozetleri"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=0,
            free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
            estimated_duration_minutes=8,
            selectable_in_wizard=False,
        ),
        AnalysisModuleDefinition(
            key="network_device_test",
            name="Ag ve cihaz testi",
            description="Farkli ag hizi/cihaz profillerinde sayfa yukleme ve erisilebilirlik olcumu.",
            outputs=("Cihaz/ag profili basina yukleme sureleri", "Profil bazli hata orani"),
            measurement_type=TECHNICAL_MEASUREMENT,
            chip_cost=40,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=6,
            selectable_in_wizard=True,
        ),
        AnalysisModuleDefinition(
            key="campaign_cta_test",
            name="Kampanya ve CTA testi",
            description="Kampanya sayfalarindaki harekete gecirici (CTA) ogelerin sentetik etkilesim tahmini.",
            outputs=("CTA bazli tiklama olasiligi tahmini", "Kampanya mesaji netligi bulgulari"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=35,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=5,
            selectable_in_wizard=True,
        ),
        AnalysisModuleDefinition(
            key="synthetic_attention_estimate",
            name="Sentetik dikkat tahmini",
            description=(
                "Sayfa duzeninden turetilen, kalibre edilmemis bir sentetik dikkat/gorsel "
                "aginlik tahmini (gercek goz izleme verisi degildir)."
            ),
            outputs=("Sentetik dikkat yogunluk haritasi ozeti", "Alan bazli tahmini ilgi puani"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=25,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=4,
            selectable_in_wizard=True,
        ),
        AnalysisModuleDefinition(
            key="ai_explanation",
            name="Hızlı rapor özeti",
            description=(
                "Mevcut rapor sonuçlarını kısaca açıklayan otomatik bir özet; "
                "yeni bir AI analizi üretmez."
            ),
            outputs=("Kisa ozet", "Olasi aciklamalar", "Onerilen dogrulama deneyi"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=0,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=1,
            selectable_in_wizard=False,
        ),
    ),
    # "2026.1"in tum satirlari degismeden korunur (eski katalog referansi
    # veren kayitlar bozulmaz - bkz. pricing.py'deki ayni surumleme deseni).
    # "2026.2" yalnizca `supported_source_types` capability alanini acikca
    # ekler: `network_device_test` gercek bir canli URL'ye ihtiyac duydugu
    # icin yalnizca "url" destekler; digger tum moduller URL/screenshot/
    # ai_generated uzerinde calisabilir (bkz. AnalysisModuleDefinition
    # docstring'i - bu, ekran goruntusu kaynaklariyla network_device_test
    # arasindaki uyumsuzlugun launch-oncesi engellenmesinin tek gercek
    # kaynagidir).
    "2026.2": (
        AnalysisModuleDefinition(
            key="basic_ux_test",
            name="Temel UX testi",
            description=(
                "Mevcut bir sitenin hedef gorev akisini sentetik persona kohortlariyla "
                "degerlendiren temel kullanilabilirlik testi."
            ),
            outputs=(
                "Gorev tamamlama olasiligi",
                "Tahmini gorev suresi",
                "Yanlis tiklama/terk etme olasiligi",
            ),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=0,
            free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
            estimated_duration_minutes=5,
            selectable_in_wizard=False,
            supported_source_types=ALL_SOURCE_TYPES,
        ),
        AnalysisModuleDefinition(
            key="accessibility_precheck",
            name="Erisilebilirlik on kontrolu",
            description=(
                "Renk kontrasti ve temel WCAG kurallarina dayali, deterministik bir "
                "erisilebilirlik on taramasi."
            ),
            outputs=("Kontrast kontrol sonucu", "Erisilebilirlik bulgulari listesi"),
            measurement_type=TECHNICAL_MEASUREMENT,
            chip_cost=0,
            free_entitlement_feature_key=FEATURE_ACCESSIBILITY_PRECHECK,
            estimated_duration_minutes=3,
            selectable_in_wizard=False,
            supported_source_types=(SOURCE_TYPE_URL,),
        ),
        AnalysisModuleDefinition(
            key="ab_comparison",
            name="A/B tasarim karsilastirmasi",
            description="Mevcut ve yeni tasarimin ayni gorev icin sentetik olarak karsilastirilmasi.",
            outputs=("Varyant bazli metrik karsilastirmasi", "Delta ozetleri"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=0,
            free_entitlement_feature_key=FEATURE_BASIC_UX_TEST,
            estimated_duration_minutes=8,
            selectable_in_wizard=False,
            supported_source_types=ALL_SOURCE_TYPES,
        ),
        AnalysisModuleDefinition(
            key="network_device_test",
            name="Ag ve cihaz testi",
            description="Farkli ag hizi/cihaz profillerinde sayfa yukleme ve erisilebilirlik olcumu.",
            outputs=("Cihaz/ag profili basina yukleme sureleri", "Profil bazli hata orani"),
            measurement_type=TECHNICAL_MEASUREMENT,
            chip_cost=40,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=6,
            selectable_in_wizard=True,
            supported_source_types=(SOURCE_TYPE_URL,),
        ),
        AnalysisModuleDefinition(
            key="campaign_cta_test",
            name="Kampanya ve CTA testi",
            description="Kampanya sayfalarindaki harekete gecirici (CTA) ogelerin sentetik etkilesim tahmini.",
            outputs=("CTA bazli tiklama olasiligi tahmini", "Kampanya mesaji netligi bulgulari"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=35,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=5,
            selectable_in_wizard=True,
            supported_source_types=ALL_SOURCE_TYPES,
        ),
        AnalysisModuleDefinition(
            key="synthetic_attention_estimate",
            name="Sentetik dikkat tahmini",
            description=(
                "Sayfa duzeninden turetilen, kalibre edilmemis bir sentetik dikkat/gorsel "
                "aginlik tahmini (gercek goz izleme verisi degildir)."
            ),
            outputs=("Sentetik dikkat yogunluk haritasi ozeti", "Alan bazli tahmini ilgi puani"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=25,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=4,
            selectable_in_wizard=True,
            supported_source_types=ALL_SOURCE_TYPES,
        ),
        AnalysisModuleDefinition(
            key="ai_explanation",
            name="Hızlı rapor özeti",
            description=(
                "Mevcut rapor sonuçlarını kısaca açıklayan otomatik bir özet; "
                "yeni bir AI analizi üretmez."
            ),
            outputs=("Kisa ozet", "Olasi aciklamalar", "Onerilen dogrulama deneyi"),
            measurement_type=SYNTHETIC_ESTIMATE,
            chip_cost=0,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=1,
            selectable_in_wizard=False,
            supported_source_types=ALL_SOURCE_TYPES,
        ),
    ),
}

# "2026.2"nin TUM satirlari degismeden korunur (yukaridaki dict literal'i
# bozulmaz); "2026.3" onlari AYNEN devralir ve yalnizca yeni `ai_report` (AI
# raporu) modulunu EKLER. Bu, hem eski surum referanslarini byte-for-byte
# korur hem de 7 mevcut girdinin tekrar yazilmasini (kopyalama hatasi riskini)
# onler.
#
# `ai_report`, `ai_explanation` ile KARISTIRILMAMALIDIR: `ai_explanation`
# rapor sayfasindan tetiklenen, her zaman ucretsiz (chip_cost=0),
# `selectable_in_wizard=False` bir aciklama servisidir; `ai_report` ise
# sihirbazda secilebilen, launch grubu basina AYRI 50 Chip rezerve edilen
# (bkz. app.services.pricing.AI_REPORT_CHIP_COST) yeni bir pipeline modulu-
# dur. Fiyat TEK kaynaktan (`AI_REPORT_CHIP_COST`) gelir; `50` burada tekrar
# hardcode EDILMEZ. `selectable_in_wizard=True` katalog-seviyesi bir niyettir;
# GERCEK sihirbaz gorunurlugu ayrica calisma-zamani hazirlik bayragiyla
# (`settings.ai_report_enabled`) kapilanir (bkz. `get_wizard_visible_modules`
# ve app.routers.analysis_modules) - hazirlik `false` iken modul katalog
# yanitinda GORUNMEZ ve launch reddedilir (bkz. app.services.test_wizard).
# Stage 1 evidence herhangi bir simulasyon sonucundan turetilebildigi icin
# tum kaynak turlerini (url/screenshot/ai_generated) destekler.
MODULE_CATALOG_VERSIONS["2026.3"] = (
    *MODULE_CATALOG_VERSIONS["2026.2"],
    AnalysisModuleDefinition(
        key=AI_REPORT_MODULE_KEY,
        name="AI raporu",
        description=(
            "Simulasyon sonuclarindan cok asamali bir AI pipeline ile uretilen, "
            "senaryo yorumu ve persona davranisi analizini iceren gelismis rapor. "
            "Launch grubu basina tek ucretlendirilir (A/B'de iki varyant icin de "
            "tek AI ucreti)."
        ),
        outputs=("Senaryo yorumu", "Persona davranis ozeti", "AI destekli UX raporu"),
        measurement_type=SYNTHETIC_ESTIMATE,
        chip_cost=AI_REPORT_CHIP_COST,
        free_entitlement_feature_key=None,
        estimated_duration_minutes=6,
        selectable_in_wizard=True,
        supported_source_types=ALL_SOURCE_TYPES,
    ),
)

# "2026.3"un TUM satirlari degismeden korunur; "2026.4" onlari AYNEN devralir ve
# yalnizca yeni `ai_interaction_heatmap` (AI etkilesim isi haritasi) modulunu
# EKLER. Bu modul, gorev-odakli bir AI TIKLAMA TAHMINI uretir: gercek OpenAI
# provider'i worker icinde cagrilir, yalnizca dogrulanmis analyzer aday
# kayitlarindan (`candidate_id`) secim yapar - koordinat URETMEZ (bkz.
# app.services.ai_interaction_heatmap). `ai_report`tan TAMAMEN BAGIMSIZDIR:
# yalnizca `ai_interaction_heatmap` secilirse Isı Haritası (AI tiklama tahmini)
# olusur, AI Raporu OLUSMAZ; yalnizca `ai_report` secilirse AI Raporu olusur, AI
# tiklama tahmini OLUSMAZ; ikisi birlikte secilirse kanit hazirligi paylasilabilir
# ama durum ve Chip yasam dongulieri AYRIDIR. Fiyat TEK kaynaktan
# (`AI_INTERACTION_HEATMAP_CHIP_COST`) gelir. `selectable_in_wizard=True` katalog-
# seviyesi bir niyettir; GERCEK sihirbaz gorunurlugu ayrica calisma-zamani
# hazirlik bayragiyla (`settings.ai_interaction_heatmap_enabled`) kapilanir (bkz.
# `get_wizard_visible_modules`) - hazirlik `false` iken modul katalog yanitinda
# GORUNMEZ ve launch reddedilir (bkz. app.services.test_wizard). Evidence tum
# kaynak turlerinden (url/screenshot/ai_generated) turetilebilir.
MODULE_CATALOG_VERSIONS["2026.4"] = (
    *MODULE_CATALOG_VERSIONS["2026.3"],
    AnalysisModuleDefinition(
        key=AI_INTERACTION_HEATMAP_MODULE_KEY,
        name="AI etkilesim isi haritasi",
        description=(
            "Hedef gorev, hedef kitle ve sayfadaki gercek etkilesim alanlari "
            "kullanilarak gercek bir AI ile uretilen sentetik tiklama tahmini. AI "
            "yalnizca dogrulanmis etkilesim adaylarindan secim yapar; koordinat "
            "uretmez. Gercek kullanici tiklamasi veya goz takibi verisi degildir. "
            "Launch grubu basina tek ucretlendirilir."
        ),
        outputs=(
            "AI tiklama tahmini (hedef gorev icin daha olası etkilesim alanlari)",
            "Gorsel dikkat tahmini",
            "Hotspot aciklama kartlari",
        ),
        measurement_type=SYNTHETIC_ESTIMATE,
        chip_cost=AI_INTERACTION_HEATMAP_CHIP_COST,
        free_entitlement_feature_key=None,
        estimated_duration_minutes=4,
        selectable_in_wizard=True,
        supported_source_types=ALL_SOURCE_TYPES,
    ),
)

CURRENT_MODULE_CATALOG_VERSION = "2026.4"


def get_module_catalog(version: str | None = None) -> tuple[AnalysisModuleDefinition, ...]:
    """Verilen surumun (veya guncel surumun) tam analiz modulu katalogunu dondurur."""

    key = version or CURRENT_MODULE_CATALOG_VERSION
    try:
        return MODULE_CATALOG_VERSIONS[key]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen modul katalogu surumu: {key}") from exc


def get_active_module_catalog(version: str | None = None) -> tuple[AnalysisModuleDefinition, ...]:
    """Yalnizca `active=True` olan katalog girdilerini dondurur (API'de gosterilenler)."""

    return tuple(module for module in get_module_catalog(version) if module.active)


def get_selectable_wizard_module_keys(version: str | None = None) -> tuple[str, ...]:
    """Sihirbazin 4. adiminda secilebilen (aktif + `selectable_in_wizard`) modul anahtarlari.

    `app.services.test_wizard.ANALYSIS_MODULES` bu fonksiyonu kullanir; boylece
    hangi modullerin sihirbazda secilebilecegi tek bir yerden (katalog) yonetilir.
    """

    return tuple(
        module.key for module in get_module_catalog(version) if module.active and module.selectable_in_wizard
    )


def get_wizard_visible_modules(
    *,
    ai_report_enabled: bool,
    ai_interaction_heatmap_enabled: bool = False,
    version: str | None = None,
) -> tuple[AnalysisModuleDefinition, ...]:
    """Sihirbaz/katalog yanitinda GORUNMESI gereken aktif modulleri, calisma-
    zamani hazirlik bayraklarini uygulayarak dondurur.

    `ai_report` ve `ai_interaction_heatmap` katalogda `selectable_in_wizard=True`
    olsa da GERCEK provider hazirligi ayri iki bayrakla (`settings.
    ai_report_enabled` / `settings.ai_interaction_heatmap_enabled`, ikisi de
    varsayilan `False`) kapilanir; ilgili hazirlik `False` iken o modul bu
    listeden CIKARILIR - frontend'in modulu hic gormemesi saglanir (yalnizca
    frontend gizlemesine guvenilmez; launch dogrulamasi da app.services.
    test_wizard'da ayrica zorunludur). Iki bayrak BAGIMSIZDIR (biri acik digeri
    kapali olabilir). Diger tum aktif moduller bu bayraklardan BAGIMSIZ dondurulur.
    """

    gated: dict[str, bool] = {
        AI_REPORT_MODULE_KEY: ai_report_enabled,
        AI_INTERACTION_HEATMAP_MODULE_KEY: ai_interaction_heatmap_enabled,
    }
    return tuple(
        module
        for module in get_active_module_catalog(version)
        if gated.get(module.key, True)
    )


def get_module_definition(key: str, version: str | None = None) -> AnalysisModuleDefinition:
    """Verilen anahtara (`key`) sahip modul tanimini (guncel/verilen surumde) dondurur.

    Launch-oncesi kaynak-modul uyumluluk dogrulamasi (bkz.
    app.services.test_wizard.validate_module_source_compatibility) VE frontend'in
    tukettigi API response'u AYNI bu tek fonksiyona/katalog kaydina dayanir.
    """

    for module in get_module_catalog(version):
        if module.key == key:
            return module
    raise ValueError(f"Bilinmeyen analiz modulu: {key}")


__all__ = [
    "AI_INTERACTION_HEATMAP_CHIP_COST",
    "AI_INTERACTION_HEATMAP_MODULE_KEY",
    "AI_REPORT_CHIP_COST",
    "AI_REPORT_MODULE_KEY",
    "ALL_SOURCE_TYPES",
    "SOURCE_TYPE_AI_GENERATED",
    "SOURCE_TYPE_SCREENSHOT",
    "SOURCE_TYPE_URL",
    "SYNTHETIC_ESTIMATE",
    "TECHNICAL_MEASUREMENT",
    "AnalysisModuleDefinition",
    "CURRENT_MODULE_CATALOG_VERSION",
    "MODULE_CATALOG_VERSIONS",
    "get_active_module_catalog",
    "get_module_catalog",
    "get_module_definition",
    "get_selectable_wizard_module_keys",
    "get_wizard_visible_modules",
]
