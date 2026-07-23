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
    sibling_heatmap: { available: false, label: "Sentetik dikkat tahmini", grid: null, disclaimer: null },
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
    expect(screen.getByRole("tab", { name: "Görsel Karşılaştırma" })).toHaveAttribute(
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
        screen.getByRole("heading", { name: "Sentetik simulasyon sonucu" }),
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
    expect(screen.getByText("uncalibrated")).toBeInTheDocument();
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

    await waitFor(() => expect(screen.getByText("Öncelikli bulgular")).toBeInTheDocument());
    expect(screen.getByText("Kontrastı güçlendirin")).toBeInTheDocument();

    const panel = screen.getByRole("tabpanel");
    const headings = within(panel).getAllByRole("heading").map((h) => h.textContent);
    const resultIndex = headings.findIndex((t) => t?.includes("simülasyon özeti"));
    const findingsIndex = headings.findIndex((t) => t === "Öncelikli bulgular");
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

    await waitFor(() => expect(screen.getByText("Öncelikli bulgular")).toBeInTheDocument());
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
    await waitFor(() => expect(screen.getByRole("button", { name: /Dışa aktar/ })).toBeInTheDocument());

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

    const visualTab = screen.getByRole("tab", { name: "Görsel Karşılaştırma" });
    expect(visualTab).toHaveAttribute("aria-selected", "true");
    expect(document.activeElement).toBe(visualTab);
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
    goToTab("Görsel Karşılaştırma");

    expect(screen.getByText("Hero / birincil başlık")).toBeInTheDocument();
    expect(
      screen.getByText(/Renkli katmanlar algoritmanın tahmini görsel belirginlik dağılımını gösterir/),
    ).toBeInTheDocument();
  });

  it("A/B gorselleri yan yana (iki panel) gosterilir, bagimsiz katman toggle'lariyla", async () => {
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
    goToTab("Görsel Karşılaştırma");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /Tasarım A/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole("heading", { name: /Tasarım B/ })).toBeInTheDocument();

    const toggles = screen.getAllByLabelText(
      "Sentetik dikkat katmanını göster",
    ) as HTMLInputElement[];
    expect(toggles).toHaveLength(2);
    expect(toggles[0].checked).toBe(true);
    expect(toggles[1].checked).toBe(true);
    fireEvent.click(toggles[0]);
    expect(toggles[0].checked).toBe(false);
    expect(toggles[1].checked).toBe(true);
  });

  it("bagimsiz CTA katman toggle'i calisir", async () => {
    const report = baseReport({
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
    goToTab("Görsel Karşılaştırma");

    const ctaToggle = screen.getByLabelText("CTA adaylarını göster") as HTMLInputElement;
    expect(ctaToggle.checked).toBe(true);
    fireEvent.click(ctaToggle);
    expect(ctaToggle.checked).toBe(false);
  });

  it("ilk 3 CTA adayi + kullanicinin onayladigi CTA varsayilan gorunur, digerleri 'Tum adaylari goster' ile acilir", async () => {
    const boxes = [
      { classification: "user_confirmed_cta", label: "Kayıt ol butonu", x: 0.05, y: 0.05, w: 0.1, h: 0.04, heuristic_score: null },
      { classification: "visual_cta_candidate", label: "Aday 1", x: 0.3, y: 0.3, w: 0.1, h: 0.04, heuristic_score: 0.9 },
      { classification: "visual_cta_candidate", label: "Aday 2", x: 0.5, y: 0.3, w: 0.1, h: 0.04, heuristic_score: 0.8 },
      { classification: "visual_cta_candidate", label: "Aday 3", x: 0.7, y: 0.3, w: 0.1, h: 0.04, heuristic_score: 0.7 },
      { classification: "visual_cta_candidate", label: "Aday 4", x: 0.3, y: 0.6, w: 0.1, h: 0.04, heuristic_score: 0.6 },
      { classification: "visual_cta_candidate", label: "Aday 5", x: 0.5, y: 0.6, w: 0.1, h: 0.04, heuristic_score: 0.5 },
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
    goToTab("Görsel Karşılaştırma");

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
    goToTab("Görsel Karşılaştırma");

    fireEvent.click(screen.getByRole("tab", { name: "Erişilebilir tablo" }));
    const rows = screen.getAllByRole("row");
    // 1 baslik satiri + en fazla 10 (5 yuksek + 5 dusuk) veri satiri.
    expect(rows.length).toBeLessThanOrEqual(11);

    const expandButton = screen.getByRole("button", { name: /Tüm 48 hücreyi göster/ });
    fireEvent.click(expandButton);
    expect(screen.getAllByRole("row").length).toBe(49);
  });
});

describe("ReportDetail — Bulgular ve Oneriler sekmesi", () => {
  it("bulgulari oncelik sirasina gore (Yuksek/Dusuk) gruplar ve 4 alani gosterir", async () => {
    const report = baseReport({
      critical_findings: [
        { key: "contrast_below_threshold", severity: "warning", text: "Kontrast düşük tespit edildi." },
        { key: "no_threshold_triggered", severity: "info", text: "Bilgi." },
      ],
    });
    renderReportDetail(report);
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Bulgular ve Öneriler");

    expect(screen.getByText("Yüksek öncelik")).toBeInTheDocument();
    expect(screen.getByText(/Ne bulundu\?/)).toBeInTheDocument();
    expect(screen.getByText(/Neden önemli\?/)).toBeInTheDocument();
    expect(screen.getByText(/Ne yapılmalı\?/)).toBeInTheDocument();
    expect(screen.getByText(/İlgili tasarım:/)).toBeInTheDocument();
  });

  it("AI destekli aciklama butonu 'Bulgulari sade dille acikla' metniyle burada yer alir", async () => {
    renderReportDetail(baseReport());
    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Bulgular ve Öneriler");

    expect(screen.getByRole("button", { name: "Bulguları sade dille açıkla" })).toBeInTheDocument();
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
    goToTab("Görsel Karşılaştırma");

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
            click_probability: { distribution: "triangular", point_estimate: 0.3, low: 0.24, mode: 0.3, high: 0.36 },
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

  it("AI destekli aciklama uretildiginde kisa ozet ve sinirlamalari gosterir", async () => {
    const report = baseReport();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/ai-explanation") && init?.method === "POST") {
          return jsonResponse(200, {
            schema_version: "1.0",
            calibration_status: "uncalibrated",
            short_summary: "Görev tamamlama olasılığı orta seviyede; belirsizlik aralığı geniştir.",
            metric_basis: [
              { text: "Görev tamamlama olasılığı %62 nokta tahminine dayanır.", metric_ids: ["task_completion_probability"] },
            ],
            possible_explanations: [
              { text: "Sayfa düzeni görevi tamamlamayı zorlaştırıyor olabilir.", metric_ids: ["task_completion_probability"] },
            ],
            suggested_verification_experiment: "Gerçek kullanıcılarla A/B testi yapılabilir.",
            limitations: "Bu açıklama sentetik verilere dayanır; gerçek kullanıcı davranışını yansıtmaz.",
            prompt_version: "v1",
            provider: "template",
            model_name: null,
            generated_at: "2026-07-16T00:00:00Z",
          });
        }
        if (url.includes("/api/reports/")) return jsonResponse(200, report);
        throw new Error(`Beklenmeyen istek: ${String(init?.method)} ${url}`);
      }),
    );

    render(
      <MemoryRouter initialEntries={["/raporlar/11111111-1111-1111-1111-111111111111"]}>
        <Routes>
          <Route path="/raporlar/:reportId" element={<ReportDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByRole("tab", { name: "Özet" })).toBeInTheDocument());
    goToTab("Bulgular ve Öneriler");

    const generateButton = await screen.findByRole("button", { name: "Bulguları sade dille açıkla" });
    fireEvent.click(generateButton);

    await waitFor(() =>
      expect(
        screen.getByText("Görev tamamlama olasılığı orta seviyede; belirsizlik aralığı geniştir."),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText("Bu açıklama sentetik verilere dayanır; gerçek kullanıcı davranışını yansıtmaz."),
    ).toBeInTheDocument();
  });
});
