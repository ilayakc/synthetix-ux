import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Reports from "./Reports";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Reports", () => {
  it("rapor yokken bos durum gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports")) return jsonResponse(200, []);
        if (url.includes("/api/tests/drafts")) return jsonResponse(200, []);
        if (url.includes("/api/projects")) return jsonResponse(200, []);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Reports />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(
        screen.getByText("Henüz yarım kalan test veya tamamlanmış rapor yok."),
      ).toBeInTheDocument(),
    );
  });

  it("raporlari proje/test adi ve tarihle listeler", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports")) {
          return jsonResponse(200, [
            {
              id: "11111111-1111-1111-1111-111111111111",
              title: "Sentetik simulasyon sonucu",
              simulation_run_id: "22222222-2222-2222-2222-222222222222",
              project_id: "33333333-3333-3333-3333-333333333333",
              project_name: "Anasayfa Yenileme",
              test_definition_id: "44444444-4444-4444-4444-444444444444",
              test_definition_name: "Anasayfa testi",
              variant_name: "Ana Senaryo",
              variant_role: "primary",
              model_version: "heuristic-baseline-2026.1",
              calibration_status: "uncalibrated",
              created_at: "2026-07-01T00:00:00Z",
            },
          ]);
        }
        if (url.includes("/api/tests/drafts")) return jsonResponse(200, []);
        if (url.includes("/api/projects")) {
          return jsonResponse(200, [
            {
              id: "33333333-3333-3333-3333-333333333333",
              organization_id: "org",
              name: "Anasayfa Yenileme",
              description: null,
              status: "active",
              test_count: 1,
              created_at: "2026-07-01T00:00:00Z",
              updated_at: "2026-07-01T00:00:00Z",
              archived_at: null,
            },
          ]);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Reports />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Anasayfa testi")).toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "Anasayfa Yenileme" })).toBeInTheDocument();
    expect(screen.getByText(/Ana Senaryo/)).toBeInTheDocument();
    expect(screen.getByText("Kalibre edilmemiş")).toBeInTheDocument();
  });

  it("yarim kalan testleri kaldigi adim ve devam baglantisiyla gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports")) return jsonResponse(200, []);
        if (url.includes("/api/tests/drafts")) {
          return jsonResponse(200, [
            {
              id: "draft-1",
              organization_id: "org",
              status: "draft",
              current_step: 3,
              payload: { name: "Mobil ödeme denemesi" },
              missing_fields: [],
              created_at: "2026-07-03T00:00:00Z",
              updated_at: "2026-07-04T00:00:00Z",
              warnings: [],
            },
          ]);
        }
        if (url.includes("/api/projects")) return jsonResponse(200, []);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Reports />
      </MemoryRouter>,
    );

    const draftTab = await screen.findByRole("tab", { name: /Yarım kalan testler/ });
    fireEvent.click(draftTab);
    const draftHeading = await screen.findByText("Devam edilecek testler");
    expect(draftHeading).toBeInTheDocument();
    expect(draftHeading.closest("section")).toHaveAttribute("id", "yarim-kalan-testler");
    expect(screen.getByText("Mobil ödeme denemesi")).toBeInTheDocument();
    expect(screen.getByText(/3. adımda bırakıldı/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Mobil ödeme denemesi/ })).toHaveAttribute(
      "href",
      "/tests/new?draft=draft-1",
    );
  });

  it("raporlar alinamadiginda hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports")) return jsonResponse(500, { detail: "Sunucu hatası" });
        if (url.includes("/api/tests/drafts")) return jsonResponse(200, []);
        if (url.includes("/api/projects")) return jsonResponse(200, []);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Reports />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Raporlar yüklenemedi.")).toBeInTheDocument());
  });

  it("hata sonrasinda tekrar deneyerek listeyi yukler", async () => {
    let reportAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports")) {
          reportAttempts += 1;
          return reportAttempts === 1 ? jsonResponse(500, {}) : jsonResponse(200, []);
        }
        if (url.includes("/api/tests/drafts")) return jsonResponse(200, []);
        if (url.includes("/api/projects")) return jsonResponse(200, []);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Reports />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Tekrar dene" }));

    await waitFor(() =>
      expect(screen.getByText("Henüz yarım kalan test veya tamamlanmış rapor yok.")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Raporlar yüklenemedi.")).not.toBeInTheDocument();
  });
});
