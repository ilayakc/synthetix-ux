import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AnalysisModuleDefinition, QuoteResponse } from "../../api/client";
import Step4Modules from "./Step4Modules";

const selectableModules: AnalysisModuleDefinition[] = [
  {
    key: "network_device_test",
    name: "Ağ ve cihaz testi",
    description: "Ag testi aciklamasi",
    outputs: ["Yukleme sureleri"],
    measurement_type: "technical_measurement",
    chip_cost: 40,
    free_entitlement_feature_key: null,
    estimated_duration_minutes: 6,
    selectable_in_wizard: true,
  },
  {
    key: "campaign_cta_test",
    name: "Kampanya ve CTA testi",
    description: "CTA testi aciklamasi",
    outputs: ["Tiklama olasiligi"],
    measurement_type: "synthetic_estimate",
    chip_cost: 35,
    free_entitlement_feature_key: null,
    estimated_duration_minutes: 5,
    selectable_in_wizard: true,
  },
];

const baseQuote: QuoteResponse = {
  pricing_version: "2026.2",
  test_type: "basic_ux_test",
  persona_count: 500,
  modules: ["network_device_test"],
  free_entitlement_feature_key: "basic_ux_test",
  free_entitlement_applicable: true,
  line_items: [],
  required_chips: 40,
  total_chips: 40,
};

function renderStep4(overrides: Partial<ComponentProps<typeof Step4Modules>> = {}) {
  const onChange = vi.fn();
  const props: ComponentProps<typeof Step4Modules> = {
    payload: { modules: ["network_device_test"] },
    fieldErrors: {},
    onChange,
    quote: baseQuote,
    quoteLoading: false,
    quoteError: null,
    chipBalance: 100,
    moduleCatalog: selectableModules,
    ...overrides,
  };
  render(<Step4Modules {...props} />);
  return { onChange };
}

describe("Step4Modules", () => {
  it("yalnizca sihirbazda secilebilir modulleri kart olarak gosterir", () => {
    renderStep4();

    expect(screen.getByRole("heading", { name: "Ağ ve cihaz testi" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Kampanya ve CTA testi" })).toBeInTheDocument();
  });

  it("bir kartin checkbox'ina tiklanmasi payload.modules'i gunceller", () => {
    const { onChange } = renderStep4();

    const checkboxes = screen.getAllByRole("checkbox");
    // Kart sirasi moduleCatalog sirasiyla ayni: [network_device_test, campaign_cta_test].
    fireEvent.click(checkboxes[1]);

    expect(onChange).toHaveBeenCalledWith("modules", ["network_device_test", "campaign_cta_test"]);
  });

  it("zaten secili bir modulun checkbox'ina tiklanmasi onu kaldirir", () => {
    const { onChange } = renderStep4();

    const checkboxes = screen.getAllByRole("checkbox");
    fireEvent.click(checkboxes[0]);

    expect(onChange).toHaveBeenCalledWith("modules", []);
  });

  it("canli teklif ozetinde persona sayisi, secili moduller, ucretsiz hak, toplam Chip, bakiye ve fiyatlandirma surumunu gosterir", () => {
    renderStep4();

    expect(screen.getByText("Persona sayısı")).toBeInTheDocument();
    expect(screen.getByText("500")).toBeInTheDocument();

    const selectedModulesRow = screen.getByText("Seçili modüller").closest("li");
    expect(selectedModulesRow).not.toBeNull();
    expect(
      within(selectedModulesRow as HTMLElement).getByText("Ağ ve cihaz testi"),
    ).toBeInTheDocument();

    expect(screen.getByText("Kullanılacak ücretsiz hak")).toBeInTheDocument();
    expect(screen.getByText("Evet")).toBeInTheDocument();

    expect(screen.getByText("Toplam Chip")).toBeInTheDocument();
    expect(screen.getAllByText("40").length).toBeGreaterThan(0);

    expect(screen.getByText("Mevcut bakiye")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();

    expect(screen.getByText("Fiyatlandırma sürümü")).toBeInTheDocument();
    expect(screen.getByText("2026.2")).toBeInTheDocument();
  });

  it("bakiye yetersizse uyari gosterir", () => {
    renderStep4({
      quote: { ...baseQuote, free_entitlement_applicable: false, required_chips: 500 },
      chipBalance: 10,
    });

    expect(
      screen.getByText(/Chip bakiyeniz bu testi başlatmak için yeterli değil/),
    ).toBeInTheDocument();
  });

  it("yeterli bakiye varsa uyari gostermez", () => {
    renderStep4({
      quote: { ...baseQuote, free_entitlement_applicable: false, required_chips: 40 },
      chipBalance: 1000,
    });

    expect(
      screen.queryByText(/Chip bakiyeniz bu testi başlatmak için yeterli değil/),
    ).not.toBeInTheDocument();
  });
});
