import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

const baseMe = {
  user_id: "user-1",
  email: "kullanici@example.com",
  display_name: "Test Kullanıcı",
  language: "tr",
  timezone: "Europe/Istanbul",
  theme: "system",
  compact_view: false,
  notify_simulation_completed: true,
  notify_simulation_failed: true,
  notify_report_ready: true,
  notify_low_chip_balance: true,
  low_chip_balance_threshold: null,
  updated_at: "2026-07-19T00:00:00Z",
};

function baseOrg(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    organization_id: "org-1",
    name: "Test Şirketi",
    slug: "test-sirketi",
    role: "owner",
    created_at: "2026-01-01T00:00:00Z",
    currency: "TRY",
    default_persona_count: 500,
    default_persona_preset_id: null,
    default_device_profile: null,
    default_modules: [],
    default_target_audience: null,
    effective_default_persona_preset_id: null,
    effective_default_device_profile: null,
    effective_default_modules: [],
    warnings: [],
    can_edit_company: true,
    can_edit_defaults: true,
    ...overrides,
  };
}

const presets = [
  {
    id: "preset-1",
    is_builtin: true,
    organization_id: null,
    name: "Genel B2C",
    description: null,
    distribution: {},
    status: "active",
    source_builtin_key: "general_b2c",
    created_at: null,
    updated_at: null,
    archived_at: null,
  },
];

const moduleCatalog = {
  catalog_version: "2026.1",
  modules: [
    {
      key: "network_device_test",
      name: "Ağ ve cihaz testi",
      description: "",
      outputs: [],
      measurement_type: "technical_measurement",
      chip_cost: 40,
      free_entitlement_feature_key: null,
      estimated_duration_minutes: 6,
      selectable_in_wizard: true,
    },
    {
      key: "basic_ux_test",
      name: "Temel UX testi",
      description: "",
      outputs: [],
      measurement_type: "synthetic_estimate",
      chip_cost: 0,
      free_entitlement_feature_key: "basic_ux_test",
      estimated_duration_minutes: 5,
      selectable_in_wizard: false,
    },
  ],
};

function stubFetch(orgOverrides: Partial<Record<string, unknown>> = {}) {
  const org = baseOrg(orgOverrides);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET").toUpperCase();
      if (url.includes("/api/settings/me")) {
        if (method === "PATCH") {
          const body = JSON.parse(String(init?.body ?? "{}"));
          return jsonResponse(200, { ...baseMe, ...body });
        }
        return jsonResponse(200, baseMe);
      }
      if (url.includes("/api/settings/organization")) {
        if (method === "PATCH") {
          const body = JSON.parse(String(init?.body ?? "{}"));
          return jsonResponse(200, { ...org, ...body });
        }
        return jsonResponse(200, org);
      }
      if (url.includes("/api/personas/presets")) return jsonResponse(200, presets);
      if (url.includes("/api/analysis-modules/catalog")) return jsonResponse(200, moduleCatalog);
      throw new Error(`Beklenmeyen istek: ${url}`);
    }),
  );
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <Settings />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

describe("Settings", () => {
  it("yükleniyor durumunu gösterir", () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
    renderPage();
    expect(screen.getByText("Yükleniyor…")).toBeInTheDocument();
  });

  it("yükleme hatasında anlaşılır bir hata mesajı gösterir", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network")));
    renderPage();
    expect(await screen.findByText("Ayarlar yüklenemedi.")).toBeInTheDocument();
  });

  it("dört sekmeyi gösterir ve varsayılan olarak Profilim sekmesi aktiftir", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Profilim" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Şirket" })).toHaveAttribute("aria-selected", "false");
  });

  it("ok tuşlarıyla sekmeler arasında klavye ile gezinilebilir", async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    const profileTab = screen.getByRole("tab", { name: "Profilim" });
    profileTab.focus();
    await user.keyboard("{ArrowRight}");

    expect(screen.getByRole("tab", { name: "Şirket" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Şirket" })).toHaveFocus();
  });

  it("görünen ad değiştirildiğinde kaydedilmemiş değişiklik göstergesi belirir ve kaydedince kaybolur", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByLabelText("Görünen ad")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Görünen ad"), { target: { value: "Yeni Ad" } });

    expect(screen.getByText("Kaydedilmemiş değişiklikler var")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));

    await waitFor(() => expect(screen.getByText("Ayarlar kaydedildi.")).toBeInTheDocument());
    expect(screen.queryByText("Kaydedilmemiş değişiklikler var")).not.toBeInTheDocument();
  });

  it("kaydetme sırasında Kaydet düğmesi devre dışı kalır", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByLabelText("Görünen ad")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Görünen ad"), { target: { value: "Yeni Ad" } });

    const saveButton = screen.getByRole("button", { name: "Kaydet" });
    fireEvent.click(saveButton);

    expect(screen.getByRole("button", { name: "Kaydediliyor…" })).toBeDisabled();

    await waitFor(() => expect(screen.getByRole("button", { name: "Kaydet" })).toBeInTheDocument());
  });

  it("değişiklikleri geri al eski değerlere döndürür", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByLabelText("Görünen ad")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Görünen ad"), { target: { value: "Yeni Ad" } });
    fireEvent.click(screen.getByRole("button", { name: "Değişiklikleri geri al" }));

    expect(screen.getByLabelText("Görünen ad")).toHaveValue("Test Kullanıcı");
    expect(screen.queryByText("Kaydedilmemiş değişiklikler var")).not.toBeInTheDocument();
  });

  it("API hatasında sessizce başarısız olmaz, Türkçe hata mesajı gösterir", async () => {
    const org = baseOrg();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        const method = (init?.method ?? "GET").toUpperCase();
        if (url.includes("/api/settings/me") && method === "PATCH") {
          return Promise.resolve({
            ok: false,
            status: 400,
            json: async () => ({ detail: "Gecersiz dil" }),
          });
        }
        if (url.includes("/api/settings/me")) return jsonResponse(200, baseMe);
        if (url.includes("/api/settings/organization")) return jsonResponse(200, org);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, presets);
        if (url.includes("/api/analysis-modules/catalog")) return jsonResponse(200, moduleCatalog);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );
    renderPage();

    await waitFor(() => expect(screen.getByLabelText("Görünen ad")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Görünen ad"), { target: { value: "Yeni Ad" } });
    fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));

    expect(await screen.findByText("Gecersiz dil")).toBeInTheDocument();
  });

  it("owner olmayan bir kullanıcı için Şirket alanları salt okunur gösterilir ve neden açıklanır", async () => {
    stubFetch({ can_edit_company: false, role: "analyst" });
    renderPage();

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Şirket" }));

    expect(screen.getByLabelText("Şirket adı")).toBeDisabled();
    expect(
      screen.getAllByText("Bu alanı yalnızca şirket sahibi veya yöneticisi düzenleyebilir.").length,
    ).toBeGreaterThan(0);
  });

  it("tema seçildiğinde documentElement üzerinde data-theme anında güncellenir", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: "Görünüm ve Bildirimler" }));

    fireEvent.click(screen.getByLabelText("Koyu tema"));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});
