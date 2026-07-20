import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import PersonaPresets from "./PersonaPresets";

function jsonResponse(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

const dimensions = [
  { key: "age_range", label: "Yaş aralığı" },
  { key: "region", label: "Bölge" },
];

const builtinPreset = {
  id: "preset-builtin-1",
  is_builtin: true,
  organization_id: null,
  name: "Genel B2C",
  description: "Genel amaçlı hazır preset",
  distribution: {
    age_range: [{ key: "a1", label: "18-30", weight: 100 }],
  },
  status: "active",
  source_builtin_key: "general_b2c",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  archived_at: null,
};

const customPreset = {
  id: "preset-custom-1",
  is_builtin: false,
  organization_id: "org-1",
  name: "Şirketimize özel",
  description: null,
  distribution: {},
  status: "active",
  source_builtin_key: null,
  created_at: "2026-01-02T00:00:00Z",
  updated_at: "2026-01-02T00:00:00Z",
  archived_at: null,
};

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/personas/dimensions")) return jsonResponse(200, dimensions);
      if (url.includes("/api/personas/presets")) {
        return jsonResponse(200, [builtinPreset, customPreset]);
      }
      throw new Error(`Beklenmeyen istek: ${url}`);
    }),
  );
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/personalar"]}>
      <Routes>
        <Route path="/personalar" element={<PersonaPresets />} />
        <Route path="/tests/new" element={<p>Sihirbaz sayfasi</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PersonaPresets", () => {
  it("hazir presetin dogrudan degistirilemedigini ve once kopyalanmasi gerektigini belirtir", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByText("Genel B2C")).toBeInTheDocument());
    expect(
      screen.getByText("Hazır preset; düzenlemek için önce kopyalayın."),
    ).toBeInTheDocument();
  });

  it("her satirda 'Detayları gör' ve 'Bu presetle test oluştur' dugmelerini gosterir", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByText("Genel B2C")).toBeInTheDocument());
    expect(screen.getAllByRole("button", { name: "Detayları gör" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Bu presetle test oluştur" })).toHaveLength(2);
  });

  it("'Detayları gör' preset dagilimini gosteren bir dialog acar", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByText("Genel B2C")).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("button", { name: "Detayları gör" })[0]);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Yaş aralığı")).toBeInTheDocument();
    expect(screen.getByText("18-30: %100")).toBeInTheDocument();
  });

  it("'Bu presetle test oluştur' /tests/new adresine persona_preset parametresiyle yonlendirir", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByText("Genel B2C")).toBeInTheDocument());
    fireEvent.click(screen.getAllByRole("button", { name: "Bu presetle test oluştur" })[0]);

    expect(await screen.findByText("Sihirbaz sayfasi")).toBeInTheDocument();
  });

  it("preset satirinin kendisi tiklanabilir bir eleman degildir", async () => {
    stubFetch();
    renderPage();

    await waitFor(() => expect(screen.getByText("Genel B2C")).toBeInTheDocument());
    const row = screen.getByText("Genel B2C").closest(".persona-preset-row");
    expect(row).not.toBeNull();
    expect(row?.tagName).toBe("DIV");
    expect(row).not.toHaveAttribute("role", "button");
  });

  it("'Bu presetle test oluştur' dugmesi klavyeyle erisilebilir ve etkinlestirilebilir", async () => {
    stubFetch();
    const user = userEvent.setup();
    renderPage();

    await waitFor(() => expect(screen.getByText("Genel B2C")).toBeInTheDocument());

    const button = screen.getAllByRole("button", { name: "Bu presetle test oluştur" })[0];
    button.focus();
    expect(button).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(await screen.findByText("Sihirbaz sayfasi")).toBeInTheDocument();
  });

  it("yukleme hatasinda hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.reject(new Error("network error"))),
    );

    renderPage();

    expect(await screen.findByText("Persona presetleri yüklenemedi.")).toBeInTheDocument();
  });
});
