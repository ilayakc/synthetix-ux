import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Dashboard from "./Dashboard";
import { AuthProvider } from "../auth/AuthContext";

const sessionResponse = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "user@example.com",
  display_name: "Ayşe Yılmaz",
  organization_id: "00000000-0000-0000-0000-000000000000",
  organization_name: "Örnek Organizasyon",
  role: "owner",
};

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

interface StubOptions {
  usage?: unknown;
  projects?: unknown[];
  runs?: unknown[];
  reports?: unknown[];
  ledger?: unknown[];
  failOnce?: boolean;
}

function stubDashboardFetch(options: StubOptions) {
  const usage = options.usage ?? {
    organization_id: "00000000-0000-0000-0000-000000000000",
    chip_balance: 0,
    entitlements: [
      { feature_key: "basic_ux_test", status: "available", quantity: 1, reserved_until: null },
      {
        feature_key: "accessibility_precheck",
        status: "consumed",
        quantity: 1,
        reserved_until: null,
      },
    ],
    pricing_version: "2026.1",
  };
  const projects = options.projects ?? [];
  const runs = options.runs ?? [];
  const reports = options.reports ?? [];
  const ledger = options.ledger ?? [];

  let usageCallCount = 0;

  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/api/auth/me")) return jsonResponse(200, sessionResponse);
      if (url.includes("/api/billing/usage-summary")) {
        usageCallCount += 1;
        if (options.failOnce && usageCallCount === 1) {
          return jsonResponse(500, { detail: "Sunucu hatası" });
        }
        return jsonResponse(200, usage);
      }
      if (url.includes("/api/projects")) return jsonResponse(200, projects);
      if (url.includes("/api/simulations/runs")) return jsonResponse(200, runs);
      if (url.includes("/api/reports")) return jsonResponse(200, reports);
      if (url.includes("/api/billing/chip-ledger")) return jsonResponse(200, ledger);
      throw new Error(`Beklenmeyen istek: ${url}`);
    }),
  );
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Dashboard />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Dashboard", () => {
  it("gerçek API verilerinden özet kartlarını, 0 Chip bakiyesini ve ücretsiz hak durumlarını gösterir", async () => {
    stubDashboardFetch({
      projects: [
        {
          id: "p1",
          organization_id: "org",
          name: "Anasayfa Yenileme",
          description: null,
          status: "active",
          test_count: 2,
          created_at: "2026-07-01T00:00:00Z",
          updated_at: "2026-07-01T00:00:00Z",
          archived_at: null,
        },
        {
          id: "p2",
          organization_id: "org",
          name: "Arşiv Projesi",
          description: null,
          status: "archived",
          test_count: 1,
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z",
          archived_at: "2026-06-15T00:00:00Z",
        },
      ],
      runs: [
        {
          id: "11111111-aaaa-bbbb-cccc-111111111111",
          organization_id: "org",
          test_variant_id: "tv1",
          status: "succeeded",
          progress_percent: 100,
          progress_message: null,
          calibration_status: "uncalibrated",
          deterministic_seed: 1,
          model_version: "heuristic-baseline-2026.1",
          rules_version: null,
          fixture_version: null,
          error: null,
          result: null,
          not_real_user_data_label: "Gerçek kullanıcı verisi değildir",
          methodology_reference: "docs/methodology.md",
          attempt_count: 1,
          started_at: "2026-07-02T00:00:00Z",
          finished_at: "2026-07-02T00:05:00Z",
          created_at: "2026-07-02T00:00:00Z",
          updated_at: "2026-07-02T00:05:00Z",
        },
      ],
    });

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );

    await waitFor(() => expect(screen.getByText("Chip Bakiyesi")).toBeInTheDocument());

    const chipCard = screen.getByText("Chip Bakiyesi").closest(".summary-card");
    expect(chipCard).not.toBeNull();
    expect(within(chipCard as HTMLElement).getByText("0")).toBeInTheDocument();

    const basicUxCard = screen.getByText("Ücretsiz Temel UX Testi").closest(".summary-card");
    expect(within(basicUxCard as HTMLElement).getByText("1")).toBeInTheDocument();
    expect(within(basicUxCard as HTMLElement).getByText("Kullanılabilir")).toBeInTheDocument();

    const a11yCard = screen
      .getByText("Ücretsiz Erişilebilirlik Ön Kontrolü")
      .closest(".summary-card");
    expect(within(a11yCard as HTMLElement).getByText("0")).toBeInTheDocument();
    expect(within(a11yCard as HTMLElement).getByText("Kullanıldı")).toBeInTheDocument();

    const activeProjectsCard = screen.getByText("Aktif Proje Sayısı").closest(".summary-card");
    expect(within(activeProjectsCard as HTMLElement).getByText("1")).toBeInTheDocument();

    const completedCard = screen.getByText("Tamamlanan Simülasyonlar").closest(".summary-card");
    expect(within(completedCard as HTMLElement).getByText("1")).toBeInTheDocument();

    // Kullanicinin oturumdaki adi karsilama basliginda kullanilir, uydurulmaz.
    expect(screen.getByText("Hoş geldiniz, Ayşe Yılmaz")).toBeInTheDocument();
  });

  it("kuyrukta veya calisan bir test varsa Aktif Test kartinda gosterir", async () => {
    stubDashboardFetch({
      runs: [
        {
          id: "22222222-dddd-eeee-ffff-222222222222",
          organization_id: "org",
          test_variant_id: "tv2",
          status: "running",
          progress_percent: 42,
          progress_message: "Senaryolar çalıştırılıyor",
          calibration_status: "uncalibrated",
          deterministic_seed: 2,
          model_version: "heuristic-baseline-2026.1",
          rules_version: null,
          fixture_version: null,
          error: null,
          result: null,
          not_real_user_data_label: "Gerçek kullanıcı verisi değildir",
          methodology_reference: "docs/methodology.md",
          attempt_count: 1,
          started_at: "2026-07-10T00:00:00Z",
          finished_at: null,
          created_at: "2026-07-10T00:00:00Z",
          updated_at: "2026-07-10T00:00:00Z",
        },
      ],
    });

    renderDashboard();

    await waitFor(() =>
      expect(document.querySelector(".active-test-card__name")).toHaveTextContent(
        "Çalıştırma 22222222",
      ),
    );
    expect(screen.getByText("Çalışıyor")).toBeInTheDocument();
    expect(screen.getByText("Senaryolar çalıştırılıyor")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
  });

  it("aktif test yoksa aciklamali bos durum ve Yeni Test Başlat baglantisi gosterir", async () => {
    stubDashboardFetch({});

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("Şu anda devam eden test bulunmuyor.")).toBeInTheDocument(),
    );
    const activeTestSection = screen.getByText("Aktif Test").closest("section");
    expect(
      within(activeTestSection as HTMLElement).getByRole("link", { name: "Yeni Test Başlat" }),
    ).toHaveAttribute("href", "/tests/new");
  });

  it("son aktivitelerde yalnizca gercek API verilerinden turetilen olaylari, en fazla 5 tanesini gosterir", async () => {
    stubDashboardFetch({
      projects: [
        {
          id: "p1",
          organization_id: "org",
          name: "Ödeme Akışı",
          description: null,
          status: "active",
          test_count: 0,
          created_at: "2026-07-05T00:00:00Z",
          updated_at: "2026-07-05T00:00:00Z",
          archived_at: null,
        },
      ],
      reports: [
        {
          id: "r1",
          title: "Sentetik sonuç",
          simulation_run_id: "run-1",
          project_id: "p1",
          project_name: "Ödeme Akışı",
          test_definition_id: "td1",
          test_definition_name: "Ödeme testi",
          variant_name: "Ana Senaryo",
          variant_role: "primary",
          model_version: "heuristic-baseline-2026.1",
          calibration_status: "uncalibrated",
          created_at: "2026-07-06T00:00:00Z",
        },
      ],
      ledger: [
        {
          id: "l1",
          amount: -50,
          entry_type: "reserve",
          reason: "Ödeme testi rezervasyonu",
          reference_type: "simulation_run",
          reference_id: "run-1",
          created_at: "2026-07-07T00:00:00Z",
        },
      ],
    });

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText("Proje oluşturuldu: Ödeme Akışı")).toBeInTheDocument(),
    );
    expect(screen.getByText("Rapor oluşturuldu: Ödeme testi — Ana Senaryo")).toBeInTheDocument();
    expect(screen.getByText(/Chip rezerve edildi: Ödeme testi rezervasyonu/)).toBeInTheDocument();

    const activityList = document.querySelector(".activity-list");
    expect(activityList?.querySelectorAll(".activity-list__item").length).toBeLessThanOrEqual(5);
  });

  it("ozet veriler alinamadiginda hata mesaji ve tekrar dene secenegi gosterir", async () => {
    stubDashboardFetch({ failOnce: true });

    renderDashboard();

    await waitFor(() => expect(screen.getByText("Özet veriler yüklenemedi.")).toBeInTheDocument());

    const retryButton = screen.getByRole("button", { name: "Tekrar dene" });
    fireEvent.click(retryButton);

    await waitFor(() =>
      expect(screen.queryByText("Özet veriler yüklenemedi.")).not.toBeInTheDocument(),
    );
    await waitFor(() => expect(screen.getByText("Chip Bakiyesi")).toBeInTheDocument());
  });
});
