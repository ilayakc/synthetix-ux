import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import TestWizard from "./TestWizard";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

const emptyDraft = {
  id: "draft-1",
  organization_id: "org-1",
  status: "draft",
  current_step: 1,
  payload: {},
  missing_fields: [
    "project_id",
    "name",
    "target_task",
    "test_type",
    "current_url",
    "persona_count",
    "target_audience",
    "authorization_confirmed",
  ],
  created_at: "2026-07-16T00:00:00Z",
  updated_at: "2026-07-16T00:00:00Z",
};

const project = {
  id: "project-1",
  organization_id: "org-1",
  name: "Anasayfa Yenileme",
  description: null,
  status: "active",
  test_count: 0,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
  archived_at: null,
};

const moduleCatalogResponse = {
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
      supported_source_types: ["url"],
    },
  ],
};

const personaPreset = {
  id: "preset-1",
  is_builtin: true,
  organization_id: null,
  name: "Genel B2C",
  description: null,
  distribution: {},
  status: "active",
  source_builtin_key: "general_b2c",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  archived_at: null,
};

function renderWizard(initialEntry = "/tests/new") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/tests/new" element={<TestWizard />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TestWizard", () => {
  it("bir taslak olusturur ve ilk adimi gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/tests/drafts") && init?.method === "POST") {
          return jsonResponse(201, emptyDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard();

    await waitFor(() => expect(screen.getByText("1. Test Detayları")).toBeInTheDocument());
    expect(screen.getByLabelText("Test adı")).toBeInTheDocument();
  });

  it("persona sayisi araligin disindaysa bir sonraki adima gecisi engeller", async () => {
    const draftWithStep3 = { ...emptyDraft, current_step: 3, payload: {} };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftWithStep3);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftWithStep3);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());

    const personaInput = screen.getByLabelText(/Persona sayısı/);
    fireEvent.change(personaInput, { target: { value: "50" } });
    fireEvent.click(screen.getByText("İleri"));

    expect(
      await screen.findByText("Persona sayısı 100 ile 50.000 arasında olmalıdır."),
    ).toBeInTheDocument();
    // Hala 3. adimda kalinmis olmali (4. adima gecilmemis).
    expect(screen.getByText("3. Persona")).toBeInTheDocument();
  });

  it("var olan bir taslagi (sayfa yenileme sonrasi) dolu alanlarla devam ettirir", async () => {
    const resumedDraft = {
      ...emptyDraft,
      current_step: 3,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 750,
        target_audience: "Yeni musteriler",
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, resumedDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, resumedDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 750,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());
    expect(screen.getByLabelText(/Persona sayısı/)).toHaveValue(750);
    expect(screen.getByLabelText("Hedef kitle")).toHaveValue("Yeni musteriler");
  });

  it("4. adimda ai_report secili ama persona secimi yoksa Ileri kullaniciyi persona adimina yonlendirir", async () => {
    const draftAtStep4 = {
      ...emptyDraft,
      current_step: 4,
      payload: {
        project_id: project.id,
        name: "AI raporu testi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 500,
        target_audience: "Genel kullanicilar",
        modules: ["ai_report"],
      },
    };
    const catalogWithAiReport = {
      catalog_version: "2026.3",
      modules: [
        ...moduleCatalogResponse.modules,
        {
          key: "ai_report",
          name: "AI raporu",
          description: "AI destekli nihai rapor",
          outputs: ["AI raporu"],
          measurement_type: "synthetic_estimate",
          chip_cost: 50,
          free_entitlement_feature_key: null,
          estimated_duration_minutes: 10,
          selectable_in_wizard: true,
          supported_source_types: ["url", "screenshot", "ai_generated"],
        },
      ],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep4);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep4);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, catalogWithAiReport);
        }
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 100,
            entitlements: [],
            pricing_version: "2026.3",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.3",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: ["ai_report"],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 50,
            total_chips: 50,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    // 4. adim yuklendi.
    await waitFor(() =>
      expect(screen.getByText("Analiz modülleri (isteğe bağlı)")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByText("İleri"));

    // Persona adimina (3) yonlendirildi: persona alani gorunur.
    await waitFor(() => expect(screen.getByLabelText(/Persona sayısı/)).toBeInTheDocument());
    // Neden banner'da (role="alert") aciklanir.
    expect(
      screen.getByText(/AI raporu modülü temsili personalar üzerinde çalışır/),
    ).toBeInTheDocument();
  });

  it("yetki onayi verilmeden baslatma dugmesi devre disi kalir", async () => {
    const readyForLaunchDraft = {
      ...emptyDraft,
      current_step: 5,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
      },
      missing_fields: ["authorization_confirmed"],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, readyForLaunchDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, readyForLaunchDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Ücretsiz hakkı kullan ve başlat");
    expect(launchButton.closest("button")).toBeDisabled();

    fireEvent.click(screen.getByLabelText(/Bu URL'leri analiz etme yetkisine sahip/));

    await waitFor(() => expect(launchButton.closest("button")).not.toBeDisabled());
  });

  function launchReadyDraft(overrides: Record<string, unknown> = {}) {
    return {
      ...emptyDraft,
      current_step: 5,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
        authorization_confirmed: true,
      },
      missing_fields: [],
      ...overrides,
    };
  }

  function stubLaunchFetch(options: {
    draft: unknown;
    quote: Record<string, unknown>;
    launchResponse?: { status: number; body: unknown };
  }) {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/launch") && init?.method === "POST") {
          const { status, body } = options.launchResponse ?? {
            status: 200,
            body: {
              draft_id: "draft-1",
              status: "launched",
              test_definition_id: "test-def-1",
              simulation_run_ids: ["run-1"],
              used_free_entitlement: true,
              reserved_chips: 0,
              engine_status_message: "Simülasyon kuyruğa alındı.",
              warnings: [],
            },
          };
          return jsonResponse(status, body);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, options.draft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, options.draft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 1000,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, options.quote);
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );
  }

  it("basarili baslatma (ucretsiz hak) sonrasi 'Test kuyruğa alındı' ekranini gosterir", async () => {
    stubLaunchFetch({
      draft: launchReadyDraft(),
      quote: {
        pricing_version: "2026.1",
        test_type: "basic_ux_test",
        persona_count: 500,
        modules: [],
        free_entitlement_feature_key: "basic_ux_test",
        free_entitlement_applicable: true,
        line_items: [],
        required_chips: 0,
        total_chips: 0,
      },
      launchResponse: {
        status: 200,
        body: {
          draft_id: "draft-1",
          status: "launched",
          test_definition_id: "test-def-1",
          simulation_run_ids: ["run-1"],
          used_free_entitlement: true,
          reserved_chips: 0,
          engine_status_message: "Simülasyon kuyruğa alındı.",
          warnings: [],
        },
      },
    });

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Ücretsiz hakkı kullan ve başlat");
    fireEvent.click(launchButton);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Test kuyruğa alındı" })).toBeInTheDocument(),
    );
    expect(
      screen.getByText(
        "Testiniz başlatıldı. Analiz durumunu Simülasyonlar sayfasından takip edebilirsiniz. Rapor, tüm çalıştırmalar başarıyla tamamlandıktan sonra oluşturulur.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Test, ücretsiz hakkınız kullanılarak başlatıldı."),
    ).toBeInTheDocument();
    expect(screen.getByText("Simülasyon kuyruğa alındı.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Simülasyonları görüntüle" })).toBeInTheDocument();
    expect(
      screen.queryByText(/Analiz başarıyla tamamlandı|Rapor hazır|Test tamamlandı/),
    ).not.toBeInTheDocument();
  });

  it("stale CTA annotation uyarisi varsa dogru tarafa (slot) bagli acik bir mesaj gosterir", async () => {
    stubLaunchFetch({
      draft: launchReadyDraft(),
      quote: {
        pricing_version: "2026.1",
        test_type: "basic_ux_test",
        persona_count: 500,
        modules: [],
        free_entitlement_feature_key: "basic_ux_test",
        free_entitlement_applicable: true,
        line_items: [],
        required_chips: 0,
        total_chips: 0,
      },
      launchResponse: {
        status: 200,
        body: {
          draft_id: "draft-1",
          status: "launched",
          test_definition_id: "test-def-1",
          simulation_run_ids: ["run-1"],
          used_free_entitlement: true,
          reserved_chips: 0,
          engine_status_message: "Simülasyon kuyruğa alındı.",
          warnings: [
            {
              code: "stale_cta_annotation_cleared",
              slot: "current",
              message: "sunucu mesaji (kullanilmiyor, frontend kendi metnini gosterir)",
            },
          ],
        },
      },
    });

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Ücretsiz hakkı kullan ve başlat");
    fireEvent.click(launchButton);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Test kuyruğa alındı" })).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/Tasarım değiştiği için önceki CTA seçiminiz temizlendi \(Tasarım A\)/),
    ).toBeInTheDocument();
  });

  it("basarili baslatma (Chip ile) sonrasi Chip mesajini gosterir", async () => {
    stubLaunchFetch({
      draft: launchReadyDraft(),
      quote: {
        pricing_version: "2026.1",
        test_type: "basic_ux_test",
        persona_count: 500,
        modules: [],
        free_entitlement_feature_key: null,
        free_entitlement_applicable: false,
        line_items: [],
        required_chips: 50,
        total_chips: 50,
      },
      launchResponse: {
        status: 200,
        body: {
          draft_id: "draft-1",
          status: "launched",
          test_definition_id: "test-def-1",
          simulation_run_ids: ["run-1"],
          used_free_entitlement: false,
          reserved_chips: 50,
          engine_status_message: "Simülasyon kuyruğa alındı.",
          warnings: [],
        },
      },
    });

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Chip ile başlat");
    fireEvent.click(launchButton);

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Test kuyruğa alındı" })).toBeInTheDocument(),
    );
    expect(screen.getByText("Test, Chip bakiyenizden düşülerek başlatıldı.")).toBeInTheDocument();
  });

  it("402 (yetersiz Chip) durumunda banner gosterir ve baslatilmis ekrani gostermez", async () => {
    stubLaunchFetch({
      draft: launchReadyDraft(),
      quote: {
        pricing_version: "2026.1",
        test_type: "basic_ux_test",
        persona_count: 500,
        modules: [],
        free_entitlement_feature_key: null,
        free_entitlement_applicable: false,
        line_items: [],
        required_chips: 5000,
        total_chips: 5000,
      },
      launchResponse: {
        status: 402,
        body: {
          detail:
            "Chip bakiyeniz bu testi başlatmak için yeterli değil; sahte/başlatılmamış bir işlem oluşturulmadı.",
        },
      },
    });

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Chip ile başlat");
    fireEvent.click(launchButton);

    await waitFor(() =>
      expect(
        screen.getByText(
          "Chip bakiyeniz bu testi başlatmak için yeterli değil; sahte/başlatılmamış bir işlem oluşturulmadı.",
        ),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByRole("heading", { name: "Test kuyruğa alındı" })).not.toBeInTheDocument();
  });

  it("404 gibi 400/402 disindaki bir ApiError'da da gercek backend mesaji gosterilir (jenerik mesaja indirgenmez)", async () => {
    stubLaunchFetch({
      draft: launchReadyDraft(),
      quote: {
        pricing_version: "2026.1",
        test_type: "basic_ux_test",
        persona_count: 500,
        modules: [],
        free_entitlement_feature_key: "basic_ux_test",
        free_entitlement_applicable: true,
        line_items: [],
        required_chips: 0,
        total_chips: 0,
      },
      launchResponse: {
        status: 404,
        body: { detail: "Taslak bulunamadi" },
      },
    });

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Ücretsiz hakkı kullan ve başlat");
    fireEvent.click(launchButton);

    await waitFor(() => expect(screen.getByText("Taslak bulunamadı")).toBeInTheDocument());
    expect(
      screen.queryByText("Test başlatılamadı. Lütfen tekrar deneyin."),
    ).not.toBeInTheDocument();
  });

  it("govdesiz/beklenmeyen 500 hatasinda guvenli jenerik bir API mesaji gosterilir (stack trace/SQL degil)", async () => {
    stubLaunchFetch({
      draft: launchReadyDraft(),
      quote: {
        pricing_version: "2026.1",
        test_type: "basic_ux_test",
        persona_count: 500,
        modules: [],
        free_entitlement_feature_key: "basic_ux_test",
        free_entitlement_applicable: true,
        line_items: [],
        required_chips: 0,
        total_chips: 0,
      },
      launchResponse: {
        status: 500,
        body: null,
      },
    });

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByText("Ücretsiz hakkı kullan ve başlat");
    fireEvent.click(launchButton);

    await waitFor(() => expect(screen.getByText(/API isteği başarısız: 500/)).toBeInTheDocument());
  });

  it("persona sayisi tam 100 oldugunda bir sonraki adima gecisi saglar (alt sinir)", async () => {
    const draftAtStep3 = { ...emptyDraft, current_step: 3, payload: {} };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/Persona sayısı/), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("Hedef kitle"), {
      target: { value: "Herhangi bir hedef kitle" },
    });
    fireEvent.click(screen.getByText("İleri"));

    await waitFor(() => expect(screen.getByText("4. Analiz Modülleri")).toBeInTheDocument());
  });

  it("persona sayisi tam 50.000 oldugunda bir sonraki adima gecisi saglar (ust sinir)", async () => {
    const draftAtStep3 = { ...emptyDraft, current_step: 3, payload: {} };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/Persona sayısı/), { target: { value: "50000" } });
    fireEvent.change(screen.getByLabelText("Hedef kitle"), {
      target: { value: "Herhangi bir hedef kitle" },
    });
    fireEvent.click(screen.getByText("İleri"));

    await waitFor(() => expect(screen.getByText("4. Analiz Modülleri")).toBeInTheDocument());
  });

  it("persona sayisi 99 oldugunda bir sonraki adima gecisi engeller", async () => {
    const draftAtStep3 = { ...emptyDraft, current_step: 3, payload: {} };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/Persona sayısı/), { target: { value: "99" } });
    fireEvent.click(screen.getByText("İleri"));

    expect(
      await screen.findByText("Persona sayısı 100 ile 50.000 arasında olmalıdır."),
    ).toBeInTheDocument();
    expect(screen.getByText("3. Persona")).toBeInTheDocument();
  });

  it("persona sayisi 50.001 oldugunda bir sonraki adima gecisi engeller", async () => {
    const draftAtStep3 = { ...emptyDraft, current_step: 3, payload: {} };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText(/Persona sayısı/), { target: { value: "50001" } });
    fireEvent.click(screen.getByText("İleri"));

    expect(
      await screen.findByText("Persona sayısı 100 ile 50.000 arasında olmalıdır."),
    ).toBeInTheDocument();
    expect(screen.getByText("3. Persona")).toBeInTheDocument();
  });

  it("A/B karsilastirmasinda yeni tasarim URL'si girilmeden bir sonraki adima gecisi engeller", async () => {
    const draftAtStep2 = {
      ...emptyDraft,
      current_step: 2,
      payload: { test_type: "ab_comparison", current_url: "https://example.com" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep2);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep2);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());

    fireEvent.click(screen.getByText("İleri"));

    expect(await screen.findByText("Yeni tasarım URL'si gereklidir.")).toBeInTheDocument();
    expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument();
  });

  it("katalogdan gelen gecerli modul secimini yeni draft'a kaydeder ve 'Seçim kaydedildi' gosterir", async () => {
    const patchCalls: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/tests/drafts") && init?.method === "POST") {
          return jsonResponse(201, emptyDraft);
        }
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          patchCalls.push(JSON.parse(String(init.body)));
          return jsonResponse(200, {
            ...emptyDraft,
            payload: { modules: ["network_device_test"] },
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          // Draft olusturulup URL'e yazildiktan sonra effect [draftId] degisikligiyle
          // yeniden calisir ve taslagi tekrar okur; sunucuda kalici olan degeri doner.
          return jsonResponse(200, {
            ...emptyDraft,
            payload: { modules: ["network_device_test"] },
          });
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?modules=network_device_test,unknown_module");

    await waitFor(() => expect(screen.getByText("1. Test Detayları")).toBeInTheDocument());
    expect(await screen.findByText("Seçim kaydedildi.")).toBeInTheDocument();

    expect(patchCalls).toHaveLength(1);
    expect(patchCalls[0]).toMatchObject({ payload: { modules: ["network_device_test"] } });
  });

  it("persona presetiyle yeni test baslatilirken gecerli preset draft'a kaydedilir", async () => {
    const patchCalls: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/tests/drafts") && init?.method === "POST") {
          return jsonResponse(201, emptyDraft);
        }
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          patchCalls.push(JSON.parse(String(init.body)));
          return jsonResponse(200, {
            ...emptyDraft,
            payload: { persona_preset_id: personaPreset.id },
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, {
            ...emptyDraft,
            payload: { persona_preset_id: personaPreset.id },
          });
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, [personaPreset]);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard(`/tests/new?persona_preset=${personaPreset.id}`);

    await waitFor(() => expect(screen.getByText("1. Test Detayları")).toBeInTheDocument());
    expect(await screen.findByText("Seçim kaydedildi.")).toBeInTheDocument();

    expect(patchCalls).toHaveLength(1);
    expect(patchCalls[0]).toMatchObject({ payload: { persona_preset_id: personaPreset.id } });
  });

  it("gecersiz modul anahtari ve persona preset kimligi reddedilir, draft'a kaydedilmez", async () => {
    const patchCalls: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/tests/drafts") && init?.method === "POST") {
          return jsonResponse(201, emptyDraft);
        }
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          patchCalls.push(JSON.parse(String(init.body)));
          return jsonResponse(200, emptyDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, emptyDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, [personaPreset]);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    // "basic_ux_test" katalogda var ama secilebilir degil; "does_not_exist" hic yok;
    // "does-not-exist" da preset listesinde bulunmayan bir kimlik.
    renderWizard("/tests/new?modules=does_not_exist,basic_ux_test&persona_preset=does-not-exist");

    await waitFor(() => expect(screen.getByText("1. Test Detayları")).toBeInTheDocument());

    expect(patchCalls).toHaveLength(0);
    expect(screen.queryByText("Seçim kaydedildi.")).not.toBeInTheDocument();
  });

  it("draft'ta kayitli persona preseti 3. adimda onceden secili gosterir", async () => {
    const draftWithPreset = {
      ...emptyDraft,
      current_step: 3,
      payload: { persona_preset_id: personaPreset.id },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftWithPreset);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftWithPreset);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, [personaPreset]);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());
    expect(screen.getByLabelText("Preset")).toHaveValue(personaPreset.id);
  });

  it("draft'ta kayitli modul secimi 4. adimda onceden secili gosterir", async () => {
    const draftWithModules = {
      ...emptyDraft,
      current_step: 4,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: ["network_device_test"],
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftWithModules);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftWithModules);
        }
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.2",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: ["network_device_test"],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 40,
            total_chips: 40,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("4. Analiz Modülleri")).toBeInTheDocument());
    const checkboxes = await screen.findAllByRole("checkbox");
    expect(checkboxes).toHaveLength(1);
    expect(checkboxes[0]).toBeChecked();
  });

  it("modul secimi degistiginde fiyat teklifi guncellenir", async () => {
    const draftAtStep4 = {
      ...emptyDraft,
      current_step: 4,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep4);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftAtStep4);
        }
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 1000,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote") && init?.method === "POST") {
          const body = JSON.parse(String(init.body)) as { modules?: string[] };
          const modules = body.modules ?? [];
          const requiredChips = modules.includes("network_device_test") ? 40 : 0;
          return jsonResponse(200, {
            pricing_version: "2026.2",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules,
            free_entitlement_feature_key: modules.length === 0 ? "basic_ux_test" : null,
            free_entitlement_applicable: modules.length === 0,
            line_items: [],
            required_chips: requiredChips,
            total_chips: requiredChips,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("4. Analiz Modülleri")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("Toplam Chip")).toBeInTheDocument());
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);

    const checkbox = await screen.findByRole("checkbox");
    fireEvent.click(checkbox);

    await waitFor(() => expect(screen.getAllByText("40").length).toBeGreaterThan(0));
  });

  it("adim gecisi sirasinda API hatasi sessizce yutulmaz, gorunur hata mesaji gosterilir", async () => {
    const draftAtStep3 = {
      ...emptyDraft,
      current_step: 3,
      payload: { persona_count: 500, target_audience: "Herkes" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep3);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return Promise.reject(new Error("network down"));
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("3. Persona")).toBeInTheDocument());
    fireEvent.click(screen.getByText("İleri"));

    expect(
      await screen.findByText("Adım geçişi kaydedilemedi. Lütfen tekrar deneyin."),
    ).toBeInTheDocument();
  });

  it("adim/modul kontrolleri klavyeyle erisilebilir ve erisilebilir isimlere sahiptir", async () => {
    const draftWithModules = {
      ...emptyDraft,
      current_step: 4,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_url: "https://example.com",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftWithModules);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, draftWithModules);
        }
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("4. Analiz Modülleri")).toBeInTheDocument());

    const checkbox = await screen.findByRole("checkbox");
    checkbox.focus();
    expect(checkbox).toHaveFocus();

    expect(screen.getByRole("button", { name: "Geri" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "İleri" })).toBeInTheDocument();
  });

  // --- Tasarim kaynagi (URL / ekran goruntusu) - Prompt 2 ---------------------

  it("ekran goruntusu secimi (source_type + asset id) autosave payload'una girer", async () => {
    const draftAtStep2 = { ...emptyDraft, current_step: 2, payload: {} };
    const patchCalls: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, draftAtStep2);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          patchCalls.push(JSON.parse(String(init.body)));
          return jsonResponse(200, draftAtStep2);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("radio", { name: /Ekran görüntüsü/ }));

    await waitFor(() =>
      expect(
        patchCalls.some(
          (call) => (call.payload as Record<string, unknown>)?.current_source_type === "screenshot",
        ),
      ).toBe(true),
    );
  });

  it("eski (source type olmayan) bir taslak URL kaynagini varsayar", async () => {
    const legacyDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: { test_type: "existing_site_basic_ux", current_url: "https://example.com" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, legacyDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, legacyDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    expect(screen.getByRole("radio", { name: "URL" })).toBeChecked();
    expect(document.getElementById("wizard-current-url")).toHaveValue("https://example.com");
  });

  it("ozet (5. adim) ekran goruntusu kaynagini gosterir ve baslatma dugmesi artik acik olur", async () => {
    const screenshotDraft = {
      ...emptyDraft,
      current_step: 5,
      payload: {
        project_id: project.id,
        name: "Sepet akisi",
        target_task: "Odeme tamamla",
        test_type: "existing_site_basic_ux",
        current_source_type: "screenshot",
        current_design_asset_id: "asset-1",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
        authorization_confirmed: true,
      },
      missing_fields: [],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/design-assets/asset-1") && (init?.method ?? "GET") === "GET") {
          return jsonResponse(200, {
            id: "asset-1",
            organization_id: "org-1",
            content_type: "image/png",
            byte_size: 2048,
            width: 100,
            height: 80,
            label: null,
            status: "active",
            has_image: true,
            expires_at: null,
            created_at: "2026-07-20T00:00:00Z",
            updated_at: "2026-07-20T00:00:00Z",
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, screenshotDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, screenshotDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getAllByText("Ekran görüntüsü").length).toBeGreaterThan(0));

    const launchButton = await screen.findByRole("button", { name: /başlat/i });
    // Paket 4 Final: gecerli bir design_asset_id secilmisse buton artik
    // ONCEDEN kapatilmaz (asil dogrulama sunucu tarafinda, launch aninda
    // yapilir - bkz. app.services.test_wizard._revalidate_launch_sources).
    expect(launchButton).not.toBeDisabled();
  });

  it("A/B karsilastirmasinda URL/URL akisi bozulmadan calismaya devam eder", async () => {
    const abDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: { test_type: "ab_comparison", current_url: "https://example.com" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    expect(document.getElementById("wizard-current-url")).toHaveValue("https://example.com");
    expect(document.getElementById("wizard-new-url")).toBeInTheDocument();
  });

  // --- Paket 3A: A/B karsilastirmasinda gorsel kaynaklar -----------------------

  it("A/B karsilastirmasinda hem Tasarim A hem Tasarim B icin ekran goruntusu secenegi gosterilir", async () => {
    const abDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: { test_type: "ab_comparison", current_url: "https://example.com" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    // Iki bagimsiz picker: her biri kendi "Ekran görüntüsü" radiogroup girisine sahip.
    expect(screen.getAllByRole("radio", { name: /Ekran görüntüsü/ })).toHaveLength(2);
    // Bileşen etiketi (label) yalnızca radiogroup'un aria-label'ına gömer,
    // ayrı bir görünür başlık olarak render etmez (bkz. DesignSourcePicker.tsx).
    expect(
      screen.getByRole("radiogroup", { name: "Varyant A — Orijinal tasarım - kaynak türü" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("radiogroup", {
        name: "Varyant B — Aynı sitenin değiştirilmiş tasarımı - kaynak türü",
      }),
    ).toBeInTheDocument();
  });

  it("A/B: Tasarim B icin ekran goruntusu secimi autosave payload'una new_source_type olarak girer", async () => {
    const abDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: { test_type: "ab_comparison", current_url: "https://example.com" },
    };
    const patchCalls: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          patchCalls.push(JSON.parse(String(init.body)));
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    const screenshotRadios = screen.getAllByRole("radio", { name: /Ekran görüntüsü/ });
    fireEvent.click(screenshotRadios[1]); // ikinci picker = Tasarim B

    await waitFor(() =>
      expect(
        patchCalls.some(
          (call) => (call.payload as Record<string, unknown>)?.new_source_type === "screenshot",
        ),
      ).toBe(true),
    );
  });

  it("Tasarim Kaynagi adiminda hicbir yerde 'AI ile oluştur' secenegi gosterilmez ve design-generations uc noktasina istek atilmaz", async () => {
    const abDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: { test_type: "ab_comparison", current_url: "https://example.com" },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/design-generations")) {
          throw new Error(`design-generations uc noktasina istek atilmamali: ${url}`);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, abDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    expect(screen.queryByText(/AI ile oluştur/)).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /AI/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("radio", { name: "URL" })).toHaveLength(2);
    expect(screen.getAllByRole("radio", { name: /Ekran görüntüsü/ })).toHaveLength(2);
  });

  it("kabul edilmis eski AI tasarimi olan taslak Tasarim B icin normal bir ekran goruntusu kaynagi gibi hidratlanir", async () => {
    const legacyAcceptedAiDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: {
        test_type: "ab_comparison",
        current_url: "https://example.com",
        new_source_type: "ai_generated",
        new_design_asset_id: "asset-legacy-ai",
        new_ai_generation_id: "job-legacy-1",
      },
    };
    const legacyAsset = {
      id: "asset-legacy-ai",
      organization_id: "org-1",
      content_type: "image/png",
      byte_size: 2048,
      width: 100,
      height: 80,
      label: null,
      status: "active",
      has_image: true,
      expires_at: null,
      created_at: "2026-07-20T00:00:00Z",
      updated_at: "2026-07-20T00:00:00Z",
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/design-generations")) {
          throw new Error(`design-generations uc noktasina istek atilmamali: ${url}`);
        }
        if (url.includes("/api/design-assets/asset-legacy-ai")) {
          return jsonResponse(200, legacyAsset);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, legacyAcceptedAiDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, legacyAcceptedAiDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    expect(
      screen.queryByText(/artık desteklenmeyen bir AI tasarım denemesi/),
    ).not.toBeInTheDocument();
    expect(await screen.findByText(/100 × 80 px/)).toBeInTheDocument();
    const screenshotRadios = screen.getAllByRole("radio", { name: /Ekran görüntüsü/ });
    expect(screenshotRadios[1]).toBeChecked();
  });

  it("yarim kalmis/kabul edilmemis eski AI denemesi olan taslakta uyari gosterilir ve kaynak onceden secili gelmez", async () => {
    const legacyUnacceptedAiDraft = {
      ...emptyDraft,
      current_step: 2,
      payload: {
        test_type: "ab_comparison",
        current_url: "https://example.com",
        new_source_type: "ai_generated",
        new_ai_generation_id: "job-legacy-2",
      },
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/design-generations")) {
          throw new Error(`design-generations uc noktasina istek atilmamali: ${url}`);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, legacyUnacceptedAiDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, legacyUnacceptedAiDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());
    expect(
      await screen.findByText(/artık desteklenmeyen bir AI tasarım denemesi/),
    ).toBeInTheDocument();
    // Eski/gecersiz AI asset kimligi sessizce bir ekran goruntusu onizlemesine
    // baglanmaz - kaynak secimi kullaniciya birakilir (varsayilan olarak URL
    // paneli bos bir alanla gosterilir, bkz. DesignSourcePicker `effectiveSourceType`).
    expect(screen.queryByAltText(/önizlemesi/)).not.toBeInTheDocument();
    const newUrlInput = document.getElementById("wizard-new-url") as HTMLInputElement;
    expect(newUrlInput).toHaveValue("");
  });

  it("ozet (5. adim) A/B icin gorsel bir kaynak (Tasarim B ekran goruntusu) varsa baslatma dugmesi acik olur", async () => {
    const abVisualDraft = {
      ...emptyDraft,
      current_step: 5,
      payload: {
        project_id: project.id,
        name: "AB testi",
        target_task: "Odeme tamamla",
        test_type: "ab_comparison",
        current_url: "https://example.com",
        new_source_type: "screenshot",
        new_design_asset_id: "asset-b",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
        authorization_confirmed: true,
      },
      missing_fields: [],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/design-assets/asset-b") && (init?.method ?? "GET") === "GET") {
          return jsonResponse(200, {
            id: "asset-b",
            organization_id: "org-1",
            content_type: "image/png",
            byte_size: 2048,
            width: 100,
            height: 80,
            label: null,
            status: "active",
            has_image: true,
            expires_at: null,
            created_at: "2026-07-20T00:00:00Z",
            updated_at: "2026-07-20T00:00:00Z",
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abVisualDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, abVisualDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    const launchButton = await screen.findByRole("button", { name: /başlat/i });
    // Paket 4 Final: A/B'nin "Tasarim B" tarafi ekran goruntusu oldugunda da
    // gecerli bir design_asset_id varsa buton artik ONCEDEN kapatilmaz.
    expect(launchButton).not.toBeDisabled();
  });

  it("karisik kaynakta (URL + ekran goruntusu) birlesik onay metnini gosterir", async () => {
    const abMixedDraft = {
      ...emptyDraft,
      current_step: 5,
      payload: {
        project_id: project.id,
        name: "AB testi",
        target_task: "Odeme tamamla",
        test_type: "ab_comparison",
        current_url: "https://example.com",
        new_source_type: "screenshot",
        new_design_asset_id: "asset-b",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
        authorization_confirmed: true,
      },
      missing_fields: [],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/design-assets/asset-b") && (init?.method ?? "GET") === "GET") {
          return jsonResponse(200, {
            id: "asset-b",
            organization_id: "org-1",
            content_type: "image/png",
            byte_size: 2048,
            width: 100,
            height: 80,
            label: null,
            status: "active",
            has_image: true,
            expires_at: null,
            created_at: "2026-07-20T00:00:00Z",
            updated_at: "2026-07-20T00:00:00Z",
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abMixedDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, abMixedDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() =>
      expect(
        screen.getByText(/^Bu URL ve tasarım görsellerini analiz etme yetkisine sahip/),
      ).toBeInTheDocument(),
    );
  });

  it("her iki taraf da ekran goruntusu ise sadece-URL onay metni gosterilmez", async () => {
    const abBothScreenshotDraft = {
      ...emptyDraft,
      current_step: 5,
      payload: {
        project_id: project.id,
        name: "AB testi",
        target_task: "Odeme tamamla",
        test_type: "ab_comparison",
        current_source_type: "screenshot",
        current_design_asset_id: "asset-a",
        new_source_type: "screenshot",
        new_design_asset_id: "asset-b",
        persona_count: 500,
        target_audience: "Yeni musteriler",
        modules: [],
        authorization_confirmed: true,
      },
      missing_fields: [],
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (
          (url.includes("/api/design-assets/asset-a") ||
            url.includes("/api/design-assets/asset-b")) &&
          (init?.method ?? "GET") === "GET"
        ) {
          return jsonResponse(200, {
            id: url.includes("asset-a") ? "asset-a" : "asset-b",
            organization_id: "org-1",
            content_type: "image/png",
            byte_size: 2048,
            width: 100,
            height: 80,
            label: null,
            status: "active",
            has_image: true,
            expires_at: null,
            created_at: "2026-07-20T00:00:00Z",
            updated_at: "2026-07-20T00:00:00Z",
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, abBothScreenshotDraft);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          return jsonResponse(200, abBothScreenshotDraft);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() =>
      expect(
        screen.getByText(/^Bu tasarım görsellerini analiz etme ve kullanma yetkisine sahip/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Bu URL'leri analiz etme yetkisine sahip/)).not.toBeInTheDocument();
  });

  // --- Kaynak turu screenshot'a cevrildiginde uyumsuz modulun otomatik temizlenmesi --

  it("kaynak URL'den screenshot'a cevrilince secili network_device_test otomatik kaldirilir ve uyari gosterilir", async () => {
    let currentDraftPayload: Record<string, unknown> = {
      project_id: "project-1",
      test_type: "existing_site_basic_ux",
      current_source_type: "url",
      current_url: "https://example.com",
      modules: ["network_device_test"],
      persona_count: 500,
      target_audience: "Test kitlesi",
    };
    const patchCalls: Array<Record<string, unknown>> = [];

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/analysis-modules/catalog")) {
          return jsonResponse(200, moduleCatalogResponse);
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "GET") {
          return jsonResponse(200, {
            ...emptyDraft,
            current_step: 2,
            payload: currentDraftPayload,
          });
        }
        if (url.includes("/api/tests/drafts/draft-1") && init?.method === "PATCH") {
          const body = JSON.parse(String(init.body));
          patchCalls.push(body);
          currentDraftPayload = { ...currentDraftPayload, ...(body.payload ?? {}) };
          return jsonResponse(200, {
            ...emptyDraft,
            current_step: 2,
            payload: currentDraftPayload,
          });
        }
        if (url.includes("/api/projects")) return jsonResponse(200, [project]);
        if (url.includes("/api/personas/dimensions")) return jsonResponse(200, []);
        if (url.includes("/api/personas/presets")) return jsonResponse(200, []);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org-1",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        if (url.includes("/api/billing/quote")) {
          return jsonResponse(200, {
            pricing_version: "2026.1",
            test_type: "basic_ux_test",
            persona_count: 500,
            modules: [],
            free_entitlement_feature_key: "basic_ux_test",
            free_entitlement_applicable: true,
            line_items: [],
            required_chips: 0,
            total_chips: 0,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderWizard("/tests/new?draft=draft-1");

    await waitFor(() => expect(screen.getByText("2. Tasarım Kaynağı")).toBeInTheDocument());

    const screenshotRadio = screen.getByRole("radio", { name: /Ekran görüntüsü/ });
    fireEvent.click(screenshotRadio);

    expect(
      await screen.findByText(
        /Ağ ve cihaz testi kaldırıldı\. Bu modül yalnızca canlı URL ile çalışır\./,
      ),
    ).toBeInTheDocument();

    await waitFor(() => {
      const lastCallWithModules = patchCalls.filter((call) => {
        const payload = call.payload as Record<string, unknown> | undefined;
        return payload && "modules" in payload;
      });
      expect(lastCallWithModules.length).toBeGreaterThan(0);
      const payload = lastCallWithModules[lastCallWithModules.length - 1].payload as {
        modules: string[];
      };
      expect(payload.modules).toEqual([]);
    });
  });
});
