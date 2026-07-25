import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import Simulations from "./Simulations";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function baseRun(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    organization_id: "org-1",
    test_variant_id: "variant-1",
    status: "failed",
    progress_percent: 0,
    progress_message: null,
    calibration_status: "uncalibrated",
    deterministic_seed: 1,
    model_version: "pending-engine",
    rules_version: null,
    fixture_version: null,
    error: "input_snapshot.url gereklidir (network_device_test)",
    result: null,
    retryable: false,
    failure_code: "network_device_test_requires_url",
    not_real_user_data_label: "Gerçek kullanıcı verisi değildir",
    methodology_reference: "docs/methodology.md",
    attempt_count: 1,
    started_at: null,
    finished_at: "2026-07-24T10:01:52Z",
    created_at: "2026-07-24T10:01:00Z",
    updated_at: "2026-07-24T10:01:52Z",
    ...overrides,
  };
}

function stubRuns(runs: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/simulations/runs")) return jsonResponse(200, runs);
      throw new Error(`Beklenmeyen istek: ${url}`);
    }),
  );
}

function renderSimulations() {
  return render(
    <MemoryRouter initialEntries={["/simulasyonlar"]}>
      <Routes>
        <Route path="/simulasyonlar" element={<Simulations />} />
        <Route path="/tests/new" element={<p>Sihirbaz sayfası</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Simulations", () => {
  it("retryable=false olan basarisiz calistirmada 'Yeniden dene' gostermez", async () => {
    stubRuns([baseRun()]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.queryByText("Yeniden dene")).not.toBeInTheDocument();
  });

  it("retryable=false olan basarisiz calistirmada 'Yeni test oluştur' baglantisi gosterir", async () => {
    stubRuns([baseRun()]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(screen.getByRole("link", { name: "Yeni test oluştur" })).toBeInTheDocument();
  });

  it("retryable=false olan calistirmada ham hata metnini birincil mesaj olarak gostermez", async () => {
    stubRuns([baseRun()]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 11111111/)).toBeInTheDocument());
    expect(
      screen.queryByText(/input_snapshot\.url gereklidir/),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/uyumsuz bir analiz modülü nedeniyle tamamlanamadı ve yeniden denenemez/),
    ).toBeInTheDocument();
  });

  it("retryable=true olan gecici hatada 'Yeniden dene' gosterir ve 'Yeni test oluştur' gostermez", async () => {
    stubRuns([
      baseRun({
        id: "22222222-2222-2222-2222-222222222222",
        retryable: true,
        failure_code: null,
        error: "analyzer'a ulasilamadi (network_device_test): connection error",
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 22222222/)).toBeInTheDocument());
    expect(screen.getByText("Yeniden dene")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Yeni test oluştur" })).not.toBeInTheDocument();
    expect(screen.getByText(/Hata: analyzer'a ulasilamadi/)).toBeInTheDocument();
  });

  it("basarili bir calistirmada ne 'Yeniden dene' ne de 'Yeni test oluştur' gosterir", async () => {
    stubRuns([
      baseRun({
        id: "33333333-3333-3333-3333-333333333333",
        status: "succeeded",
        retryable: true,
        failure_code: null,
        error: null,
        result: null,
      }),
    ]);
    renderSimulations();

    await waitFor(() => expect(screen.getByText(/Çalıştırma 33333333/)).toBeInTheDocument());
    expect(screen.queryByText("Yeniden dene")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Yeni test oluştur" })).not.toBeInTheDocument();
  });
});
