import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ForgotPassword from "./ForgotPassword";
import ResetPassword from "./ResetPassword";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ForgotPassword", () => {
  it("e-posta gonderildiginde onay mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/password-reset/request")) {
          return jsonResponse(200, {
            message: "E-posta adresiniz kayıtlıysa sıfırlama talimatları gönderildi.",
            dev_reset_token: null,
          });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <ForgotPassword />
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText("E-posta"), { target: { value: "user@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Sıfırlama bağlantısı gönder" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "E-posta adresiniz kayıtlıysa sıfırlama talimatları gönderildi.",
      ),
    );
  });
});

describe("ResetPassword", () => {
  function renderResetPassword(initialPath: string) {
    return render(
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/sifre-sifirla" element={<ResetPassword />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("gecerli token ve yeni parola ile basari mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/password-reset/confirm")) {
          return jsonResponse(200, { message: "Parola güncellendi" });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderResetPassword("/sifre-sifirla?token=valid-token");

    fireEvent.change(screen.getByLabelText("Yeni parola"), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.change(screen.getByLabelText("Yeni parola (tekrar)"), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Parolayı güncelle" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Parolanız başarıyla güncellendi. Artık yeni parolanızla giriş yapabilirsiniz.",
      ),
    );
  });

  it("gecersiz/suresi dolmus token ile hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.endsWith("/api/auth/password-reset/confirm")) {
          return jsonResponse(400, { detail: "Sıfırlama bağlantısının süresi dolmuş." });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    renderResetPassword("/sifre-sifirla?token=expired-token");

    fireEvent.change(screen.getByLabelText("Yeni parola"), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.change(screen.getByLabelText("Yeni parola (tekrar)"), {
      target: { value: "NewPassword123!" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Parolayı güncelle" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Sıfırlama bağlantısının süresi dolmuş."),
    );
  });
});
