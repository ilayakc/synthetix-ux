import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import KullanimVeChip from "./KullanimVeChip";

function renderPage() {
  return render(<KullanimVeChip />);
}

const usageSummaryResponse = {
  organization_id: "00000000-0000-0000-0000-000000000000",
  chip_balance: 0,
  entitlements: [],
  pricing_version: "2026.1",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KullanimVeChip", () => {
  it("bakiyeyi ve Chip paketlerini doğrudan aynı ekranda gösterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/billing/usage-summary")) {
          return Promise.resolve({ ok: true, json: async () => usageSummaryResponse });
        }
        if (url.includes("/api/billing/chip-packages")) {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              package_version: "2026.2",
              packages: [
                { key: "mini", name: "Mini paket", chip_amount: 50, price_try: 119 },
              ],
            }),
          });
        }
        if (url.includes("/api/billing/topup-requests")) {
          return Promise.resolve({ ok: true, json: async () => [] });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("0")).toBeInTheDocument());

    expect(screen.getByRole("heading", { name: "Chip Cüzdanı" })).toBeInTheDocument();
    expect(screen.getByText("Mevcut Chip Bakiyesi")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Chip Paketleri" })).toBeInTheDocument();
    expect(screen.getByText(/Mini paket/)).toBeInTheDocument();
    expect(screen.getByText("119 TL")).toBeInTheDocument();
    expect(screen.queryByText("Ücretsiz Temel UX Testi")).not.toBeInTheDocument();
    expect(screen.queryByText("Ücretsiz Erişilebilirlik Ön Kontrolü")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Chip Yükle" })).not.toBeInTheDocument();
  });

  it("kullanim ozeti alinamadiginda hata mesaji gosterir", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Ağ hatası")));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("Chip yükleme bilgileri yüklenemedi.")).toBeInTheDocument(),
    );
  });

  it("veri gelene kadar yukleniyor durumunu gosterir", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise(() => {})), // hicbir zaman cozulmez
    );

    renderPage();

    expect(screen.getByText("Yükleniyor…")).toBeInTheDocument();
  });
});
