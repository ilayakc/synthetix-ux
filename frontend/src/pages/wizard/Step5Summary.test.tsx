import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { QuoteResponse, WizardDraftPayload } from "../../api/client";
import Step5Summary from "./Step5Summary";

const basePayload: WizardDraftPayload = {
  test_type: "existing_site_basic_ux",
  name: "AI raporu testi",
  target_task: "Sepete ürün ekle",
  current_url: "https://example.com",
  persona_count: 500,
  target_audience: "Genel kullanıcılar",
};

function makeQuote(overrides: Partial<QuoteResponse> = {}): QuoteResponse {
  return {
    pricing_version: "2026.3",
    test_type: "basic_ux_test",
    persona_count: 500,
    modules: [],
    free_entitlement_feature_key: "basic_ux_test",
    free_entitlement_applicable: false,
    line_items: [
      { key: "basic_ux_test", label: "Temel UX testi (500 persona)", quantity: 500, unit_chip_cost: 1, chip_cost: 500, covered_by_free_entitlement: false },
      { key: "ai_report", label: "AI raporu (launch grubu başına)", quantity: 1, unit_chip_cost: 50, chip_cost: 50, covered_by_free_entitlement: false },
    ],
    required_chips: 500,
    total_chips: 550,
    ...overrides,
  };
}

function renderStep5(quote: QuoteResponse, chipBalance: number | null = 10_000) {
  const props: ComponentProps<typeof Step5Summary> = {
    payload: basePayload,
    fieldErrors: {},
    onChange: vi.fn(),
    quote,
    quoteLoading: false,
    quoteError: null,
    chipBalance,
    moduleCatalog: [],
  };
  render(<Step5Summary {...props} />);
}

describe("Step5Summary — fiyat toplamı", () => {
  it("satırlar 500 + 50 ise Toplam 550 Chip gösterir (required_chips=500 değil)", () => {
    renderStep5(makeQuote());

    const total = screen.getByText("Toplam").closest(".wizard-quote-total");
    expect(total).not.toBeNull();
    expect(within(total as HTMLElement).getByText("550 Chip")).toBeInTheDocument();
  });

  it("ücretsiz baseline + 50 AI ise Toplam 50 Chip gösterir ('Ücretsiz hak' değil)", () => {
    renderStep5(
      makeQuote({
        free_entitlement_applicable: true,
        required_chips: 0,
        total_chips: 50,
        line_items: [
          { key: "basic_ux_test", label: "Temel UX testi - ücretsiz hak", quantity: 1000, unit_chip_cost: 0, chip_cost: 0, covered_by_free_entitlement: true },
          { key: "ai_report", label: "AI raporu (launch grubu başına)", quantity: 1, unit_chip_cost: 50, chip_cost: 50, covered_by_free_entitlement: false },
        ],
      }),
    );

    const total = screen.getByText("Toplam").closest(".wizard-quote-total");
    expect(within(total as HTMLElement).getByText("50 Chip")).toBeInTheDocument();
    expect(within(total as HTMLElement).queryByText("Ücretsiz hak")).not.toBeInTheDocument();
  });

  it("tamamen ücretsiz (total 0) ise Toplam 'Ücretsiz hak' gösterir", () => {
    renderStep5(
      makeQuote({
        free_entitlement_applicable: true,
        required_chips: 0,
        total_chips: 0,
        line_items: [
          { key: "basic_ux_test", label: "Temel UX testi - ücretsiz hak", quantity: 500, unit_chip_cost: 0, chip_cost: 0, covered_by_free_entitlement: true },
        ],
      }),
    );

    const total = screen.getByText("Toplam").closest(".wizard-quote-total");
    expect(within(total as HTMLElement).getByText("Ücretsiz hak")).toBeInTheDocument();
  });

  it("bakiye total_chips'in altındaysa yetersiz bakiye uyarısı gösterir", () => {
    renderStep5(makeQuote({ total_chips: 550, required_chips: 500 }), 500);

    expect(
      screen.getByText(/Chip bakiyeniz bu testi başlatmak için yeterli değil/),
    ).toBeInTheDocument();
  });
});
