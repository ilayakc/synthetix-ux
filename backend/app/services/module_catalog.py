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


@dataclass(frozen=True)
class AnalysisModuleDefinition:
    """Tek bir analiz modulunun katalog metadata'sini tutar."""

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

    def __post_init__(self) -> None:
        if self.measurement_type not in (TECHNICAL_MEASUREMENT, SYNTHETIC_ESTIMATE):
            raise ValueError(f"Bilinmeyen measurement_type: {self.measurement_type}")


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
}

CURRENT_MODULE_CATALOG_VERSION = "2026.1"


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


__all__ = [
    "SYNTHETIC_ESTIMATE",
    "TECHNICAL_MEASUREMENT",
    "AnalysisModuleDefinition",
    "CURRENT_MODULE_CATALOG_VERSION",
    "MODULE_CATALOG_VERSIONS",
    "get_active_module_catalog",
    "get_module_catalog",
    "get_selectable_wizard_module_keys",
]
