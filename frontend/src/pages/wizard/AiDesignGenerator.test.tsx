import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import AiDesignGenerator from "./AiDesignGenerator";
import { ApiError } from "../../api/client";

const mocks = vi.hoisted(() => ({
  getDesignGenerationAvailability: vi.fn(),
  createDesignGeneration: vi.fn(),
  getDesignGeneration: vi.fn(),
  cancelDesignGeneration: vi.fn(),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getDesignGenerationAvailability: mocks.getDesignGenerationAvailability,
    createDesignGeneration: mocks.createDesignGeneration,
    getDesignGeneration: mocks.getDesignGeneration,
    cancelDesignGeneration: mocks.cancelDesignGeneration,
    getDesignAssetPreviewUrl: (assetId: string) => `http://api.test/api/design-assets/${assetId}/preview`,
  };
});

const availableResponse = {
  available: true,
  provider_name: "mock-model-v1",
  max_prompt_length: 1000,
  disabled_reason: null,
};

const disabledResponse = {
  available: false,
  provider_name: null,
  max_prompt_length: 1000,
  disabled_reason: "AI görsel üretim sağlayıcısı henüz yapılandırılmadı. Tasarım B için URL veya ekran görüntüsü kullanabilirsiniz.",
};

function renderGenerator(overrides: Partial<React.ComponentProps<typeof AiDesignGenerator>> = {}) {
  const onAccept = vi.fn();
  const onUseUpload = vi.fn();
  const onUseUrl = vi.fn();
  const utils = render(
    <AiDesignGenerator
      referenceAssetId="asset-a"
      onAccept={onAccept}
      onUseUpload={onUseUpload}
      onUseUrl={onUseUrl}
      {...overrides}
    />,
  );
  return { ...utils, onAccept, onUseUpload, onUseUrl };
}

