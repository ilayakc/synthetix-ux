import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ScreenshotCtaSelector from "./ScreenshotCtaSelector";
import type { CtaAnnotation, PageAnalysisResponse } from "../../api/client";

const mocks = vi.hoisted(() => ({
  createPageAnalysisForDesignAsset: vi.fn(),
  getPageAnalysis: vi.fn(),
  patchWizardDraft: vi.fn(),
}));

vi.mock("../../api/client", async () => {
  const actual = await vi.importActual<typeof import("../../api/client")>("../../api/client");
  return {
    ...actual,
    createPageAnalysisForDesignAsset: mocks.createPageAnalysisForDesignAsset,
    getPageAnalysis: mocks.getPageAnalysis,
    patchWizardDraft: mocks.patchWizardDraft,
  };
});

function baseAnalysis(overrides: Partial<PageAnalysisResponse> = {}): PageAnalysisResponse {
  return {
    id: "analysis-1",
    organization_id: "org-1",
    source_kind: "design_asset",
    url: null,
    design_asset_id: "asset-1",
    design_asset_still_linked: true,
    status: "queued",
    attempt_count: 0,
    error: null,
    error_code: null,
    snapshot_version: "design-asset-snapshot-1",
    analyzer_version: null,
    source: "design_asset",
    features: null,
    has_screenshot: true,
    image_width: 800,
    image_height: 600,
    screenshot_content_type: "image/png",
    content_sha256: "a".repeat(64),
    started_at: null,
    finished_at: null,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
    ...overrides,
  };
}

function readyFeatures() {
  return {
    feature_source: "visual_heuristic" as const,
    algorithm_version: "visual-analysis-1",
    visual_cta_candidates: [
      {
        kind: "visual_cta_candidate" as const,
        box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
        heuristic_score: 0.72,
        dominant_color: { r: 10, g: 10, b: 10 },
        regional_visual_contrast_estimate: 8.5,
        size_component: 0.5,
        position_component: 0.9,
        contrast_component: 0.6,
        evidence: ["edge_contour", "size_heuristic"],
        algorithm_version: "visual-analysis-1",
      },
    ],
    candidate_disclaimer:
      "Bu alanlar görsel özelliklere göre CTA adayı olarak önerilir. Gerçek kullanıcı tıklaması veya göz takibi verisi değildir.",
    synthetic_attention_estimate: {
      cells: [],
      feature_source: "visual_heuristic" as const,
      algorithm_version: "visual-analysis-1",
      disclaimer: "Sentetik dikkat tahmini: gerçek göz takibi/kullanıcı davranışı verisi değildir.",
    },
    limitations: ["Bu sonuçlar gerçek kullanıcı tıklaması veya göz takibi verisi değildir."],
  };
}

