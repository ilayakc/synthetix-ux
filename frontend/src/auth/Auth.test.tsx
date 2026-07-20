import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "../App";
import { AuthProvider } from "./AuthContext";

const sessionResponse = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "user@example.com",
  display_name: "Test User",
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

function renderApp(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Kimlik dogrulama route guard ve akislari", () => {
  it("oturum kontrol edilirken yukleniyor durumunu gosterir", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise(() => {})), // hicbir zaman cozulmez
    );

    renderApp("/");

    expect(screen.getByRole("status")).toHaveTextContent("Oturum kontrol ediliyor");
  });

  it("yanlis parola ile giriste genel bir hata mesaji gosterir ve yonlendirmez", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(401, { detail: "Oturum bulunamadi" });
        if (url.endsWith("/api/auth/login")) {
          return jsonResponse(401, { detail: "E-posta veya parola hatali" });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderApp("/giris");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Giriş yap" })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("E-posta"), { target: { value: "user@example.com" } });
    fireEvent.change(screen.getByLabelText("Parola"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Giriş yap" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("E-posta veya parola hatali"),
    );
    expect(screen.getByRole("heading", { name: "Giriş yap" })).toBeInTheDocument();
  });

  it("basarili giris sonrasi korumali sayfaya yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(401, { detail: "Oturum bulunamadi" });
        if (url.endsWith("/api/auth/login")) return jsonResponse(200, sessionResponse);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderApp("/giris");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Giriş yap" })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("E-posta"), { target: { value: "user@example.com" } });
    fireEvent.change(screen.getByLabelText("Parola"), { target: { value: "CorrectHorse123!" } });
    fireEvent.click(screen.getByRole("button", { name: "Giriş yap" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );
  });

  it("google butonu devre disi ve 'Yakında' olarak gosterilir (sahte giris yapmaz)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(401, { detail: "Oturum bulunamadi" });
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderApp("/giris");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Giriş yap" })).toBeInTheDocument(),
    );

    const googleButton = screen.getByRole("button", { name: /Google ile devam et \(Yakında\)/ });
    expect(googleButton).toBeDisabled();
  });

  it("arka planda 401 alindiginda oturumu sonlandirir ve giris ekraninda uyari gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(200, sessionResponse);
        if (url.endsWith("/api/billing/usage-summary")) {
          return jsonResponse(401, { detail: "Oturum gecersiz veya suresi dolmus" });
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    renderApp("/kullanim-ve-chip");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Giriş yap" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Oturumunuzun süresi doldu");
  });

  it("basarili kayit sonrasi korumali sayfaya (Genel Bakis) yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(401, { detail: "Oturum bulunamadi" });
        if (url.endsWith("/api/auth/register")) return jsonResponse(201, sessionResponse);
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderApp("/kayit");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Hesap oluştur" })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("Şirket / organizasyon adı"), {
      target: { value: "Test Org" },
    });
    fireEvent.change(screen.getByLabelText("E-posta"), { target: { value: "user@example.com" } });
    fireEvent.change(screen.getByLabelText("Parola"), { target: { value: "CorrectHorse123!" } });
    fireEvent.click(screen.getByRole("button", { name: "Hesap oluştur" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );
  });

  it("sunucu hatasinda (409, e-posta zaten kayitli) hata mesaji gosterir ve /kayit'ta kalir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/me")) return jsonResponse(401, { detail: "Oturum bulunamadi" });
        if (url.endsWith("/api/auth/register")) {
          return jsonResponse(409, { detail: "Bu e-posta adresi zaten kayıtlı." });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderApp("/kayit");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Hesap oluştur" })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByLabelText("Şirket / organizasyon adı"), {
      target: { value: "Test Org" },
    });
    fireEvent.change(screen.getByLabelText("E-posta"), { target: { value: "user@example.com" } });
    fireEvent.change(screen.getByLabelText("Parola"), { target: { value: "CorrectHorse123!" } });
    fireEvent.click(screen.getByRole("button", { name: "Hesap oluştur" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Bu e-posta adresi zaten kayıtlı."),
    );
    expect(screen.getByRole("heading", { name: "Hesap oluştur" })).toBeInTheDocument();
  });
});
