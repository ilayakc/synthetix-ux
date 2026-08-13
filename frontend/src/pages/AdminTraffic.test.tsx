import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import AdminTraffic from "./AdminTraffic";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    blob: async () => new Blob([""], { type: "text/csv" }),
  });
}

const OVERVIEW = {
  range_start: "2026-07-15",
  range_end: "2026-08-13",
  metrics: {
    total_page_views: 1234,
    total_unique_visitors: 456,
    unique_visitors_today: 12,
    unique_visitors_7d: 89,
    unique_visitors_30d: 300,
    total_users: 42,
    total_organizations: 18,
    new_users_in_range: 7,
    new_organizations_in_range: 3,
    successful_logins_in_range: 60,
    unique_login_users_in_range: 25,
    visitor_to_signup_rate: 0.05,
    signup_to_first_login_rate: 0.8,
    campaign_referred_visitors: 40,
    campaign_referred_signups: 5,
  },
  timeseries: [
    { day: "2026-08-10", visits: 10, signups: 1, logins: 5 },
    { day: "2026-08-11", visits: 20, signups: 2, logins: 8 },
  ],
  top_pages: [{ label: "/", visitors: 100, events: 200 }],
  top_sources: [{ label: "linkedin", visitors: 80, events: 120 }],
  top_campaigns: [{ campaign: "accelerator", visitors: 30, signups: 4 }],
  funnel: { visitors: 456, signups: 7, organizations: 3, first_tests: 2 },
};

const USERS = {
  total: 30,
  users: [
    {
      user_id: "u1",
      display_name: "Deneme Kullanıcı",
      email: "kullanici@example.com",
      organization_name: "Örnek Şirket",
      role: "owner",
      registered_at: "2026-08-01T10:00:00Z",
      first_login_at: "2026-08-01T10:00:00Z",
      last_login_at: "2026-08-10T09:00:00Z",
      total_logins: 5,
      logins_7d: 2,
      logins_30d: 5,
      last_activity_at: "2026-08-10T09:00:00Z",
      account_status: "active",
      first_source: "linkedin",
      first_campaign: "accelerator",
      last_source: "google",
      last_campaign: "retarget",
    },
  ],
};

const ORGS = {
  total: 1,
  organizations: [
    {
      organization_id: "o1",
      name: "Örnek Şirket",
      created_at: "2026-07-01T10:00:00Z",
      member_count: 4,
      active_users_30d: 2,
      first_login_at: "2026-07-01T10:00:00Z",
      last_activity_at: "2026-08-10T09:00:00Z",
      total_logins: 12,
      project_count: 3,
      completed_tests: 2,
      first_source: "linkedin",
      first_campaign: "accelerator",
      last_source: "google",
      last_campaign: "retarget",
    },
  ],
};

const VISITS = {
  total: 1,
  events: [
    {
      id: "e1",
      event_type: "page_view",
      occurred_at: "2026-08-10T09:00:00Z",
      path: "/pricing",
      referrer_domain: "google.com",
      utm_source: "linkedin",
      utm_campaign: "accelerator",
      referral_code: null,
      device_category: "desktop",
      browser_family: "Chrome",
      os_family: "macOS",
      country: null,
    },
  ],
};

interface StubOptions {
  overview?: { status: number; body: unknown };
  users?: { status: number; body: unknown };
  orgs?: { status: number; body: unknown };
  visits?: { status: number; body: unknown };
  trackingLinks?: unknown[];
}

