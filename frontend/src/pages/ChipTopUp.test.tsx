import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import ChipTopUp from "./ChipTopUp";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

const packagesResponse = {
  package_version: "2026.1",
  packages: [
    { key: "starter", name: "Başlangıç paketi", chip_amount: 100 },
    { key: "growth", name: "Büyüme paketi", chip_amount: 500 },
    { key: "scale", name: "Ölçek paketi", chip_amount: 2000 },
  ],
};

function stubFetch(overrides: {
  topupRequests?: unknown[];
  createResponse?: { status: number; body: unknown };
}) {
  const createResponse = overrides.createResponse ?? {
    status: 201,
    body: {
      id: "req-1",
      package_key: "growth",
      chip_amount: 500,
      status: "pending",
      created_at: "2026-07-17T00:00:00Z",
    },
  };

  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url.includes("/api/billing/usage-summary")) {
        return jsonResponse(200, {
          organization_id: "org-1",
          chip_balance: 0,
          entitlements: [],
          pricing_version: "2026.2",
        });
      }
      if (url.includes("/api/billing/chip-packages")) {
        return jsonResponse(200, packagesResponse);
      }
      if (url.includes("/api/billing/topup-requests") && init?.method === "POST") {
        return jsonResponse(createResponse.status, createResponse.body);
      }
      if (url.includes("/api/billing/topup-requests")) {
        return jsonResponse(200, overrides.topupRequests ?? []);
      }
      throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChipTopUp", () => {
  it("paketleri listeler", async () => {
    stubFetch({});

    render(<ChipTopUp />);

    await waitFor(() => expect(screen.getByText(/Başlangıç paketi/)).toBeInTheDocument());
    expect(screen.getByText(/Büyüme paketi/)).toBeInTheDocument();
    expect(screen.getByText(/Ölçek paketi/)).toBeInTheDocument();
  });

  it("sahte kart alanlarini gostermez ve yalnizca secilen paketi gonderir", async () => {
    stubFetch({});

    render(<ChipTopUp />);

    await waitFor(() => expect(screen.getByText(/Büyüme paketi/)).toBeInTheDocument());

    expect(screen.queryByLabelText(/kart numarası/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/kart üzerindeki isim/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/son kullanma tarihi/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/güvenlik kodu/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Ödeme sağlayıcısı henüz aktif değildir/)).toBeInTheDocument();

    const fetchMock = window.fetch as unknown as ReturnType<typeof vi.fn>;
    fireEvent.click(screen.getByText("Yükleme talebi gönder"));

    await waitFor(() =>
      expect(screen.getByText(/Chip bakiyeniz henüz değişmedi/)).toBeInTheDocument(),
    );

    const topupCall = fetchMock.mock.calls.find(
      (call: unknown[]) =>
        (call[0] as string).includes("/api/billing/topup-requests") &&
        (call[1] as RequestInit | undefined)?.method === "POST",
    );
    expect(topupCall).toBeDefined();
    const requestBody = JSON.parse((topupCall![1] as RequestInit).body as string);
    expect(requestBody).toEqual({ package_key: expect.any(String) });
  });

  it("talep gonderme basarili olunca bakiyenin degismedigini belirten bilgilendirme gosterir", async () => {
    stubFetch({});

    render(<ChipTopUp />);

    await waitFor(() => expect(screen.getByText(/Büyüme paketi/)).toBeInTheDocument());
    fireEvent.click(screen.getByText("Yükleme talebi gönder"));

    await waitFor(() =>
      expect(screen.getByText(/Chip bakiyeniz henüz değişmedi/)).toBeInTheDocument(),
    );
  });

  it("gecmis talepleri listeler", async () => {
    stubFetch({
      topupRequests: [
        {
          id: "req-0",
          package_key: "starter",
          chip_amount: 100,
          status: "pending",
          created_at: "2026-07-16T00:00:00Z",
        },
      ],
    });

    render(<ChipTopUp />);

    await waitFor(() =>
      expect(screen.getByText(/100 Chip · Başlangıç paketi/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/\(starter\)/)).not.toBeInTheDocument();
    expect(screen.getByText("Beklemede")).toBeInTheDocument();
  });
});
