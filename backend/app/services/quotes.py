"""Teklif (quote) servisi.

Girdi (persona sayisi, modul listesi, test tipi) icin hangi ucretsiz hakkin
kullanilacagini, ne kadar Chip gerektigini ve fiyatlandirmanin hangi
surume gore hesaplandigini dondurur. Salt okunurdur: hicbir rezervasyon
veya defter satiri yazmaz (yalnizca entitlement satirlarini lazily "get or
create" edebilir, boylece ilk cagrida da dogru durumu gosterir).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import EntitlementStatus
from app.services.entitlements import get_or_create_entitlement
from app.services.pricing import (
    AI_REPORT_MODULE_KEY,
    FEATURE_ACCESSIBILITY_PRECHECK,
    FEATURE_BASIC_UX_TEST,
    FREE_BASIC_UX_TEST_PERSONA_LIMIT,
    PricingConfig,
    get_pricing_config,
)


@dataclass(frozen=True)
class QuoteLineItem:
    key: str
    label: str
    quantity: int
    unit_chip_cost: int
    chip_cost: int
    covered_by_free_entitlement: bool = False


@dataclass(frozen=True)
class QuoteResult:
    pricing_version: str
    test_type: str
    persona_count: int
    modules: tuple = ()
    free_entitlement_feature_key: str | None = None
    free_entitlement_applicable: bool = False
    line_items: tuple = ()
    # `required_chips`: baseline/temel + `ai_report` DISINDAKI gelismis modul
    # Chip'i - launch aninda MEVCUT baseline rezervasyonu icin kullanilir
    # (davranisi degismez). `ai_report_chips`: yalnizca `ai_report` seciliyse
    # 50, degilse 0 - AYRI bir AI rezervasyonu icin kullanilir (bkz.
    # app.services.test_wizard.launch_draft). `total_chips` ikisinin toplamidir
    # (kullanicinin gorecegi toplam maliyet). AI seciliyse `required_chips`
    # AI ucretini ICERMEZ (cift ucret/cift rezervasyon olmaz).
    required_chips: int = 0
    ai_report_chips: int = 0
    total_chips: int = 0


async def build_quote(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    persona_count: int,
    test_type: str,
    modules: list[str] | None = None,
    pricing_version: str | None = None,
) -> QuoteResult:
    if persona_count < 0:
        raise ValueError("persona_count negatif olamaz")
    if test_type not in (FEATURE_BASIC_UX_TEST, FEATURE_ACCESSIBILITY_PRECHECK):
        raise ValueError(f"Bilinmeyen test_type: {test_type}")

    modules = modules or []
    pricing = get_pricing_config(pricing_version)

    line_items: list[QuoteLineItem] = []

    entitlement = await get_or_create_entitlement(session, organization_id, test_type)
    entitlement_available = entitlement.status == EntitlementStatus.AVAILABLE

    if test_type == FEATURE_BASIC_UX_TEST:
        test_line, free_applicable = _quote_basic_ux_test(pricing, persona_count, entitlement_available)
    else:
        test_line, free_applicable = _quote_accessibility_precheck(pricing, entitlement_available)
    line_items.append(test_line)

    # `ai_report`, digger gelismis modullerden AYRI ele alinir: bedeli
    # `advanced_module_chip_costs`ten (dolayisiyla `required_chips`ten) DEGIL,
    # `pricing.ai_report_chip_cost`ten gelir ve ayri bir line item + ayri bir
    # rezervasyon olur (bkz. QuoteResult docstring / test_wizard.launch_draft).
    ai_report_selected = AI_REPORT_MODULE_KEY in modules
    for module_key in modules:
        if module_key == AI_REPORT_MODULE_KEY:
            continue
        unit_cost = pricing.module_cost(module_key)
        line_items.append(
            QuoteLineItem(
                key=module_key,
                label=f"Gelismis modul: {module_key}",
                quantity=1,
                unit_chip_cost=unit_cost,
                chip_cost=unit_cost,
            )
        )

    # baseline rezervasyon tutari: `ai_report` HARIC tum line item'lar.
    required_chips = sum(item.chip_cost for item in line_items)

    ai_report_chips = pricing.ai_report_chip_cost if ai_report_selected else 0
    if ai_report_selected:
        # AI ucreti ayri bir line item olarak GORUNUR (frontend'de ayrik
        # gosterim), ama `required_chips`e (baseline) EKLENMEZ.
        line_items.append(
            QuoteLineItem(
                key=AI_REPORT_MODULE_KEY,
                label="AI raporu (launch grubu basina)",
                quantity=1,
                unit_chip_cost=ai_report_chips,
                chip_cost=ai_report_chips,
            )
        )

    return QuoteResult(
        pricing_version=pricing.version,
        test_type=test_type,
        persona_count=persona_count,
        modules=tuple(modules),
        free_entitlement_feature_key=test_type if free_applicable else None,
        free_entitlement_applicable=free_applicable,
        line_items=tuple(line_items),
        required_chips=required_chips,
        ai_report_chips=ai_report_chips,
        total_chips=required_chips + ai_report_chips,
    )


def _quote_basic_ux_test(
    pricing: PricingConfig, persona_count: int, entitlement_available: bool
) -> tuple[QuoteLineItem, bool]:
    within_free_limit = persona_count <= FREE_BASIC_UX_TEST_PERSONA_LIMIT
    free_applicable = within_free_limit and entitlement_available

    if free_applicable:
        return (
            QuoteLineItem(
                key=FEATURE_BASIC_UX_TEST,
                label=f"Temel UX testi ({persona_count} persona) - ucretsiz hak",
                quantity=persona_count,
                unit_chip_cost=0,
                chip_cost=0,
                covered_by_free_entitlement=True,
            ),
            True,
        )

    chip_cost = persona_count * pricing.basic_ux_test_chip_per_persona
    label = f"Temel UX testi ({persona_count} persona)"
    if not within_free_limit:
        label += " - 1.000 persona sinirini asiyor"
    else:
        # Bu satira yalnizca `within_free_limit` True iken ulasilir; `free_applicable`
        # burada False oldugundan (yukarida kontrol edildi) `entitlement_available`
        # zorunlu olarak False'tur - ayri bir `elif not entitlement_available`
        # kontrolu asla yanlis olamayacagi icin (olasiliksal olarak erisilemez
        # bir dal yaratmamak adina) kaldirilmistir.
        label += " - ucretsiz hak zaten kullanildi"

    return (
        QuoteLineItem(
            key=FEATURE_BASIC_UX_TEST,
            label=label,
            quantity=persona_count,
            unit_chip_cost=pricing.basic_ux_test_chip_per_persona,
            chip_cost=chip_cost,
        ),
        False,
    )


def _quote_accessibility_precheck(
    pricing: PricingConfig, entitlement_available: bool
) -> tuple[QuoteLineItem, bool]:
    if entitlement_available:
        return (
            QuoteLineItem(
                key=FEATURE_ACCESSIBILITY_PRECHECK,
                label="Erisilebilirlik on kontrolu - ucretsiz hak",
                quantity=1,
                unit_chip_cost=0,
                chip_cost=0,
                covered_by_free_entitlement=True,
            ),
            True,
        )

    return (
        QuoteLineItem(
            key=FEATURE_ACCESSIBILITY_PRECHECK,
            label="Erisilebilirlik on kontrolu - ucretsiz hak zaten kullanildi",
            quantity=1,
            unit_chip_cost=pricing.accessibility_precheck_chip_cost,
            chip_cost=pricing.accessibility_precheck_chip_cost,
        ),
        False,
    )