beforeEach(() => {
  mocks.getDesignGenerationAvailability.mockReset();
  mocks.createDesignGeneration.mockReset();
  mocks.getDesignGeneration.mockReset();
  mocks.cancelDesignGeneration.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AiDesignGenerator", () => {
  it("referans gorsel yoksa (Tasarim A URL ise) aciklamali sekilde engellenir, availability hic cagrilmaz", async () => {
    renderGenerator({
      referenceAssetId: undefined,
      referenceUnavailableReason:
        "AI varyant oluşturmak için önce Tasarım A'nın ekran görüntüsünü yükleyin.",
    });

    expect(
      await screen.findByText("AI varyant oluşturmak için önce Tasarım A'nın ekran görüntüsünü yükleyin."),
    ).toBeInTheDocument();
    expect(mocks.getDesignGenerationAvailability).not.toHaveBeenCalled();
  });

  it("saglayici yapilandirilmamissa aciklamali devre disi durum gosterilir", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue(disabledResponse);
    renderGenerator();

    expect(await screen.findByText(disabledResponse.disabled_reason!)).toBeInTheDocument();
    expect(screen.queryByText("Varyant oluştur")).not.toBeInTheDocument();
  });

  it("saglayici acikken prompt alani, karakter sayaci ve onay kutusu gosterilir; onay olmadan buton devre disi kalir", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue(availableResponse);
    renderGenerator();

    const promptField = await screen.findByLabelText("Değişiklik talebi");
    fireEvent.change(promptField, { target: { value: "Ana CTA'yi turuncu yap" } });

    expect(screen.getByText(/22 \/ 1000 karakter/)).toBeInTheDocument();

    const submitButton = screen.getByText("Varyant oluştur");
    expect(submitButton).toBeDisabled();

    fireEvent.click(
      screen.getByLabelText(/Bu tasarım görselinin.*bir uzak AI sağlayıcısına\s*aktarılmasını onaylıyorum/),
    );
    expect(submitButton).not.toBeDisabled();
  });

  it("bos prompt ile gonderme dugmesi devre disi kalir", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue(availableResponse);
    renderGenerator();

    await screen.findByLabelText("Değişiklik talebi");
    fireEvent.click(
      screen.getByLabelText(/Bu tasarım görselinin.*bir uzak AI sağlayıcısına\s*aktarılmasını onaylıyorum/),
    );

    expect(screen.getByText("Varyant oluştur")).toBeDisabled();
  });

  it("gonderim sonrasi kuyruk/isleniyor durumu gosterilir ve polling ile sonuc alinir", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.getDesignGenerationAvailability.mockResolvedValue(availableResponse);
    mocks.createDesignGeneration.mockResolvedValue({
      id: "job-1",
      status: "queued",
      provider: "remote",
      model_name: "mock-model-v1",
      created_at: "2026-07-22T00:00:00Z",
      started_at: null,
      finished_at: null,
      result_asset: null,
      error_message: null,
    });
    mocks.getDesignGeneration.mockResolvedValue({
      id: "job-1",
      status: "succeeded",
      provider: "remote",
      model_name: "mock-model-v1",
      created_at: "2026-07-22T00:00:00Z",
      started_at: "2026-07-22T00:00:01Z",
      finished_at: "2026-07-22T00:00:05Z",
      result_asset: { id: "asset-result-1", content_type: "image/png", width: 100, height: 80, expires_at: null },
      error_message: null,
    });

    renderGenerator();
    fireEvent.change(await screen.findByLabelText("Değişiklik talebi"), {
      target: { value: "Baslik kisalt" },
    });
    fireEvent.click(
      screen.getByLabelText(/Bu tasarım görselinin.*bir uzak AI sağlayıcısına\s*aktarılmasını onaylıyorum/),
    );
    fireEvent.click(screen.getByText("Varyant oluştur"));

    await waitFor(() => expect(mocks.createDesignGeneration).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Talebiniz sıraya alındı…")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(await screen.findByText("AI tarafından üretilmiş tasarım taslağı")).toBeInTheDocument();
    expect(screen.getByText("Tasarım B olarak kullan")).toBeInTheDocument();
  });

  it("kabul (accept) tiklaninca onAccept jobId ve assetId ile cagrilir, aksi halde hicbir zaman cagrilmaz", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue(availableResponse);
    mocks.createDesignGeneration.mockResolvedValue({
      id: "job-2",
      status: "succeeded",
      provider: "remote",
      model_name: "mock-model-v1",
      created_at: "2026-07-22T00:00:00Z",
      started_at: "2026-07-22T00:00:01Z",
      finished_at: "2026-07-22T00:00:05Z",
      result_asset: { id: "asset-result-2", content_type: "image/png", width: 100, height: 80, expires_at: null },
      error_message: null,
    });

    const { onAccept } = renderGenerator();
    fireEvent.change(await screen.findByLabelText("Değişiklik talebi"), {
      target: { value: "Baslik kisalt" },
    });
    fireEvent.click(
      screen.getByLabelText(/Bu tasarım görselinin.*bir uzak AI sağlayıcısına\s*aktarılmasını onaylıyorum/),
    );
    fireEvent.click(screen.getByText("Varyant oluştur"));

    const useButton = await screen.findByText("Tasarım B olarak kullan");
    expect(onAccept).not.toHaveBeenCalled();
    fireEvent.click(useButton);
    expect(onAccept).toHaveBeenCalledWith("job-2", "asset-result-2");
  });

  it("basarisiz uretimde hata mesaji ve yeniden dene secenegi gosterilir", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue(availableResponse);
    mocks.createDesignGeneration.mockResolvedValue({
      id: "job-3",
      status: "failed",
      provider: "remote",
      model_name: "mock-model-v1",
      created_at: "2026-07-22T00:00:00Z",
      started_at: "2026-07-22T00:00:01Z",
      finished_at: "2026-07-22T00:00:05Z",
      result_asset: null,
      error_message: "AI gorsel uretimi basarisiz oldu; lutfen tekrar deneyin.",
    });

    renderGenerator();
    fireEvent.change(await screen.findByLabelText("Değişiklik talebi"), {
      target: { value: "Baslik kisalt" },
    });
    fireEvent.click(
      screen.getByLabelText(/Bu tasarım görselinin.*bir uzak AI sağlayıcısına\s*aktarılmasını onaylıyorum/),
    );
    fireEvent.click(screen.getByText("Varyant oluştur"));

    expect(await screen.findByText("AI gorsel uretimi basarisiz oldu; lutfen tekrar deneyin.")).toBeInTheDocument();
    expect(screen.getByText("Yeniden dene")).toBeInTheDocument();
  });

  it("reddet/kendi ekran goruntumu yukle/url kullan dugmeleri dogru callback'leri tetikler", async () => {
    mocks.getDesignGenerationAvailability.mockResolvedValue(availableResponse);
    mocks.createDesignGeneration.mockResolvedValue({
      id: "job-4",
      status: "succeeded",
      provider: "remote",
      model_name: "mock-model-v1",
      created_at: "2026-07-22T00:00:00Z",
      started_at: "2026-07-22T00:00:01Z",
      finished_at: "2026-07-22T00:00:05Z",
      result_asset: { id: "asset-result-4", content_type: "image/png", width: 100, height: 80, expires_at: null },
      error_message: null,
    });

    const { onUseUpload, onUseUrl } = renderGenerator();
    fireEvent.change(await screen.findByLabelText("Değişiklik talebi"), {
      target: { value: "Baslik kisalt" },
    });
    fireEvent.click(
      screen.getByLabelText(/Bu tasarım görselinin.*bir uzak AI sağlayıcısına\s*aktarılmasını onaylıyorum/),
    );
    fireEvent.click(screen.getByText("Varyant oluştur"));

    await screen.findByText("Tasarım B olarak kullan");
    fireEvent.click(screen.getByText("Kendi ekran görüntümü yükle"));
    expect(onUseUpload).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("URL kullan"));
    expect(onUseUrl).toHaveBeenCalledTimes(1);
  });

  it("availability yuklenirken hata olursa hata mesaji gosterilir", async () => {
    mocks.getDesignGenerationAvailability.mockRejectedValue(new ApiError(500, "Sunucu hatası", null));
    renderGenerator();

    expect(await screen.findByText("Sunucu hatası")).toBeInTheDocument();
  });
});
