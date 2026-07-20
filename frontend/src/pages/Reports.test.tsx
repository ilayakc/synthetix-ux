import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
        screen.getByText("Henüz tamamlanmış bir simülasyondan üretilmiş rapor yok."),
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
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Reports />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByText("Anasayfa testi — Ana Senaryo")).toBeInTheDocument(),
    );
    expect(screen.getByText("uncalibrated")).toBeInTheDocument();
  });

  it("raporlar alinamadiginda hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports")) return jsonResponse(500, { detail: "Sunucu hatası" });
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
});
