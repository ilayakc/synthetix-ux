"""Merkezi, surumlenebilir Chip fiyatlandirma yapilandirmasi.

Fiyatlandirma degerleri kodun geneline dagitik sabitler olarak gomulmez;
tek bir surumlu yapida (`PRICING_VERSIONS`) tutulur. Bir teklif (quote),
uretildigi anda gecerli olan surum kimligini de dondurur; boylece daha
once verilmis bir teklif, sonradan fiyatlandirma degisse bile hangi
surume gore hesaplandigini kaybetmez. Yeni bir fiyatlandirma yapmak icin
`PRICING_VERSIONS`'a yeni bir surum eklenir ve `CURRENT_PRICING_VERSION`
guncellenir; eski surumler geriye donuk olarak degistirilmez.
"""

from dataclasses import dataclass, field

# --- Ucretsiz haklarin feature_key'leri ----------------------------------
FEATURE_BASIC_UX_TEST = "basic_ux_test"
FEATURE_ACCESSIBILITY_PRECHECK = "accessibility_precheck"

FREE_ENTITLEMENT_FEATURE_KEYS = (FEATURE_BASIC_UX_TEST, FEATURE_ACCESSIBILITY_PRECHECK)

# docs/product-rules.md: "En fazla 1.000 persona icin 1 adet ucretsiz temel
# UX testi hakki". Bu limitin ustundeki persona sayisi ucretsiz hakki
# gecersiz kilar (test paralı hale gelir).
FREE_BASIC_UX_TEST_PERSONA_LIMIT = 1000


@dataclass(frozen=True)
class PricingConfig:
    """Tek bir fiyatlandirma surumunun tum Chip maliyetlerini tutar."""

    version: str
    # 1.000 persona sinirinin ustundeki (veya ucretsiz hak zaten
    # kullanilmis) temel UX testleri icin persona basina Chip maliyeti.
    basic_ux_test_chip_per_persona: int
    # Ucretsiz hak kullanilamadiginda sabit erisilebilirlik on kontrolu maliyeti.
    accessibility_precheck_chip_cost: int
    # Gelismis modul anahtari -> Chip maliyeti (limit ustu/ileri kullanim).
    advanced_module_chip_costs: dict = field(default_factory=dict)

    def module_cost(self, module_key: str) -> int:
        try:
            return self.advanced_module_chip_costs[module_key]
        except KeyError as exc:
            raise ValueError(f"Bilinmeyen gelismis modul: {module_key}") from exc


PRICING_VERSIONS: dict[str, PricingConfig] = {
    "2026.1": PricingConfig(
        version="2026.1",
        basic_ux_test_chip_per_persona=1,
        accessibility_precheck_chip_cost=30,
        advanced_module_chip_costs={
            "advanced_simulation": 50,
            "extended_reporting": 20,
        },
    ),
    # "2026.1"in tum satirlari degismeden korunur (eski quote/run/rapor
    # kayitlari `pricing_version="2026.1"` ile pinlenmis kalir); bu surum
    # yalnizca yeni analiz modulu kataloguna eklenen 3 gelismis modulun
    # (`network_device_test`, `campaign_cta_test`, `synthetic_attention_estimate`)
    # Chip maliyetlerini ekler (bkz. app.services.module_catalog).
    "2026.2": PricingConfig(
        version="2026.2",
        basic_ux_test_chip_per_persona=1,
        accessibility_precheck_chip_cost=30,
        advanced_module_chip_costs={
            "advanced_simulation": 50,
            "extended_reporting": 20,
            "network_device_test": 40,
            "campaign_cta_test": 35,
            "synthetic_attention_estimate": 25,
        },
    ),
}

CURRENT_PRICING_VERSION = "2026.2"


def get_pricing_config(version: str | None = None) -> PricingConfig:
    """Verilen surumun (veya guncel surumun) fiyatlandirma yapilandirmasini dondurur."""

    key = version or CURRENT_PRICING_VERSION
    try:
        return PRICING_VERSIONS[key]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen fiyatlandirma surumu: {key}") from exc
