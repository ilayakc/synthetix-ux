import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import DesignBSourcePicker from "./DesignBSourcePicker";

const mocks = vi.hoisted(() => ({
  getDesignGenerationAvailability: vi.fn(),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getDesignGenerationAvailability: mocks.getDesignGenerationAvailability,
    getDesignAsset: vi.fn().mockRejectedValue(new Error("not used in this test")),
  };
});

function renderPicker(overrides: Partial<React.ComponentProps<typeof DesignBSourcePicker>> = {}) {
  const onSourceTypeChange = vi.fn();
  const onUrlChange = vi.fn();
  const onAssetChange = vi.fn();
  const onAiGenerationAccept = vi.fn();

  const utils = render(
    <DesignBSourcePicker
      label="Tasarım B — Alternatif tasarım"
      sourceType={undefined}
      url={undefined}
      assetId={undefined}
      onSourceTypeChange={onSourceTypeChange}
      onUrlChange={onUrlChange}
      onAssetChange={onAssetChange}
      onAiGenerationAccept={onAiGenerationAccept}
      referenceSourceType="url"
      referenceAssetId={undefined}
      {...overrides}
    />,
  );

  return { ...utils, onSourceTypeChange, onUrlChange, onAssetChange, onAiGenerationAccept };
}

describe("DesignBSourcePicker", () => {
  it("uc secenegi de (URL / Ekran goruntusu / AI ile olustur) tek radiogroup icinde gosterir", () => {
    renderPicker();
    expect(screen.getByRole("radio", { name: "URL" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Ekran görüntüsü/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /AI ile oluştur/ })).toBeInTheDocument();
  });

  it("Tasarim A bir URL ise AI secenegi aciklamali sekilde devre disi kalir", () => {
    renderPicker({ referenceSourceType: "url", referenceAssetId: undefined });
    const aiRadio = screen.getByRole("radio", { name: /AI ile oluştur/ });
    expect(aiRadio).toBeDisabled();
    expect(
      screen.getByText("AI varyant oluşturmak için önce Tasarım A'nın ekran görüntüsünü yükleyin."),
    ).toBeInTheDocument();
  });

  it("Tasarim A bir ekran goruntusu ise AI secenegi etkinlesir ve secildiginde AiDesignGenerator gosterilir", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue({
      available: true,
      provider_name: "mock-model",
      max_prompt_length: 1000,
      disabled_reason: null,
    });

    const { onSourceTypeChange } = renderPicker({
      referenceSourceType: "screenshot",
      referenceAssetId: "asset-a",
      sourceType: "ai_generated",
    });

    const aiRadio = screen.getByRole("radio", { name: /AI ile oluştur/ });
    expect(aiRadio).not.toBeDisabled();
    expect(aiRadio).toBeChecked();

    expect(await screen.findByLabelText("Değişiklik talebi")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: "URL" }));
    expect(onSourceTypeChange).toHaveBeenCalledWith("url");
  });
});
