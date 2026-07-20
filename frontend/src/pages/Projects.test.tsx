import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Projects from "./Projects";

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

describe("Projects", () => {
  it("proje yokken bos durum gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return jsonResponse(200, []);
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Projects />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Henüz bir projeniz yok.")).toBeInTheDocument());
  });

  it("projeleri durum rozeti, test sayisi ve Chip etiketiyle listeler", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/projects")) {
          return jsonResponse(200, [
            {
              id: "11111111-1111-1111-1111-111111111111",
              organization_id: "org-1",
              name: "Anasayfa Yenileme",
              description: "Anasayfa akisi UX testi",
              status: "active",
              test_count: 3,
              created_at: "2026-07-01T00:00:00Z",
              updated_at: "2026-07-01T00:00:00Z",
              archived_at: null,
            },
          ]);
        }
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Projects />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Anasayfa Yenileme")).toBeInTheDocument());
    expect(screen.getByText("Aktif")).toBeInTheDocument();
    expect(screen.getByText("3 test")).toBeInTheDocument();
    expect(screen.getByText("Yeni Proje")).toBeInTheDocument();
  });

  it("yeni proje olusturma formu gonderildiginde listeye eklenir", async () => {
    const createdProject = {
      id: "22222222-2222-2222-2222-222222222222",
      organization_id: "org-1",
      name: "Yeni Proje X",
      description: null,
      status: "active",
      test_count: 0,
      created_at: "2026-07-16T00:00:00Z",
      updated_at: "2026-07-16T00:00:00Z",
      archived_at: null,
    };

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/projects") && init?.method === "GET") {
          return jsonResponse(200, []);
        }
        if (url.includes("/api/projects") && init?.method === "POST") {
          return jsonResponse(201, createdProject);
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Projects />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Henüz bir projeniz yok.")).toBeInTheDocument());

    fireEvent.click(screen.getByText("İlk projenizi oluşturun"));
    fireEvent.change(screen.getByLabelText("Proje adı"), {
      target: { value: "Yeni Proje X" },
    });
    fireEvent.click(screen.getByText("Oluştur"));

    await waitFor(() => expect(screen.getByText("Yeni Proje X")).toBeInTheDocument());
  });

  it("projeler alinamadiginda hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/projects")) return jsonResponse(500, { detail: "Sunucu hatası" });
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Projects />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Projeler yüklenemedi.")).toBeInTheDocument());
  });

  it("proje olusturma 409 (ayni isim) hatasinda modal icinde hata gosterir ve modali kapatmaz", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/api/projects") && init?.method === "GET") {
          return jsonResponse(200, []);
        }
        if (url.includes("/api/projects") && init?.method === "POST") {
          return jsonResponse(409, { detail: "Bu isimde bir proje zaten var." });
        }
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    render(
      <MemoryRouter>
        <Projects />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Henüz bir projeniz yok.")).toBeInTheDocument());

    fireEvent.click(screen.getByText("İlk projenizi oluşturun"));
    fireEvent.change(screen.getByLabelText("Proje adı"), {
      target: { value: "Anasayfa Yenileme" },
    });
    fireEvent.click(screen.getByText("Oluştur"));

    await waitFor(() =>
      expect(screen.getByText("Bu isimde bir proje zaten var.")).toBeInTheDocument(),
    );
    // Modal hala acik olmali (kapatilmamis).
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
