import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import Step3Personas from "./Step3Personas";
import { ApiError } from "../../api/client";

// api/client'in ag fonksiyonlarini mock'la; ApiError gibi gercek export'lari koru.
vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    listPersonaDimensions: vi.fn(),
    listPersonaPresets: vi.fn(),
    previewPersonaSample: vi.fn(),
  };
});

import {
  listPersonaDimensions,
  listPersonaPresets,
  previewPersonaSample,
} from "../../api/client";
import type { PersonaPresetResponse } from "../../api/client";

const preset = {
  id: "builtin:general_web_users",
  is_builtin: true,
  organization_id: null,
  name: "Genel web kullanıcıları",
  description: null,
  distribution: {},
  status: "active",
  source_builtin_key: null,
  created_at: null,
  updated_at: null,
  archived_at: null,
} satisfies PersonaPresetResponse;

afterEach(() => {
  vi.clearAllMocks();
});

describe("Step3Personas preset/boyut yukleme teshisi", () => {
  it("preset yuklemesi basarisizsa GERCEK hatayi gosterir ve sessizce yutmaz", async () => {
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {});
    // Boyutlar basarili, presetler basarisiz -> ikisi BAGIMSIZ ele alinmali.
    vi.mocked(listPersonaDimensions).mockResolvedValue([
      { key: "device_class", label: "Cihaz sınıfı" },
    ]);
    vi.mocked(listPersonaPresets).mockRejectedValue(
      new ApiError(500, "Sunucu hatası: presetler getirilemedi", null),
    );

    render(<Step3Personas payload={{ persona_count: 500 }} fieldErrors={{}} onChange={vi.fn()} />);

    // Jenerik "yüklenemedi" degil, ASIL ApiError mesaji yuzeye cikar.
    await waitFor(() =>
      expect(screen.getByText("Sunucu hatası: presetler getirilemedi")).toBeInTheDocument(),
    );
    // Gercek hata teshis icin loglanir.
    expect(consoleErr).toHaveBeenCalled();
    consoleErr.mockRestore();
  });

  it("boyut yuklemesi basarisiz olsa bile preset listesi yine de kullanilabilir (decoupled)", async () => {
    const consoleErr = vi.spyOn(console, "error").mockImplementation(() => {});
    // Boyutlar basarisiz, presetler basarili -> preset secimi CALISMAYA devam etmeli.
    vi.mocked(listPersonaDimensions).mockRejectedValue(new ApiError(500, "boyut hatasi", null));
    vi.mocked(listPersonaPresets).mockResolvedValue([preset]);
    vi.mocked(previewPersonaSample).mockResolvedValue({
      generator_version: "v1",
      deterministic_seed: 1,
      distribution_snapshot: {},
      segments: [],
      total_count: 500,
      sample_hash: "hash",
    });

    render(
      <Step3Personas
        payload={{ persona_count: 500, persona_preset_id: preset.id }}
        fieldErrors={{}}
        onChange={vi.fn()}
      />,
    );

    // Boyut hatasina RAGMEN preset secenegi (presetler yuklendi) gorunur.
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /Genel web kullanıcıları/ })).toBeInTheDocument(),
    );
    // Boyut hatasi da sessizce yutulmaz.
    expect(consoleErr).toHaveBeenCalled();
    consoleErr.mockRestore();
  });
});
