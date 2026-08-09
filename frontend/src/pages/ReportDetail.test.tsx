import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ReportDetail from "./ReportDetail";

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

function baseReport(overrides: Record<string, unknown> = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    title: "Sentetik simulasyon sonucu",
    created_at: "2026-07-01T10:00:00Z",
    project_id: "33333333-3333-3333-3333-333333333333",
    project_name: "Anasayfa Yenileme",
    test_definition_id: "44444444-4444-4444-4444-444444444444",
    test_definition_name: "Anasayfa testi",
    variant_name: "Ana Senaryo",
    variant_role: "primary",
    info_box: {
      not_real_user_data_label: "Gerçek kullanıcı verisi değildir",
      model_version: "heuristic-baseline-2026.1",
      calibration_status: "uncalibrated",
      generated_at: "2026-07-01T10:00:00Z",
      deterministic_seed: 42,
      rules_version: "rules-2026.1",
      fixture_version: "fixtures-2026.1",
      input_summary: {
        url: "https://example.com/anasayfa",
        source_type: "url",
        wizard_test_type: "existing_site_basic_ux",
        persona_count: 500,
        target_audience: "Yeni B2B müşteri adayları",
      },
    },
    metrics: {
      task_completion_probability: {
        distribution: "triangular",
        point_estimate: 0.62,
        low: 0.54,
        mode: 0.62,
        high: 0.7,
      },
      task_duration_seconds: {
        unit: "seconds",
        distribution: "triangular",
        point_estimate: 45,
        low: 30,
        mode: 45,
        high: 60,
        p10: 34,
        p50: 45,
        p90: 56,
      },
      misclick_probability: {
        distribution: "triangular",
        point_estimate: 0.1,
        low: 0.05,
        mode: 0.1,
        high: 0.15,
      },
      abandonment_probability: {
        distribution: "triangular",
        point_estimate: 0.2,
        low: 0.12,
        mode: 0.2,
        high: 0.28,
      },
      readability_score: 72.5,
      contrast_check: { pass: true, avg_ratio: 5.2, min_ratio: 4.8, threshold: 4.5 },
      regional_interest: [],
    },
    disclaimer: "Bu sonuçlar sentetik senaryo tahminidir; gerçek kullanıcı verisi değildir.",
    methodology_reference: "docs/methodology.md",
    ab_comparison: null,
    persona_segments: [],
    persona_segment_note:
      "Küçük sentetik örneklemli segmentlerde (n<30) belirsizlik daha yüksektir; bu sayılar gerçek kullanıcı örneklemi değildir.",
    critical_findings: [
      {
        key: "no_threshold_triggered",
        severity: "info",
        text: "Tanımlı eşik tabanlı kritik bulgu tetiklenmedi.",
      },
    ],
    heatmap: {
      available: false,
      label: "Sentetik dikkat tahmini",
      overlay_kind: "semantic_region",
      feature_source: "dom",
      grid: null,
      disclaimer: null,
    },
    cta_overlay: {
      available: false,
      feature_source: null,
      boxes: [],
      screenshot_url: null,
      coordinates_available: false,
      coordinates_unavailable_reason: null,
      disclaimer: "CTA adayları gerçek kullanıcı tıklaması veya göz takibi verisi değildir.",
    },
    campaign_cta: null,
    network_device: null,
    accessible_chart_summaries: [
      {
        chart_key: "task_completion_probability",
        text: "Görev tamamlama olasılığı: nokta tahmini %62, belirsizlik aralığı %54–%70.",
      },
    ],
    export_json_url: "/api/reports/11111111-1111-1111-1111-111111111111/export.json",
    export_csv_url: "/api/reports/11111111-1111-1111-1111-111111111111/export.csv",
    ...overrides,
  };
}

