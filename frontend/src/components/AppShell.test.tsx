import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import AppShell from "./AppShell";

const sessionResponse = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "user@example.com",
  display_name: "Test Kullanıcı",
  organization_id: "00000000-0000-0000-0000-000000000000",
  organization_name: "Test Şirketi",
  role: "owner",
  is_platform_admin: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AppShell", () => {
  it("kullanıcı menüsünü düğmeyle soldan açar ve arka plana basınca kapatır", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) {
          return Promise.resolve({ ok: true, status: 200, json: async () => sessionResponse });
        }
        if (url.includes("/api/billing/usage-summary")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ chip_balance: 25, entitlements: [] }),
          });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <AuthProvider>
          <AppShell>
            <p>İçerik</p>
          </AppShell>
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Test Kullanıcı")).toBeInTheDocument());
    const sidebar = screen.getByRole("complementary", { name: "Ana menü" });
    expect(sidebar).not.toHaveClass("is-open");

    fireEvent.click(screen.getByRole("button", { name: "Menüyü aç" }));
    expect(sidebar).toHaveClass("is-open");

    fireEvent.click(screen.getByRole("button", { name: "Menü dışına tıkla ve kapat" }));
    expect(sidebar).not.toHaveClass("is-open");
  });
});
