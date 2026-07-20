import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Topbar from "./Topbar";
import { AuthProvider } from "../auth/AuthContext";

const sessionResponse = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "user@example.com",
  display_name: "Test Kullanıcı",
  organization_id: "00000000-0000-0000-0000-000000000000",
  organization_name: "Test Org",
  role: "owner",
};

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

function renderTopbar() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Topbar isMenuOpen={false} onToggleMenu={() => {}} />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Topbar", () => {
  it("gercek kullanim API'sinden Chip bakiyesini gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(200, sessionResponse);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org",
            chip_balance: 250,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderTopbar();

    await waitFor(() => expect(screen.getByText("250 Chip")).toBeInTheDocument());
  });

  it("arama alani sahte sonuc uretmez; sonraki asama bilgisini gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(200, sessionResponse);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderTopbar();

    expect(
      screen.getByText("Proje ve test araması sonraki aşamada etkinleştirilecek."),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Proje veya test ara…")).toBeInTheDocument();
  });

  it("bildirim simgesi sahte bir sayi gostermez", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(200, sessionResponse);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderTopbar();

    const bellButton = screen.getByRole("button", { name: "Bildirimler (yakında)" });
    expect(bellButton).toBeDisabled();
    expect(bellButton.textContent).toMatch(/^\s*$/);
  });

  it("kullanici menusu ve cikis islemi calisir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(200, sessionResponse);
        if (url.endsWith("/api/auth/logout")) return jsonResponse(204, null);
        if (url.includes("/api/billing/usage-summary")) {
          return jsonResponse(200, {
            organization_id: "org",
            chip_balance: 0,
            entitlements: [],
            pricing_version: "2026.1",
          });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderTopbar();

    await waitFor(() => expect(screen.getByText("Test Org")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Çıkış yap" })).toBeInTheDocument();
  });
});
