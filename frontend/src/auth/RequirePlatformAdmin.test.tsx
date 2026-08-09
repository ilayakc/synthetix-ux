import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import RequirePlatformAdmin from "./RequirePlatformAdmin";

function renderGuard(isPlatformAdmin: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        user_id: "00000000-0000-0000-0000-000000000001",
        email: "user@example.com",
        display_name: "Test User",
        organization_id: "00000000-0000-0000-0000-000000000002",
        organization_name: "Test Org",
        role: "owner",
        is_platform_admin: isPlatformAdmin,
      }),
    }),
  );

  render(
    <MemoryRouter initialEntries={["/yonetim"]}>
      <AuthProvider>
        <Routes>
          <Route
            path="/yonetim"
            element={
              <RequirePlatformAdmin>
                <p>Yönetici içeriği</p>
              </RequirePlatformAdmin>
            }
          />
          <Route path="/" element={<p>Genel Bakış içeriği</p>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RequirePlatformAdmin", () => {
  it("platform yoneticisinin korumali icerige erismesine izin verir", async () => {
    renderGuard(true);
    expect(await screen.findByText("Yönetici içeriği")).toBeInTheDocument();
  });

  it("normal kullaniciyi Genel Bakis'a yonlendirir", async () => {
    renderGuard(false);
    expect(await screen.findByText("Genel Bakış içeriği")).toBeInTheDocument();
    expect(screen.queryByText("Yönetici içeriği")).not.toBeInTheDocument();
  });
});
