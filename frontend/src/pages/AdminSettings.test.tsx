import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AuthProvider } from "../auth/AuthContext";
import { ThemeProvider } from "../theme/ThemeContext";
import AdminSettings from "./AdminSettings";

const session = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "admin@example.com",
  display_name: "Demo Yönetici",
  organization_id: "00000000-0000-0000-0000-000000000002",
  organization_name: "Yönetim",
  role: "owner",
  is_platform_admin: true,
};

const meSettings = {
  user_id: session.user_id,
  email: session.email,
  display_name: session.display_name,
  language: "tr",
  timezone: "Europe/Istanbul",
  theme: "system",
  compact_view: false,
  notify_simulation_completed: true,
  notify_simulation_failed: true,
  notify_report_ready: true,
  notify_low_chip_balance: true,
  low_chip_balance_threshold: 100,
  updated_at: "2026-08-07T10:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute("data-theme");
});

describe("AdminSettings", () => {
  it("hesap bilgilerini, tema tercihini ve guvenli sistem bilgilerini gosterir", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
      if (url.includes("/api/auth/me")) {
        return Promise.resolve({ ok: true, status: 200, json: async () => session });
      }
      if (url.includes("/api/settings/me")) {
        const body = options?.body ? JSON.parse(String(options.body)) : {};
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ ...meSettings, ...body }),
        });
      }
      if (url.includes("/api/admin/settings")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            environment: "development",
            ai_report: { enabled: true, provider: "mock", provider_ready: true, model: null },
            chip_topups: {
              review_mode: "manual_admin_review",
              approval_credits_once: true,
              rejection_note_required: true,
            },
            security: {
              secure_cookies: false,
              access_token_ttl_minutes: 15,
              refresh_token_ttl_days: 30,
              login_rate_limit_max_attempts: 5,
              login_rate_limit_window_minutes: 5,
            },
            operations: {
              log_level: "INFO",
              analyzer_timeout_seconds: 30,
              report_screenshot_retention_days: 30,
            },
          }),
        });
      }
      if (url.includes("/api/ready")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            ready: true,
            database: "ok",
            redis: "ok",
            environment: "development",
          }),
        });
      }
      throw new Error(`Beklenmeyen istek: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthProvider>
        <ThemeProvider>
          <AdminSettings />
        </ThemeProvider>
      </AuthProvider>,
    );

    expect(await screen.findByRole("heading", { name: "Ayarlar" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Görünüm" })).toBeInTheDocument();
    expect(await screen.findByText("Demo Yönetici")).toBeInTheDocument();
    expect(screen.getByText("admin@example.com")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "AI raporlama" })).toBeInTheDocument();
    expect(screen.getByText("mock")).toBeInTheDocument();
    expect(screen.queryByText(/api key/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Koyu/ }));
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    fireEvent.click(screen.getByRole("button", { name: "Değişiklikleri geri al" }));
    expect(document.documentElement).not.toHaveAttribute("data-theme");

    fireEvent.click(screen.getByRole("radio", { name: /Açık/ }));
    expect(document.documentElement).toHaveAttribute("data-theme", "light");
    fireEvent.click(screen.getByRole("button", { name: "Kaydet" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/api/settings/me"),
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ theme: "light" }) }),
      );
    });
    expect(await screen.findByText("Görünüm tercihiniz kaydedildi.")).toBeInTheDocument();
  });
});
