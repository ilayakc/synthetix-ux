import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import KullanimVeChip from "./KullanimVeChip";

function renderPage() {
  return render(
    <MemoryRouter>
      <KullanimVeChip />
    </MemoryRouter>,
  );
}

const usageSummaryResponse = {
  organization_id: "00000000-0000-0000-0000-000000000000",
  chip_balance: 0,
  entitlements: [
    { feature_key: "basic_ux_test", status: "available", quantity: 1, reserved_until: null },
    {
      feature_key: "accessibility_precheck",
      status: "available",
      quantity: 1,
      reserved_until: null,
    },
  ],
  pricing_version: "2026.1",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KullanimVeChip", () => {
  it("bakiyeyi ve iki ücretsiz hakkı ayrı kartlarda gösterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => usageSummaryResponse,
      }),
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("0")).toBeInTheDocument());

    expect(screen.getByText("Chip Bakiyesi")).toBeInTheDocument();
    expect(screen.getByText("Ücretsiz Temel UX Testi")).toBeInTheDocument();
    expect(screen.getByText("Ücretsiz Erişilebilirlik Ön Kontrolü")).toBeInTheDocument();
    expect(screen.getAllByText("0/1")).toHaveLength(2);
    expect(screen.getAllByText("Kullanılabilir")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "Chip Yükle" })).toHaveAttribute("href", "/chip-yukle");
    expect(screen.queryByText(/aşağıda listelenir/i)).not.toBeInTheDocument();
  });

  it("kullanilmis (consumed) bir hakki 'Kullanıldı' olarak gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          ...usageSummaryResponse,
          entitlements: [
            { feature_key: "basic_ux_test", status: "consumed", quantity: 1, reserved_until: null },
          ],
        }),
      }),
    );

    renderPage();

    await waitFor(() => expect(screen.getByText("Kullanıldı")).toBeInTheDocument());
    expect(screen.getByText("1/1")).toBeInTheDocument();
  });

  it("kullanim ozeti alinamadiginda hata mesaji gosterir", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Ağ hatası")));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText("Kullanım özeti yüklenemedi.")).toBeInTheDocument(),
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
