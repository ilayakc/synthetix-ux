import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../../auth/AuthContext";
import Login from "./Login";

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderLogin(initialEntry = "/giris") {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "Oturum bulunamadı" }),
    }),
  );

  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthProvider>
        <Login />
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("Login giriş türleri", () => {
  it("kullanıcı girişini varsayılan olarak gösterir", async () => {
    renderLogin();

    expect(await screen.findByRole("heading", { name: "Giriş yap" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Kullanıcı girişi" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByLabelText("E-posta")).toHaveValue("synthetix.demo.user@example.com");
    expect(screen.getByRole("link", { name: "Ücretsiz hesap oluştur" })).toBeInTheDocument();
  });

  it("aynı sayfada yönetici girişine geçer ve kullanıcı kayıt bağlantısını gizler", async () => {
    renderLogin();
    await screen.findByRole("heading", { name: "Giriş yap" });

    fireEvent.click(screen.getByRole("tab", { name: "Yönetici girişi" }));

    expect(screen.getByRole("heading", { name: "Yönetici girişi" })).toBeInTheDocument();
    expect(screen.getByLabelText("Yönetici e-postası")).toHaveValue(
      "synthetix.demo.admin@example.com",
    );
    expect(screen.getByRole("button", { name: "Yönetim alanına gir" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /hesap oluştur/i })).not.toBeInTheDocument();
  });

  it("eski yönetici giriş bağlantısından gelen sorguyla yönetici sekmesini açar", async () => {
    renderLogin("/giris?tip=yonetici");

    expect(await screen.findByRole("heading", { name: "Yönetici girişi" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Yönetici girişi" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});
