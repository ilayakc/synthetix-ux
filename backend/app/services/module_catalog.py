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
            name="AI destekli aciklama",
            description=(
                "Zaten hesaplanmis rapor metriklerinden otomatik uretilen, uzman "
                "degerlendirmesi gerektiren bir aciklama (bir 'AI karari' degildir)."
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
            name="AI destekli aciklama",
            description=(
                "Zaten hesaplanmis rapor metriklerinden otomatik uretilen, uzman "
                "degerlendirmesi gerektiren bir aciklama (bir 'AI karari' degildir)."
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

CURRENT_MODULE_CATALOG_VERSION = "2026.2"


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
]