function stubFetch(options: StubOptions = {}) {
  const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const method = (init?.method ?? "GET").toUpperCase();
    if (url.includes("/api/admin/analytics/overview")) {
      const o = options.overview ?? { status: 200, body: OVERVIEW };
      return jsonResponse(o.status, o.body);
    }
    if (url.includes("/api/admin/analytics/users/export.csv")) {
      return jsonResponse(200, {});
    }
    if (url.includes("/api/admin/analytics/users")) {
      const u = options.users ?? { status: 200, body: USERS };
      return jsonResponse(u.status, u.body);
    }
    if (url.includes("/api/admin/analytics/organizations/")) {
      return jsonResponse(200, {
        organization_id: "o1",
        name: "Örnek Şirket",
        created_at: "2026-07-01T10:00:00Z",
        member_count: 1,
        project_count: 3,
        completed_tests: 2,
        total_logins: 12,
        first_source: "linkedin",
        first_campaign: "accelerator",
        last_source: "google",
        last_campaign: "retarget",
        members: [
          {
            user_id: "u1",
            display_name: "Deneme Kullanıcı",
            email: "kullanici@example.com",
            role: "owner",
            last_login_at: "2026-08-10T09:00:00Z",
            total_logins: 5,
          },
        ],
      });
    }
    if (url.includes("/api/admin/analytics/organizations")) {
      const o = options.orgs ?? { status: 200, body: ORGS };
      return jsonResponse(o.status, o.body);
    }
    if (url.includes("/api/admin/analytics/visits")) {
      const v = options.visits ?? { status: 200, body: VISITS };
      return jsonResponse(v.status, v.body);
    }
    if (url.includes("/api/admin/analytics/tracking-links") && method === "POST") {
      return jsonResponse(201, {
        id: "l1",
        name: "LinkedIn Outreach",
        destination_path: "/kayit",
        utm_source: "linkedin",
        utm_medium: "outreach",
        utm_campaign: "accelerator_august",
        utm_content: null,
        referral_code: "abc123xyz",
        description: null,
        is_active: true,
        created_at: "2026-08-13T10:00:00Z",
        tracking_url: "/api/analytics/track/abc123xyz",
        stats: {
          total_visits: 0,
          unique_visitors: 0,
          signups: 0,
          organizations: 0,
          first_tests: 0,
          conversion_rate: 0,
          first_visit_at: null,
          last_visit_at: null,
        },
      });
    }
    if (url.includes("/api/admin/analytics/tracking-links")) {
      return jsonResponse(200, options.trackingLinks ?? []);
    }
    throw new Error(`Beklenmeyen istek: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderTraffic(initialEntry = "/yonetim/trafik") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AdminTraffic />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("AdminTraffic", () => {
  it("başlık, sekmeler ve özet metrik kartlarını render eder", async () => {
    stubFetch();
    renderTraffic();

    expect(await screen.findByRole("heading", { name: "Girişler ve Trafik" })).toBeInTheDocument();
    // Beş sekme.
    expect(screen.getByRole("tab", { name: "Genel Bakış" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Kullanıcı Girişleri" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Bağlantılar/Kampanyalar" })).toBeInTheDocument();
    // Metrik kartı değeri (toplam sayfa görüntüleme, tr-TR biçiminde 1.234).
    expect(await screen.findByText("1.234")).toBeInTheDocument();
    expect(screen.getByText("Toplam benzersiz ziyaretçi")).toBeInTheDocument();
  });

  it("sekme değiştirince kullanıcı tablosunu yükler", async () => {
    stubFetch();
    renderTraffic();
    fireEvent.click(await screen.findByRole("tab", { name: "Kullanıcı Girişleri" }));

    expect(await screen.findByText("Deneme Kullanıcı")).toBeInTheDocument();
    expect(screen.getByText("kullanici@example.com")).toBeInTheDocument();
    expect(screen.getByText("Örnek Şirket")).toBeInTheDocument();
  });

  it("tarih filtresi değiştirilince özet yeniden çekilir", async () => {
    const fetchMock = stubFetch();
    renderTraffic();
    await screen.findByText("1.234");
    const before = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/overview")).length;

    fireEvent.click(screen.getByRole("button", { name: "Son 7 gün" }));

    await waitFor(() => {
      const after = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/overview")).length;
      expect(after).toBeGreaterThan(before);
    });
    expect(screen.getByRole("button", { name: "Son 7 gün" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("kullanıcı araması sunucuya iletilir", async () => {
    vi.useFakeTimers();
    const fetchMock = stubFetch();
    renderTraffic();
    fireEvent.click(screen.getByRole("tab", { name: "Kullanıcı Girişleri" }));
    // debounce'u ilerlet
    await vi.advanceTimersByTimeAsync(10);

    const searchInput = screen.getByPlaceholderText("Kullanıcı, e-posta veya şirket");
    fireEvent.change(searchInput, { target: { value: "ornek" } });
    await vi.advanceTimersByTimeAsync(400);

    const searched = fetchMock.mock.calls.some(
      (c) => String(c[0]).includes("/analytics/users") && String(c[0]).includes("search=ornek"),
    );
    expect(searched).toBe(true);
  });

  it("şirket araması sunucuya iletilir", async () => {
    vi.useFakeTimers();
    const fetchMock = stubFetch();
    renderTraffic();
    fireEvent.click(screen.getByRole("tab", { name: "Şirketler" }));
    await vi.advanceTimersByTimeAsync(10);

    fireEvent.change(screen.getByPlaceholderText("Şirket adı"), { target: { value: "ornek" } });
    await vi.advanceTimersByTimeAsync(400);

    const searched = fetchMock.mock.calls.some(
      (c) =>
        String(c[0]).includes("/analytics/organizations") && String(c[0]).includes("search=ornek"),
    );
    expect(searched).toBe(true);
  });

  it("boş kullanıcı listesinde boş durum gösterir", async () => {
    stubFetch({ users: { status: 200, body: { total: 0, users: [] } } });
    renderTraffic();
    fireEvent.click(await screen.findByRole("tab", { name: "Kullanıcı Girişleri" }));

    expect(await screen.findByText("Kullanıcı bulunamadı")).toBeInTheDocument();
  });

  it("API hatası olduğunda hata mesajı gösterir (yetkisiz/500)", async () => {
    stubFetch({ overview: { status: 403, body: { detail: "Bu işlem için yetkiniz yok" } } });
    renderTraffic();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("kullanıcı tablosunda sayfalama çalışır", async () => {
    const fetchMock = stubFetch();
    renderTraffic();
    fireEvent.click(await screen.findByRole("tab", { name: "Kullanıcı Girişleri" }));
    await screen.findByText("Deneme Kullanıcı");

    const next = screen.getByRole("button", { name: "Sonraki" });
    expect(next).not.toBeDisabled();
    fireEvent.click(next);

    await waitFor(() => {
      const paged = fetchMock.mock.calls.some(
        (c) => String(c[0]).includes("/analytics/users") && String(c[0]).includes("offset=25"),
      );
      expect(paged).toBe(true);
    });
  });

  it("kampanya/bağlantı oluşturma formu POST gönderir", async () => {
    const fetchMock = stubFetch();
    renderTraffic();
    fireEvent.click(await screen.findByRole("tab", { name: "Bağlantılar/Kampanyalar" }));

    const form = await screen.findByRole("heading", { name: "Yeni takip bağlantısı" });
    expect(form).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Bağlantı adı *"), {
      target: { value: "LinkedIn Outreach" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Bağlantı oluştur" }));

    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        (c) =>
          String(c[0]).includes("/analytics/tracking-links") &&
          (c[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(posted).toBeTruthy();
    });
    expect(await screen.findByRole("status")).toHaveTextContent("oluşturuldu");
  });

  it("şirket adına tıklayınca detay çekmecesi açılır", async () => {
    stubFetch();
    renderTraffic();
    fireEvent.click(await screen.findByRole("tab", { name: "Şirketler" }));
    fireEvent.click(await screen.findByRole("button", { name: "Örnek Şirket" }));

    const dialog = await screen.findByRole("dialog", { name: "Şirket detayı" });
    expect(within(dialog).getByText("Kullanıcılar")).toBeInTheDocument();
  });
});