function renderSelector(overrides: Partial<React.ComponentProps<typeof ScreenshotCtaSelector>> = {}) {
  const onAnnotationChange = vi.fn();
  const utils = render(
    <ScreenshotCtaSelector
      slot="current"
      label="Tasarım A"
      draftId="draft-1"
      assetId="asset-1"
      annotation={null}
      onAnnotationChange={onAnnotationChange}
      {...overrides}
    />,
  );
  return { ...utils, onAnnotationChange };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("ScreenshotCtaSelector", () => {
  it("assetId yoksa hicbir sey render etmez ve API cagrisi yapmaz", () => {
    const { container } = renderSelector({ assetId: undefined });
    expect(container).toBeEmptyDOMElement();
    expect(mocks.createPageAnalysisForDesignAsset).not.toHaveBeenCalled();
  });

  it("mount olunca analiz baslatir ve 'hazırlanıyor' durumunu gosterir", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(baseAnalysis({ status: "queued" }));
    renderSelector();

    expect(await screen.findByText("Görsel analiz hazırlanıyor…")).toBeInTheDocument();
    await waitFor(() => expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledWith("asset-1"));
  });

  it("basarili (succeeded) analiz sonrasi CTA adaylarini ve disclaimer'i gosterir", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "succeeded", features: readyFeatures() }),
    );
    renderSelector();

    expect(await screen.findByText("CTA adayı 1")).toBeInTheDocument();
    expect(screen.getByText(/Gerçek kullanıcı tıklaması veya göz takibi verisi değildir/)).toBeInTheDocument();
    expect(screen.getByText(/gerçek göz takibi\/kullanıcı davranışı verisi değildir/)).toBeInTheDocument();
  });

  it("hicbir aday bulunamazsa bos sonuc mesaji gosterir (sahte aday uretilmez)", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({
        status: "succeeded",
        features: { ...readyFeatures(), visual_cta_candidates: [] },
      }),
    );
    renderSelector();

    expect(await screen.findByText("Bu görsel için otomatik bir CTA adayı bulunamadı.")).toBeInTheDocument();
  });

  it("analiz basarisiz (failed) olursa hata durumunu gosterir", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "failed", error: "Görsel analiz başarısız oldu" }),
    );
    renderSelector();

    expect(await screen.findByRole("alert")).toHaveTextContent(/başarısız/);
  });

  it("polling ile queued -> succeeded gecisini yakalar", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(baseAnalysis({ status: "queued" }));
    mocks.getPageAnalysis.mockResolvedValue(baseAnalysis({ status: "succeeded", features: readyFeatures() }));

    renderSelector();
    await waitFor(() => expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2100);
    });

    expect(await screen.findByText("CTA adayı 1")).toBeInTheDocument();
  });

  it("uzun sure 'running' durumunda kalirsa timeout gosterir", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(baseAnalysis({ status: "running" }));
    mocks.getPageAnalysis.mockResolvedValue(baseAnalysis({ status: "running" }));

    renderSelector();
    await waitFor(() => expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000 * 31);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/beklenenden uzun sürüyor/);
  });

  it("bir aday tiklandiginda patchWizardDraft cagrilir ve onAnnotationChange sunucu-cozumlenmis degerle tetiklenir", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "succeeded", features: readyFeatures() }),
    );
    const resolvedAnnotation: CtaAnnotation = {
      design_asset_id: "asset-1",
      box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
      selection_source: "candidate_confirmation",
      source_candidate_index: 0,
      verified_content_sha256: "b".repeat(64),
    };
    mocks.patchWizardDraft.mockResolvedValue({
      payload: { current_cta_annotation: resolvedAnnotation },
      warnings: [],
    });

    const user = userEvent.setup();
    const { onAnnotationChange } = renderSelector();

    const candidateButton = await screen.findByText("CTA adayı 1");
    await user.click(candidateButton);

    await waitFor(() =>
      expect(mocks.patchWizardDraft).toHaveBeenCalledWith("draft-1", {
        current_cta_annotation: {
          design_asset_id: "asset-1",
          box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
          selection_source: "candidate_confirmation",
          source_candidate_index: 0,
        },
      }),
    );
    expect(onAnnotationChange).toHaveBeenCalledWith(resolvedAnnotation);
  });

  it("bir aday klavye ile (Tab + Enter) secilebilir", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "succeeded", features: readyFeatures() }),
    );
    mocks.patchWizardDraft.mockResolvedValue({
      payload: {
        current_cta_annotation: {
          design_asset_id: "asset-1",
          box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
          selection_source: "candidate_confirmation",
          source_candidate_index: 0,
          verified_content_sha256: "c".repeat(64),
        },
      },
      warnings: [],
    });

    const user = userEvent.setup();
    renderSelector();

    await screen.findByText("CTA adayı 1");
    await user.tab();
    expect(screen.getByText("CTA adayı 1").closest("button")).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(mocks.patchWizardDraft).toHaveBeenCalledTimes(1));
  });

  it("manuel kutu modu acikca x/y/genislik/yukseklik alanlari sunar ve kaydeder", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "succeeded", features: readyFeatures() }),
    );
    mocks.patchWizardDraft.mockResolvedValue({
      payload: {
        current_cta_annotation: {
          design_asset_id: "asset-1",
          box: { x: 0.2, y: 0.3, w: 0.1, h: 0.05 },
          selection_source: "manual_box",
          verified_content_sha256: "d".repeat(64),
        },
      },
      warnings: [],
    });

    const user = userEvent.setup();
    renderSelector();
    await screen.findByText("CTA adayı 1");

    await user.click(screen.getByLabelText("CTA alanını kendim seçmek istiyorum"));
    expect(screen.getByLabelText("x")).toBeInTheDocument();
    expect(screen.getByLabelText("y")).toBeInTheDocument();
    expect(screen.getByLabelText("genişlik")).toBeInTheDocument();
    expect(screen.getByLabelText("yükseklik")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("x"));
    await user.type(screen.getByLabelText("x"), "0.2");
    await user.clear(screen.getByLabelText("y"));
    await user.type(screen.getByLabelText("y"), "0.3");
    await user.clear(screen.getByLabelText("genişlik"));
    await user.type(screen.getByLabelText("genişlik"), "0.1");
    await user.clear(screen.getByLabelText("yükseklik"));
    await user.type(screen.getByLabelText("yükseklik"), "0.05");

    await user.click(screen.getByRole("button", { name: "Manuel alanı kaydet" }));

    await waitFor(() =>
      expect(mocks.patchWizardDraft).toHaveBeenCalledWith("draft-1", {
        current_cta_annotation: {
          design_asset_id: "asset-1",
          box: { x: 0.2, y: 0.3, w: 0.1, h: 0.05 },
          selection_source: "manual_box",
        },
      }),
    );
  });

  it("secimi temizle patchWizardDraft'i null ile cagirir ve onAnnotationChange(null) tetikler", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "succeeded", features: readyFeatures() }),
    );
    mocks.patchWizardDraft.mockResolvedValue({ payload: { current_cta_annotation: null }, warnings: [] });

    const existingAnnotation: CtaAnnotation = {
      design_asset_id: "asset-1",
      box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
      selection_source: "manual_box",
      verified_content_sha256: "e".repeat(64),
    };
    const user = userEvent.setup();
    const { onAnnotationChange } = renderSelector({ annotation: existingAnnotation });

    await screen.findByText("CTA adayı 1");
    await user.click(screen.getByRole("button", { name: "Seçimi temizle" }));

    await waitFor(() =>
      expect(mocks.patchWizardDraft).toHaveBeenCalledWith("draft-1", { current_cta_annotation: null }),
    );
    expect(onAnnotationChange).toHaveBeenCalledWith(null);
  });

  it("buyuk alan uyarisi backend'den donunce kullaniciya gosterilir", async () => {
    mocks.createPageAnalysisForDesignAsset.mockResolvedValue(
      baseAnalysis({ status: "succeeded", features: readyFeatures() }),
    );
    mocks.patchWizardDraft.mockResolvedValue({
      payload: {
        current_cta_annotation: {
          design_asset_id: "asset-1",
          box: { x: 0, y: 0, w: 1, h: 1 },
          selection_source: "candidate_confirmation",
          source_candidate_index: 0,
          verified_content_sha256: "f".repeat(64),
        },
      },
      warnings: ["cta_annotation_covers_full_image"],
    });

    const user = userEvent.setup();
    renderSelector();
    const candidateButton = await screen.findByText("CTA adayı 1");
    await user.click(candidateButton);

    expect(
      await screen.findByText(/neredeyse tamamını kaplıyor/),
    ).toBeInTheDocument();
  });

  it("asset degisince eski secim yerel state'te de hemen temizlenir ve yeni asset icin analiz baslatilir", async () => {
    mocks.createPageAnalysisForDesignAsset
      .mockResolvedValueOnce(baseAnalysis({ status: "succeeded", features: readyFeatures(), design_asset_id: "asset-1" }))
      .mockResolvedValueOnce(
        baseAnalysis({ status: "succeeded", features: readyFeatures(), design_asset_id: "asset-2" }),
      );

    const existingAnnotation: CtaAnnotation = {
      design_asset_id: "asset-1",
      box: { x: 0.1, y: 0.1, w: 0.2, h: 0.08 },
      selection_source: "manual_box",
      verified_content_sha256: "g".repeat(64),
    };

    const { rerender, onAnnotationChange } = renderSelector({ annotation: existingAnnotation });
    await screen.findByText("CTA adayı 1");

    rerender(
      <ScreenshotCtaSelector
        slot="current"
        label="Tasarım A"
        draftId="draft-1"
        assetId="asset-2"
        annotation={existingAnnotation}
        onAnnotationChange={onAnnotationChange}
      />,
    );

    await waitFor(() => expect(onAnnotationChange).toHaveBeenCalledWith(null));
    await waitFor(() => expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledWith("asset-2"));
    expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledTimes(2);
  });

  it("Design A ve Design B durumlari birbirinden bagimsizdir (ayni render agacinda cakismaz)", async () => {
    mocks.createPageAnalysisForDesignAsset.mockImplementation((assetId: string) =>
      Promise.resolve(
        baseAnalysis({
          status: "succeeded",
          features: readyFeatures(),
          design_asset_id: assetId,
          id: `analysis-${assetId}`,
        }),
      ),
    );

    const onAnnotationChangeA = vi.fn();
    const onAnnotationChangeB = vi.fn();

    render(
      <>
        <ScreenshotCtaSelector
          slot="current"
          label="Tasarım A"
          draftId="draft-1"
          assetId="asset-a"
          annotation={null}
          onAnnotationChange={onAnnotationChangeA}
        />
        <ScreenshotCtaSelector
          slot="new"
          label="Tasarım B"
          draftId="draft-1"
          assetId="asset-b"
          annotation={null}
          onAnnotationChange={onAnnotationChangeB}
        />
      </>,
    );

    await waitFor(() => expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledWith("asset-a"));
    await waitFor(() => expect(mocks.createPageAnalysisForDesignAsset).toHaveBeenCalledWith("asset-b"));
    expect(screen.getAllByText("CTA adayı 1")).toHaveLength(2);
  });
});
