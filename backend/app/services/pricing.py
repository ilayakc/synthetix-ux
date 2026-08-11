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

# --- AI raporu modulu (`ai_report`) fiyat kaynagi (Faz 3C.2B1) -------------
# `ai_report`, sihirbazda secilebilen digger gelismis modullerin aksine
# `advanced_module_chip_costs`e KONULMAZ: bedeli baseline/modul ucretinden
# AYRI, launch grubu basina TEK bir Chip rezervasyonu olarak tutulur (bkz.
# app.services.test_wizard.launch_draft, app.services.quotes.build_quote).
# Fiyat burada TEK bir yerde (single source of truth) tanimlanir; hem
# `PricingConfig.ai_report_chip_cost` hem de katalog metadata'si
# (app.services.module_catalog) bu sabiti kullanir - 50 iki yere hardcode
# EDILMEZ.
AI_REPORT_MODULE_KEY = "ai_report"
AI_REPORT_CHIP_COST = 50

# --- AI etkilesim isi haritasi modulu (`ai_interaction_heatmap`) fiyat kaynagi -
# `ai_report` ile AYNI desen: `advanced_module_chip_costs`e KONULMAZ, bedeli
# launch grubu basina TEK bir AYRI Chip rezervasyonu olarak tutulur (bkz.
# app.services.test_wizard.launch_draft) ve `ai_report`tan TAMAMEN BAGIMSIZ bir
# yasam donguson (rezervasyon/consume/release) izler. Iki modul birlikte
# secilirse iki AYRI rezervasyon olusur (kanit hazirligi paylasilsa da finansal
# yasam donguleri ayridir). Fiyat burada TEK kaynaktan gelir; katalog metadata'si
# (app.services.module_catalog) bu sabiti kullanir - deger iki yere hardcode
# EDILMEZ.
AI_INTERACTION_HEATMAP_MODULE_KEY = "ai_interaction_heatmap"
AI_INTERACTION_HEATMAP_CHIP_COST = 30

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
    # `ai_report` (AI raporu) modulunun launch grubu basina flat Chip bedeli.
    # `advanced_module_chip_costs`ten KASITLI olarak ayridir (ayri rezervasyon
    # yasam dongusu, per-grup tek ucret - bkz. AI_REPORT_CHIP_COST). Eski
    # surumlerde 0'dir (ai_report o surumlerde tanimli degildi).
    ai_report_chip_cost: int = 0
    # `ai_interaction_heatmap` (AI etkilesim isi haritasi) modulunun launch grubu
    # basina flat Chip bedeli - `ai_report_chip_cost` ile AYNI desen ama AYRI bir
    # rezervasyon (bkz. AI_INTERACTION_HEATMAP_CHIP_COST). Eski surumlerde 0'dir.
    interaction_heatmap_chip_cost: int = 0

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
    # "2026.2"nin tum satirlari degismeden korunur (eski quote/run/rapor
    # kayitlari onceki surumleriyle pinlenmis kalir). "2026.3" yalnizca yeni
    # `ai_report` (AI raporu) modulunun launch grubu basina flat Chip bedelini
    # (`ai_report_chip_cost`, bkz. AI_REPORT_CHIP_COST) ekler; mevcut modul
    # fiyatlari AYNEN korunur.
    "2026.3": PricingConfig(
        version="2026.3",
        basic_ux_test_chip_per_persona=1,
        accessibility_precheck_chip_cost=30,
        advanced_module_chip_costs={
            "advanced_simulation": 50,
            "extended_reporting": 20,
            "network_device_test": 40,
            "campaign_cta_test": 35,
            "synthetic_attention_estimate": 25,
        },
        ai_report_chip_cost=AI_REPORT_CHIP_COST,
    ),
    # "2026.3"un TUM alanlari degismeden korunur (eski quote/run/rapor kayitlari
    # onceki surumleriyle pinlenmis kalir). "2026.4" yalnizca yeni
    # `ai_interaction_heatmap` (AI etkilesim isi haritasi) modulunun launch grubu
    # basina flat Chip bedelini (`interaction_heatmap_chip_cost`, bkz.
    # AI_INTERACTION_HEATMAP_CHIP_COST) ekler; mevcut modul/AI raporu fiyatlari
    # AYNEN korunur.
    "2026.4": PricingConfig(
        version="2026.4",
        basic_ux_test_chip_per_persona=1,
        accessibility_precheck_chip_cost=30,
        advanced_module_chip_costs={
            "advanced_simulation": 50,
            "extended_reporting": 20,
            "network_device_test": 40,
            "campaign_cta_test": 35,
            "synthetic_attention_estimate": 25,
        },
        ai_report_chip_cost=AI_REPORT_CHIP_COST,
        interaction_heatmap_chip_cost=AI_INTERACTION_HEATMAP_CHIP_COST,
    ),
}

CURRENT_PRICING_VERSION = "2026.4"


def get_pricing_config(version: str | None = None) -> PricingConfig:
    """Verilen surumun (veya guncel surumun) fiyatlandirma yapilandirmasini dondurur."""

    key = version or CURRENT_PRICING_VERSION
    try:
        return PRICING_VERSIONS[key]
    except KeyError as exc:
        raise ValueError(f"Bilinmeyen fiyatlandirma surumu: {key}") from exc
