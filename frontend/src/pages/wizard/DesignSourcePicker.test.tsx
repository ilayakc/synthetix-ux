import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DesignSourcePicker from "./DesignSourcePicker";
import { ApiError } from "../../api/client";

const mocks = vi.hoisted(() => ({
  getDesignAsset: vi.fn(),
  uploadDesignAsset: vi.fn(),
  deleteDesignAsset: vi.fn(),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    getDesignAsset: mocks.getDesignAsset,
    uploadDesignAsset: mocks.uploadDesignAsset,
    deleteDesignAsset: mocks.deleteDesignAsset,
    getDesignAssetPreviewUrl: (assetId: string) => `http://api.test/api/design-assets/${assetId}/preview`,
  };
});

const assetMeta = {
  id: "asset-1",
  organization_id: "org-1",
  content_type: "image/png" as const,
  byte_size: 2048,
  width: 100,
  height: 80,
  label: null,
  status: "active" as const,
  has_image: true,
  expires_at: "2026-08-01T00:00:00Z",
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z",
};

function renderPicker(overrides: Partial<React.ComponentProps<typeof DesignSourcePicker>> = {}) {
  const onSourceTypeChange = vi.fn();
  const onUrlChange = vi.fn();
  const onAssetChange = vi.fn();

  const utils = render(
    <DesignSourcePicker
      label="Tasarım kaynağı"
      sourceType={undefined}
      url={undefined}
      assetId={undefined}
      onSourceTypeChange={onSourceTypeChange}
      onUrlChange={onUrlChange}
      onAssetChange={onAssetChange}
      {...overrides}
    />,
  );

  return { ...utils, onSourceTypeChange, onUrlChange, onAssetChange };
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("DesignSourcePicker", () => {
  it("URL ve Ekran görüntüsü seçeneklerini gösterir", () => {
    renderPicker();
    expect(screen.getByRole("radio", { name: "URL" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Ekran görüntüsü/ })).toBeInTheDocument();
  });

  it("URL girdisi degistiginde onUrlChange cagrilir", async () => {
    const user = userEvent.setup();
    const { onUrlChange } = renderPicker({ sourceType: "url" });

    const urlInput = document.getElementById("design-source-url") as HTMLInputElement;
    await user.type(urlInput, "https://example.com");

    expect(onUrlChange).toHaveBeenCalled();
  });

  it("dosya secilerek yukleme yapilir ve onAssetChange yeni asset id ile cagrilir", async () => {
    mocks.uploadDesignAsset.mockResolvedValue(assetMeta);
    const user = userEvent.setup();
    const { onAssetChange } = renderPicker({ sourceType: "screenshot" });

    const fileInput = screen.getByLabelText(/tasarım ekran görüntüsü dosyası seç/i);
    const file = new File(["fake-bytes"], "design.png", { type: "image/png" });
    await user.upload(fileInput, file);

    await waitFor(() => expect(onAssetChange).toHaveBeenCalledWith("asset-1"));
    expect(await screen.findByAltText(/önizlemesi/)).toBeInTheDocument();
  });

  it("surukle-birak (drag-and-drop) ile yukleme yapar", async () => {
    mocks.uploadDesignAsset.mockResolvedValue(assetMeta);
    const { onAssetChange, container } = renderPicker({ sourceType: "screenshot" });

    const dropzone = container.querySelector(".design-source-dropzone") as HTMLElement;
    const file = new File(["fake-bytes"], "design.png", { type: "image/png" });

    fireEvent.dragEnter(dropzone);
    expect(dropzone.className).toContain("design-source-dropzone--active");
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(onAssetChange).toHaveBeenCalledWith("asset-1"));
  });

  it("yukleme sirasinda ilerleme yuzdesini gosterir", async () => {
    let capturedOnProgress: ((loaded: number, total: number) => void) | undefined;
    let resolveUpload: ((value: typeof assetMeta) => void) | undefined;
    mocks.uploadDesignAsset.mockImplementation(
      (_file: File, options?: { onProgress?: (loaded: number, total: number) => void }) => {
        capturedOnProgress = options?.onProgress;
        return new Promise((resolve) => {
          resolveUpload = resolve;
        });
      },
    );

    const user = userEvent.setup();
    renderPicker({ sourceType: "screenshot" });

    const fileInput = screen.getByLabelText(/tasarım ekran görüntüsü dosyası seç/i);
    const file = new File(["fake-bytes"], "design.png", { type: "image/png" });
    await user.upload(fileInput, file);

    expect(screen.getByText("Yükleniyor…")).toBeInTheDocument();

    act(() => capturedOnProgress?.(50, 100));
    expect(await screen.findByText(/%50/)).toBeInTheDocument();

    await act(async () => {
      resolveUpload?.(assetMeta);
      await Promise.resolve();
    });
  });

  it("basarisiz upload mesaji gosterir", async () => {
    mocks.uploadDesignAsset.mockRejectedValue(new ApiError(422, "Desteklenmeyen görsel formatı", null));
    const user = userEvent.setup();
    renderPicker({ sourceType: "screenshot" });

    const fileInput = screen.getByLabelText(/tasarım ekran görüntüsü dosyası seç/i);
    const file = new File(["fake-bytes"], "design.png", { type: "image/png" });
    await user.upload(fileInput, file);

    expect(await screen.findByText("Desteklenmeyen görsel formatı")).toBeInTheDocument();
  });

  it("basarisiz yeni yukleme mevcut basarili asset'i korur", async () => {
    mocks.getDesignAsset.mockResolvedValue(assetMeta);
    mocks.uploadDesignAsset.mockRejectedValue(new ApiError(422, "Gecersiz dosya", null));
    const user = userEvent.setup();
    const { onAssetChange } = renderPicker({ sourceType: "screenshot", assetId: "asset-1" });

    await screen.findByAltText(/önizlemesi/);

    const fileInput = screen.getByLabelText(/tasarım ekran görüntüsü dosyası seç/i);
    const file = new File(["fake-bytes"], "bad.png", { type: "image/png" });
    await user.upload(fileInput, file);

    expect(await screen.findByText("Gecersiz dosya")).toBeInTheDocument();
    // Onceki basarili asset hala goruntuleniyor, onAssetChange BOS/null ile cagrilmadi.
    expect(screen.getByAltText(/önizlemesi/)).toBeInTheDocument();
    expect(onAssetChange).not.toHaveBeenCalled();
  });

  it("görseli kaldir dugmesi deleteDesignAsset'i cagirir ve onAssetChange(null) tetikler", async () => {
    mocks.getDesignAsset.mockResolvedValue(assetMeta);
    mocks.deleteDesignAsset.mockResolvedValue(undefined);
    const user = userEvent.setup();
    const { onAssetChange } = renderPicker({ sourceType: "screenshot", assetId: "asset-1" });

    await screen.findByAltText(/önizlemesi/);
    await user.click(screen.getByRole("button", { name: "Görseli kaldır" }));

    await waitFor(() => expect(mocks.deleteDesignAsset).toHaveBeenCalledWith("asset-1"));
    expect(onAssetChange).toHaveBeenCalledWith(null);
  });

  it("silme hatasinda asset referansi korunur ve hata gosterilir", async () => {
    mocks.getDesignAsset.mockResolvedValue(assetMeta);
    mocks.deleteDesignAsset.mockRejectedValue(new ApiError(500, "Görsel kaldırılamadı", null));
    const user = userEvent.setup();
    const { onAssetChange } = renderPicker({ sourceType: "screenshot", assetId: "asset-1" });

    await screen.findByAltText(/önizlemesi/);
    await user.click(screen.getByRole("button", { name: "Görseli kaldır" }));

    expect(await screen.findByText("Görsel kaldırılamadı")).toBeInTheDocument();
    expect(onAssetChange).not.toHaveBeenCalled();
    expect(screen.getByAltText(/önizlemesi/)).toBeInTheDocument();
  });

  it("sayfa yenilenmesini simule eder: assetId ile mount edildiginde metadata/onizleme yuklenir", async () => {
    mocks.getDesignAsset.mockResolvedValue(assetMeta);
    renderPicker({ sourceType: "screenshot", assetId: "asset-1" });

    await waitFor(() => expect(mocks.getDesignAsset).toHaveBeenCalledWith("asset-1"));
    expect(await screen.findByAltText(/önizlemesi/)).toBeInTheDocument();
    expect(screen.getByText(/100 × 80 px/)).toBeInTheDocument();
  });

  it("silinmis/suresi dolmus asset icin anlasilir hata gosterir ve sahte onizleme gostermez", async () => {
    mocks.getDesignAsset.mockRejectedValue(new ApiError(404, "Tasarım gorseli bulunamadi", null));
    renderPicker({ sourceType: "screenshot", assetId: "asset-1" });

    expect(await screen.findByRole("alert")).toHaveTextContent(/artık kullanılamıyor/);
    expect(screen.queryByAltText(/önizlemesi/)).not.toBeInTheDocument();
  });

  it("dosya secme alani klavye ile erisilebilir bir isme sahiptir", () => {
    renderPicker({ sourceType: "screenshot" });
    const fileInput = screen.getByLabelText(/tasarım ekran görüntüsü dosyası seç/i);
    expect(fileInput).toHaveAttribute("type", "file");
    expect(screen.getByRole("button", { name: "Dosya seç" })).toBeInTheDocument();
  });

  it("desteklenmeyen test turunde ekran goruntusu secenegini aciklamayla birlikte kullanilamaz gosterir", () => {
    renderPicker({
      sourceType: "url",
      screenshotDisabledReason:
        "Erişilebilirlik ön kontrolü DOM ve sayfa yapısı gerektirdiği için URL ile çalışır.",
    });

    const screenshotRadio = screen.getByRole("radio", { name: /Ekran görüntüsü/ });
    expect(screenshotRadio).toBeDisabled();
    expect(
      screen.getByText("Erişilebilirlik ön kontrolü DOM ve sayfa yapısı gerektirdiği için URL ile çalışır."),
    ).toBeInTheDocument();
  });
});
