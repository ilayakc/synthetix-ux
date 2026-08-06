"""Faz 3C.2B1: `ai_report` modulu katalog metadata'si, tek-kaynak fiyat ve
calisma-zamani hazirlik (readiness) gating testleri (MODULE/READINESS 1-3, 7).
"""

import pytest

from app.services import module_catalog
from app.services.pricing import AI_REPORT_CHIP_COST, get_pricing_config

pytestmark = pytest.mark.unit


def test_ai_report_module_present_with_expected_metadata():
    module = module_catalog.get_module_definition("ai_report")
    assert module.key == "ai_report"
    assert module.name  # kullaniciya gosterilen acik ad
    assert module.description
    assert module.free_entitlement_feature_key is None
    assert module.chip_cost == AI_REPORT_CHIP_COST == 50
    assert module.selectable_in_wizard is True
    # Stage 1 evidence herhangi bir simulasyon sonucundan turer -> tum kaynaklar.
    assert module.supported_source_types == module_catalog.ALL_SOURCE_TYPES


def test_ai_report_price_comes_from_single_source_of_truth():
    """Fiyat iki yere hardcode edilmez: katalog metadata'si ile pricing
    surumu AYNI `AI_REPORT_CHIP_COST` sabitinden gelir."""

    catalog_cost = module_catalog.get_module_definition("ai_report").chip_cost
    pricing_cost = get_pricing_config().ai_report_chip_cost
    assert catalog_cost == pricing_cost == AI_REPORT_CHIP_COST == 50


def test_ai_report_is_not_confused_with_ai_explanation():
    modules = {m.key: m for m in module_catalog.get_module_catalog()}
    ai_explanation = modules["ai_explanation"]
    ai_report = modules["ai_report"]
    assert ai_explanation.chip_cost == 0
    assert ai_explanation.selectable_in_wizard is False
    assert ai_report.chip_cost == 50
    assert ai_report.selectable_in_wizard is True


def test_wizard_visible_modules_hide_ai_report_when_readiness_false():
    """Hazirlik `False` iken `ai_report` sihirbaz/katalog gorunumunde YER ALMAZ."""

    visible = module_catalog.get_wizard_visible_modules(ai_report_enabled=False)
    keys = {m.key for m in visible}
    assert "ai_report" not in keys
    # Diger aktif moduller etkilenmez.
    assert "network_device_test" in keys
    assert "ai_explanation" in keys


def test_wizard_visible_modules_show_ai_report_when_readiness_true():
    visible = module_catalog.get_wizard_visible_modules(ai_report_enabled=True)
    assert "ai_report" in {m.key for m in visible}