function renderReportDetail(report: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/reports/")) return jsonResponse(200, report);
      throw new Error(`Beklenmeyen istek: ${url}`);
    }),
  );

  render(
    <MemoryRouter initialEntries={["/raporlar/11111111-1111-1111-1111-111111111111"]}>
      <Routes>
        <Route path="/raporlar/:reportId" element={<ReportDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

function goToTab(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

function baseAbComparison(overrides: Record<string, unknown> = {}) {
  return {
    comparisons: {
      task_completion_probability: { variant_a: 0.6, variant_b: 0.6, delta: 0 },
      misclick_probability: { variant_a: 0.1, variant_b: 0.1, delta: 0 },
      abandonment_probability: { variant_a: 0.2, variant_b: 0.2, delta: 0 },
      task_duration_seconds: { variant_a: 45, variant_b: 45, delta: 0 },
      readability_score: { variant_a: 70, variant_b: 70, delta: 0 },
    },
    sampled_synthetic_persona_count: { variant_a: 500, variant_b: 500 },
    calibration_status: "uncalibrated",
    note: "Bu bir simülasyon farkıdır; istatistiksel anlamlılık iddia edilmez.",
    this_variant_role: "variant_a",
    sibling_variant_name: "Yeni Tasarım",
    sibling_report_id: "22222222-2222-2222-2222-222222222222",
    this_source_type: "url",
    sibling_source_type: "url",
    sibling_heatmap: {
      available: false,
      label: "Sentetik dikkat tahmini",
      grid: null,
      disclaimer: null,
    },
    sibling_cta_overlay: {
      available: false,
      feature_source: null,
      boxes: [],
      screenshot_url: null,
      coordinates_available: false,
      coordinates_unavailable_reason: null,
      disclaimer: "test",
    },
    same_snapshot_sha256: false,
    ...overrides,
  };
}

describe("ReportDetail — genel yapi ve Ozet sekmesi", () => {
  it("Ozet sekmesi varsayilan olarak acik gelir", async () => {
    renderReportDetail(baseReport());

    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    expect(screen.getByRole("tab", { name: "Özet" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Isı Haritası" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("A/B karsilastirmasi varsa baslik 'A/B Tasarım Karşılaştırması' olur", async () => {
    renderReportDetail(baseReport({ ab_comparison: baseAbComparison() }));

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "A/B Tasarım Karşılaştırması" }),
      ).toBeInTheDocument(),
    );
  });

  it("A/B karsilastirmasi yoksa eski tekil/legacy rapor basligi ile acilir", async () => {
    renderReportDetail(baseReport());

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Sentetik simülasyon sonucu" }),
      ).toBeInTheDocument(),
    );
  });

  it("teknik bilgiler Ozet ekraninda gorunmez", async () => {
    renderReportDetail(baseReport());

    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    expect(screen.queryByText("heuristic-baseline-2026.1")).not.toBeInTheDocument();
    expect(screen.queryByText("Model sürümü")).not.toBeInTheDocument();
  });

  it("teknik bilgiler Teknik Detaylar sekmesinde mevcuttur", async () => {
    renderReportDetail(baseReport());

    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Teknik Detaylar");

    expect(screen.getByText("heuristic-baseline-2026.1")).toBeInTheDocument();
    expect(screen.getByText("Kalibre edilmemiş")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("esit A/B sonuclarinda kesin kazanan uretilmez, esitlik acikca soylenir", async () => {
    renderReportDetail(baseReport({ ab_comparison: baseAbComparison() }));

    await waitFor(() =>
      expect(
        screen.getByText("İki tasarım arasında belirgin metrik farkı görülmedi"),
      ).toBeInTheDocument(),
    );
    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toMatch(/kazandı/i);
    expect(bodyText).not.toMatch(/kanıtlandı/i);
  });

  it("farkli A/B sonuclarinda tarafsiz sayisal fark gosterilir, kazanan dili kullanilmaz", async () => {
    renderReportDetail(
      baseReport({
        ab_comparison: baseAbComparison({
          comparisons: {
            task_completion_probability: { variant_a: 0.6, variant_b: 0.7, delta: 0.1 },
            misclick_probability: { variant_a: 0.1, variant_b: 0.1, delta: 0 },
            abandonment_probability: { variant_a: 0.2, variant_b: 0.2, delta: 0 },
            task_duration_seconds: { variant_a: 45, variant_b: 45, delta: 0 },
            readability_score: { variant_a: 70, variant_b: 70, delta: 0 },
          },
        }),
      }),
    );

    await waitFor(() =>
      expect(
        screen.getByText("Tasarımlar arasında sentetik metrik farkları var"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/A %60 — B %70/)).toBeInTheDocument();
    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toMatch(/kazandı/i);
    expect(bodyText).not.toMatch(/kanıtlandı/i);
  });

  it("kritik bulgular Ozetin ust kisminda (Sonuc kartinin hemen altinda) gorunur", async () => {
    const report = baseReport({
      critical_findings: [
        { key: "contrast_below_threshold", severity: "warning", text: "Kontrast düşük." },
      ],
    });
    renderReportDetail(report);

    await waitFor(() =>
      expect(screen.getByText("Kritik bulgular ve öneriler")).toBeInTheDocument(),
    );
    expect(screen.getByText("Kontrastı güçlendirin")).toBeInTheDocument();

    const panel = screen.getByRole("tabpanel");
    const headings = within(panel)
      .getAllByRole("heading")
      .map((h) => h.textContent);
    const resultIndex = headings.findIndex((t) => t?.includes("simülasyon özeti"));
    const findingsIndex = headings.findIndex((t) => t === "Kritik bulgular ve öneriler");
    const metricsIndex = headings.findIndex((t) => t === "Metrik özeti");
    expect(resultIndex).toBeLessThan(findingsIndex);
    expect(findingsIndex).toBeLessThan(metricsIndex);
  });

  it("en fazla 3 oncelikli bulgu karti gosterir", async () => {
    const report = baseReport({
      critical_findings: [
        { key: "contrast_below_threshold", severity: "warning", text: "Kontrast düşük." },
        { key: "low_task_completion", severity: "warning", text: "Tamamlama düşük." },
        { key: "high_abandonment", severity: "warning", text: "Terk yüksek." },
        { key: "high_misclick", severity: "warning", text: "Yanlış tıklama yüksek." },
      ],
    });
    renderReportDetail(report);

    await waitFor(() =>
      expect(screen.getByText("Kritik bulgular ve öneriler")).toBeInTheDocument(),
    );
    const panel = screen.getByRole("tabpanel");
    expect(panel.querySelectorAll(".priority-finding-card")).toHaveLength(3);
  });

  it("performans metrikleri Ozet sekmesinde yalnizca bir kez gorunur (tekrar etmez)", async () => {
    renderReportDetail(baseReport());
    await waitFor(() => expect(screen.getByText("Metrik özeti")).toBeInTheDocument());
    // Ayni metrik degeri (%62) hem cubuk hem paragrafta TEKRAR EDILMEMELI.
    expect(screen.getAllByText(/%62/).length).toBe(1);
  });

  it("JSON/CSV disa aktarma tek bir kucuk menu icinde sunulur", async () => {
    renderReportDetail(baseReport());
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Dışa aktar/ })).toBeInTheDocument(),
    );

    expect(screen.queryByText("JSON olarak dışa aktar")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Dışa aktar/ }));
    expect(screen.getByRole("menuitem", { name: "JSON olarak dışa aktar" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "CSV olarak dışa aktar" })).toBeInTheDocument();
  });
});

