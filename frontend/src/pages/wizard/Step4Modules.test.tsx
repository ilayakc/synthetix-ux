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
    supported_source_types: ["url"],
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
    supported_source_types: ["url", "screenshot", "ai_generated"],
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

  it("1. adimdaki ana test ile 4. adimdaki ek modullerin farkini aciklar", () => {
    renderStep4();

    expect(screen.getByText("Ek analiz modülleri (isteğe bağlı)")).toBeInTheDocument();
    expect(
      screen.getByText(/1\. adımda seçtiğiniz ana test burada tekrarlanmaz/),
    ).toBeInTheDocument();
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

  // --- Kaynak-modul uyumluluk (network_device_test yalnizca URL) ---------

  it("URL kaynaginda Ag ve cihaz testi secilebilir (disabled degil)", () => {
    renderStep4({ payload: { current_source_type: "url", modules: [] } });

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes[0]).not.toBeDisabled();
  });

  it("screenshot kaynaginda Ag ve cihaz testi disabled olur", () => {
    renderStep4({ payload: { current_source_type: "screenshot", modules: [] } });

    const checkboxes = screen.getAllByRole("checkbox");
    expect(checkboxes[0]).toBeDisabled();
  });

  it("disabled karti icin erisilebilir bir aciklama gosterir", () => {
    renderStep4({ payload: { current_source_type: "screenshot", modules: [] } });

    expect(
      screen.getByText(
        "Bu modül gerçek sayfa yükleme ve ağ ölçümü yaptığı için yalnızca canlı URL kaynaklarıyla kullanılabilir.",
      ),
    ).toBeInTheDocument();
  });

  it("A/B URL/URL'de Ag ve cihaz testi secilebilir", () => {
    renderStep4({
      payload: {
        test_type: "ab_comparison",
        current_source_type: "url",
        new_source_type: "url",
        modules: [],
      },
    });

    expect(screen.getAllByRole("checkbox")[0]).not.toBeDisabled();
  });

  it("A/B URL/screenshot'ta Ag ve cihaz testi disabled olur", () => {
    renderStep4({
      payload: {
        test_type: "ab_comparison",
        current_source_type: "url",
        new_source_type: "screenshot",
        modules: [],
      },
    });

    expect(screen.getAllByRole("checkbox")[0]).toBeDisabled();
  });

  it("A/B screenshot/URL'de Ag ve cihaz testi disabled olur", () => {
    renderStep4({
      payload: {
        test_type: "ab_comparison",
        current_source_type: "screenshot",
        new_source_type: "url",
        modules: [],
      },
    });

    expect(screen.getAllByRole("checkbox")[0]).toBeDisabled();
  });

  it("A/B screenshot/screenshot'ta Ag ve cihaz testi disabled olur", () => {
    renderStep4({
      payload: {
        test_type: "ab_comparison",
        current_source_type: "screenshot",
        new_source_type: "screenshot",
        modules: [],
      },
    });

    expect(screen.getAllByRole("checkbox")[0]).toBeDisabled();
  });

  it("disabled bir kartin checkbox'ina tiklamak payload'i degistirmez", () => {
    const { onChange } = renderStep4({
      payload: { current_source_type: "screenshot", modules: [] },
    });

    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    expect(onChange).not.toHaveBeenCalled();
  });

  // --- ai_report persona zorunlulugu (bkz. aiReportPersona) ----------------

  it("ai_report secili ama persona secimi yoksa aciklayici uyari gosterir", () => {
    renderStep4({ payload: { modules: ["ai_report"] } });

    expect(
      screen.getByText(/AI raporu modülü temsili personalar üzerinde çalışır/),
    ).toBeInTheDocument();
  });

  it("ai_report + bos ozel dagilim ({}) da uyari gosterir", () => {
    renderStep4({ payload: { modules: ["ai_report"], persona_distribution: {} } });

    expect(
      screen.getByText(/AI raporu modülü temsili personalar üzerinde çalışır/),
    ).toBeInTheDocument();
  });

  it("ai_report + persona preset secili ise uyari GOSTERMEZ", () => {
    renderStep4({
      payload: { modules: ["ai_report"], persona_preset_id: "builtin:general_web_users" },
    });

    expect(
      screen.queryByText(/AI raporu modülü temsili personalar üzerinde çalışır/),
    ).not.toBeInTheDocument();
  });

  it("ai_report secili degilse (temel akis) uyari GOSTERMEZ", () => {
    renderStep4({ payload: { modules: ["network_device_test"] } });

    expect(
      screen.queryByText(/AI raporu modülü temsili personalar üzerinde çalışır/),
    ).not.toBeInTheDocument();
  });

  // --- Fiyat toplami: total_chips (baseline + ai_report) gosterilir ---------

  it("satirlar 500 + 50 ise Toplam/Gerekli Chip 550 gosterir (required_chips=500 DEGIL)", () => {
    renderStep4({
      quote: {
        ...baseQuote,
        free_entitlement_applicable: false,
        persona_count: 500,
        required_chips: 500, // baseline (ai_report HARIC)
        total_chips: 550, // baseline + ai_report 50
      },
      chipBalance: 10_000,
    });

    // "Toplam Chip" satiri + "Gerekli Chip" toplami = iki kez 550.
    expect(screen.getAllByText("550")).toHaveLength(2);
  });

  it("ucretsiz baseline + 50 AI ise Toplam/Gerekli Chip 50 gosterir", () => {
    renderStep4({
      quote: {
        ...baseQuote,
        free_entitlement_applicable: true,
        persona_count: 1000,
        required_chips: 0, // baseline ucretsiz
        total_chips: 50, // yalnizca ai_report
      },
      chipBalance: 10_000,
    });

    expect(screen.getAllByText("50")).toHaveLength(2);
    // Ucretsiz hak bilgisi ("Evet") yine dogru gosterilir.
    expect(screen.getByText("Evet")).toBeInTheDocument();
  });

  it("bakiye kontrolu total_chips'e gore yapilir (ucretsiz baseline + 50 AI, bakiye 10 -> yetersiz)", () => {
    renderStep4({
      quote: {
        ...baseQuote,
        free_entitlement_applicable: true,
        required_chips: 0,
        total_chips: 50,
      },
      chipBalance: 10,
    });

    expect(
      screen.getByText(/Chip bakiyeniz bu testi başlatmak için yeterli değil/),
    ).toBeInTheDocument();
  });
});
