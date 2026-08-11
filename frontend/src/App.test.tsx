import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";

const sessionResponse = {
  user_id: "00000000-0000-0000-0000-000000000001",
  email: "user@example.com",
  display_name: "Test User",
  organization_id: "00000000-0000-0000-0000-000000000000",
  organization_name: "Test Org",
  role: "owner",
  is_platform_admin: false,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("gecerli bir oturumda uygulama kabugunu, durustluk bandini ve ana menuyu render eder", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => sessionResponse,
      }),
    );

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );

    expect(screen.getByText(/kalibre edilmemiş sentetik tahminlerdir/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Genel Bakış" })).toBeInTheDocument();
  });

  it("oturum yoksa public ana sayfayı gösterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Oturum bulunamadi" }),
      }),
    );

    render(
      <MemoryRouter initialEntries={["/"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(
        screen.getByRole("heading", {
          name: "Tasarım risklerini geliştirmeye geçmeden önce görün.",
        }),
      ).toBeInTheDocument(),
    );
  });

  it("/register adresini /kayit adresine yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Oturum bulunamadi" }),
      }),
    );

    render(
      <MemoryRouter initialEntries={["/register"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Ücretsiz hesap oluştur" })).toBeInTheDocument(),
    );
  });

  it("tanimlanmamis bir adres acildiginda oturum yoksa giris ekranina yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Oturum bulunamadi" }),
      }),
    );

    render(
      <MemoryRouter initialEntries={["/olmayan-bir-sayfa"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Giriş yap" })).toBeInTheDocument(),
    );
  });

  it("tanimlanmamis bir adres acildiginda gecerli oturumda ana panele yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => sessionResponse,
      }),
    );

    render(
      <MemoryRouter initialEntries={["/olmayan-bir-sayfa"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );
  });

  it("gecerli oturumda /giris adresi acildiginda ana panele yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => sessionResponse,
      }),
    );

    render(
      <MemoryRouter initialEntries={["/giris"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );
  });

  it("gecerli oturumda /kayit adresi acildiginda ana panele yonlendirir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => sessionResponse,
      }),
    );

    render(
      <MemoryRouter initialEntries={["/kayit"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Genel Bakış" })).toBeInTheDocument(),
    );
  });

  it("giris ekraninda ucretsiz hesap olusturma baglantisi /kayit adresine gider", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ detail: "Oturum bulunamadi" }),
      }),
    );

    render(
      <MemoryRouter initialEntries={["/giris"]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Ücretsiz hesap oluştur" })).toHaveAttribute(
        "href",
        "/kayit",
      ),
    );
  });
});