describe("ReportDetail — ust duzey sekme gezinmesi (klavye)", () => {
  it("ok tuslariyla sekmeler arasinda gezinilebilir", async () => {
    renderReportDetail(baseReport());
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());

    const summaryTab = screen.getByRole("tab", { name: "Özet" });
    summaryTab.focus();
    fireEvent.keyDown(summaryTab, { key: "ArrowRight" });

    const visualTab = screen.getByRole("tab", { name: "Isı Haritası" });
    expect(visualTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(visualTab);
  });
});

describe("ReportDetail — AI Raporu sekmesi", () => {
  const RUN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";

  function runningPipeline() {
    return {
      pipeline_id: "pp",
      simulation_run_id: RUN_ID,
      status: "running",
      created_at: "2026-08-01T10:00:00Z",
      started_at: "2026-08-01T10:00:01Z",
      finished_at: null,
      cancel_requested: false,
      expected_stage_count: 8,
      completed_stage_count: 2,
      succeeded_stage_count: 2,
      running_stage_count: 1,
      queued_stage_count: 5,
      failed_stage_count: 0,
      progress_percent: 25,
      report_available: false,
      stages: [
        {
          stage_type: "evidence_preparation",
          status: "succeeded",
          batch_index: null,
          attempt_count: 1,
          error_code: null,
          created_at: "2026-08-01T10:00:00Z",
          started_at: "2026-08-01T10:00:01Z",
          finished_at: "2026-08-01T10:00:02Z",
        },
      ],
    };
  }

  function renderWithAi(aiPipeline: () => ReturnType<typeof jsonResponse>) {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes(`/api/simulations/runs/${RUN_ID}/ai-pipeline`)) return aiPipeline();
      if (url.includes("/api/reports/"))
        return jsonResponse(200, baseReport({ simulation_run_id: RUN_ID }));
      throw new Error(`Beklenmeyen istek: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/raporlar/11111111-1111-1111-1111-111111111111"]}>
        <Routes>
          <Route path="/raporlar/:reportId" element={<ReportDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    return fetchMock;
  }

  it("rapor cevabindaki simulation_run_id ile AI pipeline durumu sondalanir", async () => {
    const fetchMock = renderWithAi(() => jsonResponse(200, runningPipeline()));

    await waitFor(() => expect(screen.getByRole("tab", { name: "AI Raporu" })).toBeInTheDocument());
    // Sonda tam olarak bu run'in ai-pipeline endpoint'ine yapildi.
    expect(
      fetchMock.mock.calls.some(([url]) =>
        String(url).includes(`/api/simulations/runs/${RUN_ID}/ai-pipeline`),
      ),
    ).toBe(true);
  });

  it("pipeline mevcutsa (RUNNING) AI Raporu sekmesi gorunur; secilince ilerleme ve asamalar gosterilir", async () => {
    renderWithAi(() => jsonResponse(200, runningPipeline()));

    const tab = await screen.findByRole("tab", { name: "AI Raporu" });
    fireEvent.click(tab);

    expect(screen.getByText(/AI raporu hazırlanıyor/)).toBeInTheDocument();
    expect(screen.getByText(/%25/)).toBeInTheDocument();
    expect(screen.getByText(/Kanıt hazırlığı/)).toBeInTheDocument();
  });

  it("pipeline yoksa (kontrollu 404) AI Raporu sekmesi GOSTERILMEZ, normal rapor bozulmaz", async () => {
    renderWithAi(() => jsonResponse(404, { detail: "ai_pipeline_not_found" }));

    // Standart sekmeler yuklendi.
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    // AI Raporu sekmesi yok.
    await waitFor(() =>
      expect(screen.queryByRole("tab", { name: "AI Raporu" })).not.toBeInTheDocument(),
    );
    // Yalnizca 4 ust duzey sekme.
    expect(screen.getAllByRole("tab")).toHaveLength(4);
  });

  it("klavye ok tuslariyla yeni AI Raporu sekmesine gecilebilir", async () => {
    renderWithAi(() => jsonResponse(200, runningPipeline()));

    await waitFor(() => expect(screen.getByRole("tab", { name: "AI Raporu" })).toBeInTheDocument());
    const technicalTab = screen.getByRole("tab", { name: "Teknik Detaylar" });
    technicalTab.focus();
    fireEvent.keyDown(technicalTab, { key: "ArrowRight" });

    const aiTab = screen.getByRole("tab", { name: "AI Raporu" });
    expect(aiTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(aiTab);
  });
});

describe("ReportDetail — Gorsel Karsilastirma sekmesi", () => {
  it("yalin tek-tasarim gorunumunde panel gosterir", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        overlay_kind: "semantic_region",
        feature_source: "dom",
        grid: [{ key: "hero_baslik", label: "Hero / birincil başlık", score: 0.32 }],
        disclaimer: "test",
        regions: null,
        screenshot_url: null,
        coordinates_available: false,
        coordinates_unavailable_reason: "Bu URL icin sayfa analizi bulunamadi.",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    expect(screen.getByText("Hero / birincil başlık")).toBeInTheDocument();
    expect(screen.getByText(/yeşil düşük, sarı artan, kırmızı/i)).toBeInTheDocument();
  });

  it("A/B gorselleri yan yana gosterilir ve isi katmani gereksiz bir anahtarla kapatilmaz", async () => {
    const visualHeatmap = {
      available: true,
      label: "Sentetik dikkat tahmini",
      overlay_kind: "synthetic_visual_attention",
      feature_source: "visual_heuristic",
      grid: null,
      visual_cells: [{ x: 0, y: 0, w: 1, h: 1, intensity: 0.5 }],
      disclaimer: "test",
      screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
      coordinates_available: true,
      coordinates_unavailable_reason: null,
    };
    const report = baseReport({
      heatmap: visualHeatmap,
      ab_comparison: baseAbComparison({
        this_source_type: "screenshot",
        sibling_source_type: "screenshot",
        sibling_heatmap: {
          ...visualHeatmap,
          screenshot_url: "/api/reports/22222222-2222-2222-2222-222222222222/heatmap-screenshot",
        },
      }),
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Tasarım A/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole("heading", { name: /Tasarım B/ })).toBeInTheDocument();

    expect(screen.getAllByTitle("Sayfanın düşük yoğunluklu temel alanı")).toHaveLength(2);
    expect(screen.queryByLabelText("Sentetik göz odağını göster")).not.toBeInTheDocument();
  });

  it("bagimsiz CTA katman toggle'i calisir", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        overlay_kind: "synthetic_visual_attention",
        feature_source: "visual_heuristic",
        grid: null,
        visual_cells: [
          { x: 0.1, y: 0.15, w: 0.25, h: 0.12, intensity: 0.78 },
          { x: 0.4, y: 0.2, w: 0.2, h: 0.06, intensity: 0.58 },
        ],
        disclaimer: "test",
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
      },
      cta_overlay: {
        available: true,
        feature_source: "visual_heuristic",
        boxes: [
          {
            classification: "visual_cta_candidate",
            label: "Görsel CTA adayı",
            x: 0.4,
            y: 0.2,
            w: 0.2,
            h: 0.06,
            heuristic_score: 0.72,
          },
        ],
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
        disclaimer: "test",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    const ctaToggle = screen.getByLabelText(
      "CTA bölgelerini göster (buton ve bağlantı adayları)",
    ) as HTMLInputElement;
    expect(screen.getByRole("button", { name: "Tıklama ısı haritası" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Görsel odak haritası" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Birincil etkileşim alanı: beklenen tıklama payı/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Ekran görüntüsündeki görsel etkileşim adayları"),
    ).toBeInTheDocument();
    expect(screen.getByText(/DOM ve gerçek olay verisi yok/i)).toBeInTheDocument();
    expect(ctaToggle.checked).toBe(false);
    fireEvent.click(ctaToggle);
    expect(ctaToggle.checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /odak haritas/i }));
    expect(document.querySelector(".heatmap-visual-cell")).toBeInTheDocument();
  });

  it("sentetik tiklama yogunlugu ve goz odagi gorunumlerini ayri sunar", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        overlay_kind: "semantic_region",
        feature_source: "dom",
        grid: [{ key: "hero_baslik", label: "Hero", score: 0.3 }],
        regions: [
          {
            key: "hero_baslik",
            label: "Hero",
            score: 0.3,
            level: "high",
            box: { x_pct: 10, y_pct: 10, width_pct: 50, height_pct: 20 },
          },
        ],
        click_grid: [{ key: "birincil_cta", label: "Birincil CTA", score: 0.5 }],
        click_regions: [
          {
            key: "birincil_cta",
            label: "Birincil CTA",
            score: 0.5,
            level: "high",
            box: { x_pct: 20, y_pct: 35, width_pct: 25, height_pct: 10 },
          },
        ],
        disclaimer: "test",
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
      },
      cta_overlay: {
        available: true,
        feature_source: "dom",
        boxes: [
          {
            classification: "dom_interactive_candidate",
            label: "DOM etkileşimli aday",
            x: 0.58,
            y: 0.08,
            w: 0.09,
            h: 0.014,
            heuristic_score: null,
          },
          {
            classification: "dom_interactive_candidate",
            label: "DOM etkileşimli aday",
            x: 0.2,
            y: 0.01,
            w: 0.03,
            h: 0.006,
            heuristic_score: null,
          },
        ],
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
        disclaimer: "test",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    expect(screen.getByRole("button", { name: "Tıklama ısı haritası" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Görsel odak haritası" })).toBeInTheDocument();
    expect(screen.getByText(/buton, bağlantı ve etkileşimli alanlarda/i)).toBeInTheDocument();
    expect(screen.getByText("Sayfanın DOM yapısındaki buton ve bağlantılar")).toBeInTheDocument();
    expect(screen.getByText(/olay verisi yok/i)).toBeInTheDocument();
    expect(screen.getByText("DOM tabanlı")).toBeInTheDocument();
    const muteToggle = screen.getByLabelText(
      "Vurgular için arka planı soluklaştır",
    ) as HTMLInputElement;
    expect(muteToggle.checked).toBe(true);
    const screenshot = screen.getByRole("img", { name: /analiz anında alınmış/i });
    expect(screenshot).toHaveClass("heatmap-screenshot--muted");
    fireEvent.click(muteToggle);
    expect(screenshot).not.toHaveClass("heatmap-screenshot--muted");
    expect(
      screen.getByRole("button", { name: /Birincil etkileşim alanı: beklenen tıklama payı/i }),
    ).toHaveClass("heatmap-region--click");
    expect(
      screen.queryByRole("button", { name: /Hero: beklenen tıklama payı/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/olay verisi yok/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Görsel odak haritası" }));
    expect(screen.getByText(/renk, kontrast, boyut ve yerleşimin/i)).toBeInTheDocument();
    expect(screen.getByText(/gerçek göz takibi verisiyle kalibre edilmedi/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hero: görsel odak payı/i })).toHaveClass(
      "heatmap-region--gaze",
    );
    expect(screen.queryByText("Sentetik tıklama yoğunluğunu göster")).not.toBeInTheDocument();
    expect(screen.getByText(/gerçek göz takibi verisiyle kalibre edilmedi/i)).toBeInTheDocument();
  });

  it("DOM etkileşim koordinatı yoksa semantik bolgeleri tiklama noktasi gibi gostermez", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        overlay_kind: "semantic_region",
        feature_source: "dom",
        grid: [{ key: "hero_baslik", label: "Hero", score: 0.4 }],
        regions: [
          {
            key: "hero_baslik",
            label: "Hero",
            score: 0.4,
            level: "high",
            box: { x_pct: 10, y_pct: 10, width_pct: 50, height_pct: 20 },
          },
        ],
        click_grid: [{ key: "birincil_cta", label: "Birincil CTA", score: 0.6 }],
        click_regions: [
          {
            key: "birincil_cta",
            label: "Birincil CTA",
            score: 0.6,
            level: "high",
            box: { x_pct: 20, y_pct: 35, width_pct: 25, height_pct: 10 },
          },
        ],
        disclaimer: "test",
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
      },
      cta_overlay: {
        available: false,
        feature_source: "dom",
        boxes: [],
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
        disclaimer: "test",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    expect(screen.queryByRole("button", { name: "Tıklama ısı haritası" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hero: görsel odak payı/i })).toBeInTheDocument();
  });

  it("genis kapsayici linki tiklama noktasi saymaz ve form eylemini one cikarir", async () => {
    const report = baseReport({
      cta_overlay: {
        available: true,
        feature_source: "dom",
        boxes: [
          {
            classification: "dom_interactive_candidate",
            label: "DOM etkileÅŸimli aday",
            interaction_kind: "container_link",
            x: 0.12,
            y: 0.02,
            w: 0.72,
            h: 0.014,
          },
          {
            classification: "dom_interactive_candidate",
            label: "DOM etkileÅŸimli aday",
            interaction_kind: "form_action",
            x: 0.7,
            y: 0.2,
            w: 0.12,
            h: 0.06,
          },
          {
            classification: "dom_interactive_candidate",
            label: "DOM etkileÅŸimli aday",
            interaction_kind: "pagination_control",
            x: 0.2,
            y: 0.3,
            w: 0.02,
            h: 0.05,
          },
        ],
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
        disclaimer: "test",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: /zet$/i })).toBeInTheDocument());
    fireEvent.click(screen.getByRole("tab", { name: /Haritas/i }));

    const clickMarkers = document.querySelectorAll(".heatmap-region--click");
    expect(clickMarkers).toHaveLength(2);
    expect(clickMarkers[0]).toHaveStyle({ left: "66%" });
    expect((clickMarkers[0] as HTMLElement).style.background).toContain("220, 38, 38");
    expect((clickMarkers[1] as HTMLElement).style.background).toContain("34, 197, 94");
  });

  it("ilk 3 CTA adayi + kullanicinin onayladigi CTA varsayilan gorunur, digerleri 'Tum adaylari goster' ile acilir", async () => {
    const boxes = [
      {
        classification: "user_confirmed_cta",
        label: "Kayıt ol butonu",
        x: 0.05,
        y: 0.05,
        w: 0.1,
        h: 0.04,
        heuristic_score: null,
      },
      {
        classification: "visual_cta_candidate",
        label: "Aday 1",
        x: 0.3,
        y: 0.3,
        w: 0.1,
        h: 0.04,
        heuristic_score: 0.9,
      },
      {
        classification: "visual_cta_candidate",
        label: "Aday 2",
        x: 0.5,
        y: 0.3,
        w: 0.1,
        h: 0.04,
        heuristic_score: 0.8,
      },
      {
        classification: "visual_cta_candidate",
        label: "Aday 3",
        x: 0.7,
        y: 0.3,
        w: 0.1,
        h: 0.04,
        heuristic_score: 0.7,
      },
      {
        classification: "visual_cta_candidate",
        label: "Aday 4",
        x: 0.3,
        y: 0.6,
        w: 0.1,
        h: 0.04,
        heuristic_score: 0.6,
      },
      {
        classification: "visual_cta_candidate",
        label: "Aday 5",
        x: 0.5,
        y: 0.6,
        w: 0.1,
        h: 0.04,
        heuristic_score: 0.5,
      },
    ];
    const report = baseReport({
      cta_overlay: {
        available: true,
        feature_source: "visual_heuristic",
        boxes,
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
        disclaimer: "test",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    fireEvent.click(screen.getByLabelText("CTA bölgelerini göster (buton ve bağlantı adayları)"));

    expect(screen.getByRole("button", { name: /Sizin CTA'nız/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /CTA adayı 1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /CTA adayı 3/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /CTA adayı 4/ })).not.toBeInTheDocument();

    const showAllButton = screen.getByRole("button", { name: /Tüm adayları göster \(6\)/ });
    fireEvent.click(showAllButton);
    expect(screen.getByRole("button", { name: /CTA adayı 5/ })).toBeInTheDocument();
  });

  it("48 satirlik heatmap grid tablosu Gorsel Karsilastirma'da varsayilan olarak tam gorunmez; en yuksek/dusuk 5 gosterilir", async () => {
    const cells = Array.from({ length: 48 }, (_, i) => ({
      x: (i % 8) * 0.125,
      y: Math.floor(i / 8) * 0.2,
      w: 0.125,
      h: 0.2,
      intensity: i / 48,
    }));
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        overlay_kind: "synthetic_visual_attention",
        feature_source: "visual_heuristic",
        grid: null,
        visual_cells: cells,
        disclaimer: "test",
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    fireEvent.click(screen.getByRole("tab", { name: "Yoğunluk tablosu" }));
    const rows = screen.getAllByRole("row");
    // 1 baslik satiri + en fazla 10 (5 yuksek + 5 dusuk) veri satiri.
    expect(rows.length).toBeLessThanOrEqual(11);

    const expandButton = screen.getByRole("button", { name: /Tüm 48 hücreyi göster/ });
    fireEvent.click(expandButton);
    expect(screen.getAllByRole("row").length).toBe(49);
  });
});

describe("ReportDetail — Bulgular ve Oneriler sekmesi", () => {
  it("bulguyu ve onerilen adimi tek, anlasilir kartta gosterir", async () => {
    const report = baseReport({
      critical_findings: [
        {
          key: "contrast_below_threshold",
          severity: "warning",
          text: "Kontrast düşük tespit edildi.",
        },
        { key: "no_threshold_triggered", severity: "info", text: "Bilgi." },
      ],
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Bulgular ve Öneriler");

    expect(screen.getByText("Ne bulduk, ne yapmalısınız?")).toBeInTheDocument();
    expect(screen.getByText("Öncelikli")).toBeInTheDocument();
    expect(screen.getByText(/Önerilen adım:/)).toBeInTheDocument();
    expect(screen.queryByText(/Neden önemli\?/)).not.toBeInTheDocument();
  });
});

describe("ReportDetail — screenshot kontrast terminolojisi", () => {
  it("screenshot kaynaginda kesin WCAG gecti/kaldi dili KULLANILMAZ", async () => {
    const report = baseReport({
      info_box: {
        ...baseReport().info_box,
        input_summary: { ...baseReport().info_box.input_summary, source_type: "screenshot" },
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Teknik Detaylar");

    expect(screen.getByText("Bölgesel görsel kontrast tahmini")).toBeInTheDocument();
    expect(screen.queryByText("Kontrast kontrolü (WCAG AA)")).not.toBeInTheDocument();
    const bodyText = document.body.textContent ?? "";
    expect(bodyText).not.toMatch(/WCAG AA\)\s*: Geçti/);
    expect(bodyText).toMatch(/kesin WCAG uygunluk testi değildir/);
  });

  it("DOM/URL kaynaginda dogrulanmis WCAG gecti/kaldi dili korunur", async () => {
    renderReportDetail(baseReport());
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Teknik Detaylar");

    expect(screen.getByText("Kontrast kontrolü (WCAG AA)")).toBeInTheDocument();
    expect(screen.getByText("Geçti")).toBeInTheDocument();
  });
});

describe("ReportDetail — yasakli iddialar", () => {
  it("yasakli 'gercek goz takibi' / kesinlik iddialarini asla gostermez", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        overlay_kind: "semantic_region",
        feature_source: "dom",
        grid: [{ key: "hero_baslik", label: "Hero / birincil başlık", score: 0.32 }],
        disclaimer: "test",
        regions: [
          {
            key: "hero_baslik",
            label: "Hero / birincil başlık",
            score: 0.32,
            level: "high",
            box: { x_pct: 5, y_pct: 8, width_pct: 90, height_pct: 10 },
          },
        ],
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Isı Haritası");

    const bodyText = (document.body.textContent ?? "").toLowerCase();
    // Not: "gerçek göz takibi ... değildir" gibi NEGATE EDILMIS ifadeler
    // (bkz. HEATMAP_VISUAL_DISCLAIMER) bilerek disinda tutulur - bunlar
    // yasakli bir IDDIA degil, tam tersine bunun REDDIDIR.
    for (const forbidden of [
      "kullanıcılar buraya baktı",
      "kullanıcıların gözü buraya gitti",
      "kesin dikkat alanı",
      "kanıtlanmış davranış",
      "kazandı",
      "kanıtlandı",
      "gerçek dönüşüm",
    ]) {
      expect(bodyText).not.toContain(forbidden);
    }
    expect(bodyText).toMatch(/gerçek göz takibi.*değildir/);
  });
});

describe("ReportDetail — diger modul bolumleri (Teknik Detaylar altinda)", () => {
  it("kampanya CTA modulu Teknik Detaylar'da gorunur", async () => {
    const report = baseReport({
      campaign_cta: {
        ctas: [
          {
            key: "cta_1",
            label: "CTA 1",
            rank: 1,
            above_fold: true,
            click_probability: {
              distribution: "triangular",
              point_estimate: 0.3,
              low: 0.24,
              mode: 0.3,
              high: 0.36,
            },
          },
        ],
        message_clarity_findings: [
          { key: "no_threshold_triggered", severity: "info", text: "Bulgu yok." },
        ],
        disclaimer: "Bu, sentetik bir tahmindir; gerçek tıklama verisi değildir.",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    expect(screen.queryByText("Kampanya ve CTA analizi")).not.toBeInTheDocument();

    goToTab("Teknik Detaylar");
    expect(screen.getByText("Kampanya ve CTA analizi")).toBeInTheDocument();
  });

  it("ag ve cihaz modulu Teknik Detaylar'da gorunur", async () => {
    const report = baseReport({
      network_device: {
        profiles: [
          {
            profile_key: "desktop_broadband",
            device_label: "Masaüstü",
            network_label: "Geniş bant",
            succeeded: true,
            error: null,
            timings: { dom_content_loaded_ms: 100, load_event_ms: 150, total_navigation_ms: 150 },
            accessibility_violation_count: 0,
          },
        ],
        error_rate: 0.0,
        disclaimer: "Bu, gerçek teknik ölçümdür; gerçek kullanıcı deneyimini temsil etmez.",
      },
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Teknik Detaylar");
    expect(screen.getByText("Ağ ve cihaz testi")).toBeInTheDocument();
    expect(screen.getByText("150 ms")).toBeInTheDocument();
  });
});

describe("ReportDetail — hata durumlari", () => {
  it("rapor alinamadiginda hata mesaji gosterir", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports/")) return jsonResponse(500, { detail: "Sunucu hatası" });
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/raporlar/11111111-1111-1111-1111-111111111111"]}>
        <Routes>
          <Route path="/raporlar/:reportId" element={<ReportDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Rapor yüklenemedi.")).toBeInTheDocument());
  });

  it("rapor yukleme hatasindan sonra tekrar deneyebilir", async () => {
    let attempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/reports/")) {
          attempts += 1;
          return attempts === 1 ? jsonResponse(500, {}) : jsonResponse(200, baseReport());
        }
        if (url.includes("/ai-pipeline")) return jsonResponse(404, {});
        throw new Error(`Beklenmeyen istek: ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/raporlar/11111111-1111-1111-1111-111111111111"]}>
        <Routes>
          <Route path="/raporlar/:reportId" element={<ReportDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Tekrar dene" }));
    expect(await screen.findByRole("tab", { name: "Özet" })).toBeInTheDocument();
    expect(screen.queryByText("Rapor yüklenemedi.")).not.toBeInTheDocument();
  });
});
