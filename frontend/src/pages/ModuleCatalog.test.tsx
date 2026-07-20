import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ModuleCatalog from "./ModuleCatalog";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

const catalogResponse = {
  catalog_version: "2026.1",
  modules: [
    {
      key: "basic_ux_test",
      name: "Temel UX testi",
      description: "Temel UX testi aciklamasi",
      outputs: ["Gorev tamamlama olasiligi"],
      measurement_type: "synthetic_estimate",
      chip_cost: 0,
      free_entitlement_feature_key: "basic_ux_test",
      estimated_duration_minutes: 5,
      selectable_in_wizard: false,
    },
    {
      key: "network_device_test",
      name: "Ağ ve cihaz testi",
      description: "Ag ve cihaz testi aciklamasi",
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
  ],
};

function renderCatalog() {
  return render(
    <MemoryRouter initialEntries={["/analiz-modulleri"]}>
      <Routes>
        <Route path="/analiz-modulleri" element={<ModuleCatalog />} />
        <Route path="/tests/new" element={<p>Sihirbaz sayfasi</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ModuleCatalog", () => {
  it("katalogdaki tum modulleri ad, aciklama, olcum turu, Chip bedeli ve sureyle gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, catalogResponse);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderCatalog();

    await waitFor(() => expect(screen.getByText("Temel UX testi")).toBeInTheDocument());
    expect(screen.getByText("Ağ ve cihaz testi")).toBeInTheDocument();
    expect(screen.getByText("Ücretsiz hak uygunluğu var")).toBeInTheDocument();
    expect(screen.getByText("40 Chip")).toBeInTheDocument();
    expect(screen.getByText("Teknik ölçüm")).toBeInTheDocument();
    expect(screen.getAllByText("Sentetik tahmin").length).toBeGreaterThan(0);
    expect(screen.getByText(/Katalog sürümü: 2026.1/)).toBeInTheDocument();
  });

  it("secilemeyen temel moduller icin devre disi checkbox yerine aciklama gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, catalogResponse);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderCatalog();

    await waitFor(() => expect(screen.getByText("Temel UX testi")).toBeInTheDocument());
    expect(screen.getByText("Test türüne göre otomatik eklenir.")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("bir modulde 'Yeni testte kullan' basinca modul Test hazirligi alanina eklenir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, catalogResponse);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderCatalog();

    await waitFor(() => expect(screen.getByText("Ağ ve cihaz testi")).toBeInTheDocument());
    expect(screen.queryByText("Test hazırlığı")).not.toBeInTheDocument();

    const useButtons = screen.getAllByRole("button", { name: "Yeni testte kullan" });
    fireEvent.click(useButtons[0]);

    expect(screen.getByText("Test hazırlığı")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Seçtiklerimle yeni test oluştur" }),
    ).toBeInTheDocument();
  });

  it("birden fazla modul secilebilir ve 'Seçtiklerimle yeni test oluştur' /tests/new adresine yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, catalogResponse);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderCatalog();

    await waitFor(() => expect(screen.getByText("Ağ ve cihaz testi")).toBeInTheDocument());

    // Her tiklama, o modulun dugme etiketini "kaldir" haline getirir; bu yuzden
    // ikinci modulu eklemek icin tekrar kalan "Yeni testte kullan" dugmesini ariyoruz.
    fireEvent.click(screen.getAllByRole("button", { name: "Yeni testte kullan" })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: "Yeni testte kullan" })[0]);

    fireEvent.click(screen.getByRole("button", { name: "Seçtiklerimle yeni test oluştur" }));

    expect(await screen.findByText("Sihirbaz sayfasi")).toBeInTheDocument();
  });

  it("'Yeni testte kullan' dugmesi klavyeyle erisilebilir ve etkinlestirilebilir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, catalogResponse);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    const user = userEvent.setup();
    renderCatalog();

    await waitFor(() => expect(screen.getByText("Ağ ve cihaz testi")).toBeInTheDocument());

    const useButton = screen.getAllByRole("button", { name: "Yeni testte kullan" })[0];
    useButton.focus();
    expect(useButton).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(screen.getByText("Test hazırlığı")).toBeInTheDocument();
  });

  it("yukleme hatasinda hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() =>
        Promise.resolve({
          ok: false,
          status: 500,
          json: async () => ({ detail: "sunucu hatasi" }),
        }),
      ),
    );

    renderCatalog();

    expect(await screen.findByText("sunucu hatasi")).toBeInTheDocument();
  });
});
