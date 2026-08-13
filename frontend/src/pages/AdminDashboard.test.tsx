import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AdminDashboard from "./AdminDashboard";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

const pendingRequest = {
  id: "11111111-1111-1111-1111-111111111111",
  organization_id: "22222222-2222-2222-2222-222222222222",
  organization_name: "Örnek Şirket",
  requested_by_email: "owner@example.com",
  requested_by_display_name: "Şirket Sahibi",
  package_key: "growth",
  chip_amount: 500,
  status: "pending",
  created_at: "2026-08-06T12:00:00Z",
  reviewed_at: null,
  reviewed_by_email: null,
  review_note: null,
};

function stubAdminFetch() {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.includes("/api/admin/summary")) {
      return jsonResponse(200, {
        organization_count: 7,
        user_count: 12,
        pending_topup_count: 1,
        failed_ai_pipeline_count: 2,
      });
    }
    if (url.includes("/review") && init?.method === "POST") {
      return jsonResponse(200, {
        ...pendingRequest,
        status: "approved",
        reviewed_at: "2026-08-06T12:05:00Z",
        reviewed_by_email: "admin@example.com",
      });
    }
    if (url.includes("/api/admin/topup-requests")) {
      return jsonResponse(200, [pendingRequest]);
    }
    if (url.includes("/api/admin/login-history")) {
      return jsonResponse(200, {
        total_logins: 5,
        unique_users: 3,
        events: [
          {
            id: "33333333-3333-3333-3333-333333333333",
            user_id: "44444444-4444-4444-4444-444444444444",
            email: "kullanici@example.com",
            display_name: "Deneme Kullanıcı",
            organization_name: "Örnek Şirket",
            logged_in_at: "2026-08-06T09:30:00Z",
            ip_address: "203.0.113.7",
            user_agent: "Mozilla/5.0",
            login_type: "password",
          },
        ],
      });
    }
    throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderDashboard(section: "overview" | "topups" | "ai" | "audit" | "logins") {
  return render(
    <MemoryRouter>
      <AdminDashboard section={section} />
    </MemoryRouter>,
  );
}

describe("AdminDashboard", () => {
  it("platform ozetini ve bekleyen Chip talebini gosterir", async () => {
    stubAdminFetch();
    renderDashboard("overview");

    expect(await screen.findByRole("heading", { name: "Yönetim Özeti" })).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Başarısız AI işlemi")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Chip taleplerini incele/ })).toHaveAttribute(
      "href",
      "/yonetim/chip-talepleri",
    );
  });

  it("talebi onaylar ve yonetici notunu API'ye gonderir", async () => {
    const fetchMock = stubAdminFetch();
    renderDashboard("topups");
    await screen.findByText("Örnek Şirket");

    fireEvent.change(screen.getByLabelText("Örnek Şirket için yönetici notu"), {
      target: { value: "Ödeme doğrulandı" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Onayla" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("500 Chip onaylandı"));
    const reviewCall = fetchMock.mock.calls.find(
      (call) => String(call[0]).includes("/review") && call[1]?.method === "POST",
    );
    expect(JSON.parse(reviewCall?.[1]?.body as string)).toEqual({
      action: "approve",
      note: "Ödeme doğrulandı",
    });
  });

  it("giris kayitlarini ve sayaclari gosterir", async () => {
    stubAdminFetch();
    renderDashboard("logins");

    expect(await screen.findByRole("heading", { name: "Giriş Kayıtları" })).toBeInTheDocument();
    expect(await screen.findByText("Deneme Kullanıcı")).toBeInTheDocument();
    expect(screen.getByText("kullanici@example.com")).toBeInTheDocument();
    expect(screen.getByText("203.0.113.7")).toBeInTheDocument();
    // Farkli kullanici (3) ve toplam giris (5) sayaclari.
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("aciklama olmadan reddetmeye izin vermez", async () => {
    const fetchMock = stubAdminFetch();
    renderDashboard("topups");
    await screen.findByText("Örnek Şirket");
    fireEvent.click(screen.getByRole("button", { name: "Reddet" }));

    expect(screen.getByRole("alert")).toHaveTextContent("açıklama yazın");
    expect(fetchMock.mock.calls.some((call) => String(call[0]).includes("/review"))).toBe(false);
  });
});
