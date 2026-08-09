import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthContext";
import AdminShell from "./AdminShell";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminShell", () => {
  it("yalnizca yonetim navigasyonunu gosterir", async () => {
    const adminSession = {
      user_id: "00000000-0000-0000-0000-000000000001",
      email: "admin@example.com",
      display_name: "Demo Yönetici",
      organization_id: "00000000-0000-0000-0000-000000000002",
      organization_name: "Yönetim",
      role: "owner",
      is_platform_admin: true,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: async () =>
            url.includes("/api/admin/summary")
              ? {
                  organization_count: 3,
                  user_count: 8,
                  pending_topup_count: 2,
                  failed_ai_pipeline_count: 1,
                }
              : adminSession,
        }),
      ),
    );

    render(
      <MemoryRouter>
        <AuthProvider>
          <AdminShell>
            <p>Yönetim içeriği</p>
          </AdminShell>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findAllByRole("navigation")).toHaveLength(2);
    expect(screen.getByRole("navigation", { name: "Yönetim ayarları" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Yönetim Özeti" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Chip Talepleri" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "AI İşlemleri" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "İşlem Kayıtları" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ayarlar" })).toHaveAttribute(
      "href",
      "/yonetim/ayarlar",
    );
    expect(screen.queryByRole("link", { name: "Projeler" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Genel Bakış" })).not.toBeInTheDocument();

    expect(screen.getByText("Y")).toBeInTheDocument();
    const notificationButton = await screen.findByRole("button", {
      name: "Bildirimler, 3 işlem bekliyor",
    });
    fireEvent.click(notificationButton);
    expect(screen.getByRole("dialog", { name: "Yönetim bildirimleri" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /2 Chip talebi onay bekliyor/ })).toHaveAttribute(
      "href",
      "/yonetim/chip-talepleri",
    );
    expect(screen.getByRole("link", { name: /1 AI işlemi inceleme gerektiriyor/ })).toHaveAttribute(
      "href",
      "/yonetim/ai-islemleri",
    );

    const sidebar = screen.getByRole("complementary", { name: "Yönetim menüsü" });
    expect(sidebar).not.toHaveClass("is-open");
    fireEvent.click(screen.getByRole("button", { name: "Yönetim menüsünü aç" }));
    expect(sidebar).toHaveClass("is-open");
    fireEvent.click(screen.getByRole("button", { name: "Yönetim menüsü dışına tıkla ve kapat" }));
    expect(sidebar).not.toHaveClass("is-open");
  });
});
