import { describe, expect, it } from "vitest";
import type { AnalysisModuleDefinition } from "../../api/client";
import {
  activeSourceTypes,
  incompatibleSelectedModuleKeys,
  isModuleCompatibleWithSources,
} from "./moduleCompatibility";

function module(overrides: Partial<AnalysisModuleDefinition> = {}): AnalysisModuleDefinition {
  return {
    key: "network_device_test",
    name: "Ağ ve cihaz testi",
    description: "",
    outputs: [],
    measurement_type: "technical_measurement",
    chip_cost: 40,
    free_entitlement_feature_key: null,
    estimated_duration_minutes: 6,
    selectable_in_wizard: true,
    supported_source_types: ["url"],
    ...overrides,
  };
}

describe("activeSourceTypes", () => {
  it("kaynak turu belirtilmemisse varsayilan olarak url dondurur", () => {
    expect(activeSourceTypes({})).toEqual(["url"]);
  });

  it("tekli testte yalnizca current_source_type'i dondurur", () => {
    expect(activeSourceTypes({ current_source_type: "screenshot" })).toEqual(["screenshot"]);
  });

  it("A/B'de iki tarafi da dondurur", () => {
    expect(
      activeSourceTypes({
        test_type: "ab_comparison",
        current_source_type: "url",
        new_source_type: "screenshot",
      }),
    ).toEqual(["url", "screenshot"]);
  });
});

describe("isModuleCompatibleWithSources", () => {
  const networkModule = module({ supported_source_types: ["url"] });
  const ctaModule = module({
    key: "campaign_cta_test",
    supported_source_types: ["url", "screenshot", "ai_generated"],
  });

  it("tekli URL kaynaginda network_device_test uyumludur", () => {
    expect(isModuleCompatibleWithSources(networkModule, { current_source_type: "url" })).toBe(true);
  });

  it("tekli screenshot kaynaginda network_device_test uyumsuzdur", () => {
    expect(isModuleCompatibleWithSources(networkModule, { current_source_type: "screenshot" })).toBe(
      false,
    );
  });

  it("A/B URL/URL'de network_device_test uyumludur", () => {
    expect(
      isModuleCompatibleWithSources(networkModule, {
        test_type: "ab_comparison",
        current_source_type: "url",
        new_source_type: "url",
      }),
    ).toBe(true);
  });

  it("A/B URL/screenshot'ta network_device_test uyumsuzdur", () => {
    expect(
      isModuleCompatibleWithSources(networkModule, {
        test_type: "ab_comparison",
        current_source_type: "url",
        new_source_type: "screenshot",
      }),
    ).toBe(false);
  });

  it("A/B screenshot/URL'de network_device_test uyumsuzdur", () => {
    expect(
      isModuleCompatibleWithSources(networkModule, {
        test_type: "ab_comparison",
        current_source_type: "screenshot",
        new_source_type: "url",
      }),
    ).toBe(false);
  });

  it("A/B screenshot/screenshot'ta network_device_test uyumsuzdur", () => {
    expect(
      isModuleCompatibleWithSources(networkModule, {
        test_type: "ab_comparison",
        current_source_type: "screenshot",
        new_source_type: "screenshot",
      }),
    ).toBe(false);
  });

  it("screenshot kaynaginda campaign_cta_test uyumludur", () => {
    expect(isModuleCompatibleWithSources(ctaModule, { current_source_type: "screenshot" })).toBe(true);
  });
});

describe("incompatibleSelectedModuleKeys", () => {
  const catalog = [
    module({ key: "network_device_test", supported_source_types: ["url"] }),
    module({ key: "campaign_cta_test", supported_source_types: ["url", "screenshot", "ai_generated"] }),
  ];

  it("screenshot kaynaginda secili network_device_test'i uyumsuz olarak isaretler", () => {
    const keys = incompatibleSelectedModuleKeys(
      { current_source_type: "screenshot", modules: ["network_device_test", "campaign_cta_test"] },
      catalog,
    );
    expect(keys).toEqual(["network_device_test"]);
  });

  it("url kaynaginda hicbir modulu uyumsuz olarak isaretlemez", () => {
    const keys = incompatibleSelectedModuleKeys(
      { current_source_type: "url", modules: ["network_device_test", "campaign_cta_test"] },
      catalog,
    );
    expect(keys).toEqual([]);
  });
});
