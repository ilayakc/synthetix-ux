"""Analiz modulu katalogu: surumleme, filtreleme ve API sozlesmesi testleri."""

import pytest

from app.services import module_catalog

pytestmark = pytest.mark.unit


def test_get_module_catalog_returns_current_version_by_default():
    modules = module_catalog.get_module_catalog()
    assert len(modules) == 7
    assert {m.key for m in modules} == {
        "basic_ux_test",
        "accessibility_precheck",
        "ab_comparison",
        "network_device_test",
        "campaign_cta_test",
        "synthetic_attention_estimate",
        "ai_explanation",
    }


def test_get_module_catalog_rejects_unknown_version():
    with pytest.raises(ValueError):
        module_catalog.get_module_catalog("9999.99-does-not-exist")


def test_get_active_module_catalog_excludes_inactive_modules():
    # Katalogdaki tum girdiler varsayilan olarak aktif; explicit inactive bir
    # kopya olusturup filtrenin gercekten calistigini dogrula.
    import dataclasses

    active_modules = module_catalog.get_module_catalog()
    inactive_copy = dataclasses.replace(active_modules[0], active=False)
    patched_version = (inactive_copy, *active_modules[1:])

    original = module_catalog.MODULE_CATALOG_VERSIONS["2026.1"]
    module_catalog.MODULE_CATALOG_VERSIONS["2026.1"] = patched_version
    try:
        active = module_catalog.get_active_module_catalog()
        assert inactive_copy.key not in {m.key for m in active}
        assert len(active) == len(active_modules) - 1
    finally:
        module_catalog.MODULE_CATALOG_VERSIONS["2026.1"] = original


def test_get_selectable_wizard_module_keys_only_returns_selectable_advanced_modules():
    keys = module_catalog.get_selectable_wizard_module_keys()
    assert set(keys) == {"network_device_test", "campaign_cta_test", "synthetic_attention_estimate"}
    # Test turleri (test_type) ve her zaman ucretsiz AI aciklamasi sihirbazin
    # 4. adiminda AYRICA secilebilir olarak listelenmemeli.
    assert "basic_ux_test" not in keys
    assert "accessibility_precheck" not in keys
    assert "ab_comparison" not in keys
    assert "ai_explanation" not in keys


def test_analysis_module_definition_rejects_unknown_measurement_type():
    with pytest.raises(ValueError):
        module_catalog.AnalysisModuleDefinition(
            key="x",
            name="x",
            description="x",
            outputs=(),
            measurement_type="not_a_real_type",
            chip_cost=0,
            free_entitlement_feature_key=None,
            estimated_duration_minutes=1,
            selectable_in_wizard=False,
        )


def test_new_advanced_modules_have_nonzero_chip_cost_and_no_free_entitlement():
    modules = {m.key: m for m in module_catalog.get_module_catalog()}
    for key in ("network_device_test", "campaign_cta_test", "synthetic_attention_estimate"):
        assert modules[key].chip_cost > 0
        assert modules[key].free_entitlement_feature_key is None


def test_ai_explanation_module_is_always_free_and_not_selectable():
    modules = {m.key: m for m in module_catalog.get_module_catalog()}
    ai_module = modules["ai_explanation"]
    assert ai_module.chip_cost == 0
    assert ai_module.selectable_in_wizard is False
