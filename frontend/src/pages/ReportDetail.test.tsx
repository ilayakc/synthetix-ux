import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    heatmap: { available: false, label: "Sentetik dikkat tahmini", grid: null, disclaimer: null },
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

describe("ReportDetail", () => {
  it("kalici bilgi kutusunu model, kalibrasyon, tarih ve seed ile gosterir", async () => {
    renderReportDetail(baseReport());

    await waitFor(() =>
      expect(screen.getByText("Gerçek kullanıcı verisi değildir")).toBeInTheDocument(),
    );
    expect(screen.getByText("heuristic-baseline-2026.1")).toBeInTheDocument();
    expect(screen.getByText("uncalibrated")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("belirsizlik araligini nokta tahmini ile birlikte gosterir", async () => {
    renderReportDetail(baseReport());

    await waitFor(() => expect(screen.getByText("Görev tamamlama olasılığı")).toBeInTheDocument());
    expect(screen.getByText("%62 (%54 – %70)")).toBeInTheDocument();

    // Belirsizlik noktasi klavye ile erisilebilir olmali (buton + aria-label).
    const point = screen.getByRole("button", {
      name: /Görev tamamlama olasılığı: Nokta tahmini %62/,
    });
    expect(point).toBeInTheDocument();
  });

  it("erisilebilir grafik ozetini ekran okuyucular icin gizli metin olarak icerir", async () => {
    renderReportDetail(baseReport());

    await waitFor(() =>
      expect(
        screen.getByText(
          "Görev tamamlama olasılığı: nokta tahmini %62, belirsizlik aralığı %54–%70.",
        ),
      ).toBeInTheDocument(),
    );
  });

  it("motor grid verisi uretmedigi zaman bos isi haritasi durumu gosterir", async () => {
    renderReportDetail(baseReport());

    await waitFor(() => expect(screen.getByText("Sentetik dikkat tahmini")).toBeInTheDocument());
    expect(screen.getByText(/Gösterilecek/, { exact: false })).toBeInTheDocument();
  });

  it("dikkat tahmini modulu secildiginde ısı haritasini ve acik uyariyi gosterir", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        grid: [{ key: "hero_baslik", label: "Hero / birincil başlık", score: 0.32 }],
        disclaimer:
          "GERCEK GOZ IZLEME (eye-tracking) verisi veya gercek kullanici davranisi DEGILDIR.",
      },
    });
    renderReportDetail(report);

    await waitFor(() => expect(screen.getByText("Hero / birincil başlık")).toBeInTheDocument());
    expect(
      screen.getByText(/GERCEK GOZ IZLEME \(eye-tracking\) verisi/, { exact: false }),
    ).toBeInTheDocument();
  });

  it("attention grid bos oldugunda bos isi haritasi durumu ve modul secim ipucu gosterir", async () => {
    const report = baseReport({
      heatmap: { available: true, label: "Sentetik dikkat tahmini", grid: [], disclaimer: null },
    });
    renderReportDetail(report);

    await waitFor(() => expect(screen.getByText("Sentetik dikkat tahmini")).toBeInTheDocument());
    expect(screen.getByText(/Gösterilecek/, { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/4\. adımda/, { exact: false })).toBeInTheDocument();
  });

  it("koordinat verisi yoksa erisilebilir tabloya duser ve nedenini belirtir", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        grid: [{ key: "hero_baslik", label: "Hero / birincil başlık", score: 0.32 }],
        disclaimer: "GERCEK GOZ IZLEME (eye-tracking) verisi DEGILDIR.",
        regions: null,
        screenshot_url: null,
        coordinates_available: false,
        coordinates_unavailable_reason: "Bu URL icin sayfa analizi (ekran goruntusu) bulunamadi.",
      },
    });
    renderReportDetail(report);

    await waitFor(() =>
      expect(
        screen.getByText("Bu URL icin sayfa analizi (ekran goruntusu) bulunamadi."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText("Hero / birincil başlık")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Görsel görünüm" })).not.toBeInTheDocument();
  });

  it("koordinat ve ekran goruntusu mevcutsa gorsel katmani, renk skalasini ve sekmeleri gosterir", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        grid: [
          { key: "hero_baslik", label: "Hero / birincil başlık", score: 0.32 },
          { key: "alt_bilgi", label: "Alt bilgi (footer)", score: 0.1 },
        ],
        disclaimer: "GERCEK GOZ IZLEME (eye-tracking) verisi DEGILDIR.",
        regions: [
          {
            key: "hero_baslik",
            label: "Hero / birincil başlık",
            score: 0.32,
            level: "high",
            box: { x_pct: 5, y_pct: 8, width_pct: 90, height_pct: 10 },
          },
          {
            key: "alt_bilgi",
            label: "Alt bilgi (footer)",
            score: 0.1,
            level: "low",
            box: { x_pct: 0, y_pct: 90, width_pct: 100, height_pct: 10 },
          },
        ],
        screenshot_url: "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
        coordinates_available: true,
        coordinates_unavailable_reason: null,
      },
    });
    renderReportDetail(report);

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Görsel görünüm" })).toBeInTheDocument(),
    );
    expect(screen.getByRole("tab", { name: "Erişilebilir tablo" })).toBeInTheDocument();

    // Bilimsel durustluk uyarisi, gorselin hemen uzerinde gorunur.
    expect(
      screen.getByText(/Bu görsel gerçek göz hareketlerinden/, { exact: false }),
    ).toBeInTheDocument();

    // Ekran goruntusu, harici bir URL degil, yetkilendirilmis backend uc noktasi
    // uzerinden yuklenir.
    const image = screen.getByAltText("Sayfanın önceden alınmış güvenli ekran görüntüsü");
    expect(image.getAttribute("src")).toContain(
      "/api/reports/11111111-1111-1111-1111-111111111111/heatmap-screenshot",
    );

    // Renk skalasi: renk tek basina anlam tasimaz, dusuk/orta/yuksek etiketleri
    // de gosterilir.
    expect(screen.getByText("Düşük sentetik dikkat payı")).toBeInTheDocument();
    expect(screen.getByText("Orta sentetik dikkat payı")).toBeInTheDocument();
    expect(screen.getByText("Yüksek sentetik dikkat payı")).toBeInTheDocument();

    // Bolgeler klavye ile erisilebilir (buton + erisilebilir ad + puan).
    const regionButton = screen.getByRole("button", {
      name: /Hero \/ birincil başlık: tahmini görsel yoğunluk %32 \(Yüksek\)/,
    });
    expect(regionButton).toBeInTheDocument();

    // Katmani goster/gizle kontrolu.
    const layerToggle = screen.getByLabelText("Yoğunluk katmanını göster") as HTMLInputElement;
    expect(layerToggle.checked).toBe(true);
    fireEvent.click(layerToggle);
    expect(layerToggle.checked).toBe(false);

    // Erisilebilir tablo, gorsel gorunumun altinda alternatif olarak korunur.
    expect(screen.getAllByText("Hero / birincil başlık").length).toBeGreaterThan(0);

    // "Erişilebilir tablo" sekmesine gecis calisir.
    fireEvent.click(screen.getByRole("tab", { name: "Erişilebilir tablo" }));
    expect(screen.getByRole("tabpanel")).toBeInTheDocument();
  });

  it("yasakli 'gercek goz takibi' iddialarini asla gostermez (isi haritasi gorsel gorunumu dahil)", async () => {
    const report = baseReport({
      heatmap: {
        available: true,
        label: "Sentetik dikkat tahmini",
        grid: [{ key: "hero_baslik", label: "Hero / birincil başlık", score: 0.32 }],
        disclaimer: "GERCEK GOZ IZLEME (eye-tracking) verisi DEGILDIR.",
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

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Görsel görünüm" })).toBeInTheDocument(),
    );

    const bodyText = (document.body.textContent ?? "").toLowerCase();
    for (const forbidden of [
      "kullanıcılar buraya baktı",
      "kullanıcıların gözü buraya gitti",
      "gerçek göz takibi",
      "kesin dikkat alanı",
      "kanıtlanmış davranış",
    ]) {
      expect(bodyText).not.toContain(forbidden);
    }
  });

  it("kampanya CTA modulu secildiginde CTA bolumunu gosterir", async () => {
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
          {
            key: "no_threshold_triggered",
            severity: "info",
            text: "Tanımlı eşik tabanlı bir mesaj netliği bulgusu tetiklenmedi.",
          },
        ],
        disclaimer: "Bu, sentetik bir tahmindir; gerçek tıklama verisi değildir.",
      },
    });
    renderReportDetail(report);

    await waitFor(() => expect(screen.getByText("Kampanya ve CTA analizi")).toBeInTheDocument());
    expect(
      screen.getByText("Bu, sentetik bir tahmindir; gerçek tıklama verisi değildir."),
    ).toBeInTheDocument();
    expect(screen.getByText("CTA 1 (ilk ekranda)")).toBeInTheDocument();
  });

  it("kampanya CTA modulu secilmemisse bolumu gostermez", async () => {
    renderReportDetail(baseReport());
    await waitFor(() => expect(screen.getByText("Kritik bulgular")).toBeInTheDocument());
    expect(screen.queryByText("Kampanya ve CTA analizi")).not.toBeInTheDocument();
  });

  it("ag ve cihaz modulu secildiginde profil tablosunu gosterir", async () => {
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
          {
            profile_key: "mobile_slow_3g",
            device_label: "Mobil",
            network_label: "Yavaş 3G",
            succeeded: false,
            error: "Sayfa zaman aşımına uğradı",
            timings: null,
            accessibility_violation_count: null,
          },
        ],
        error_rate: 0.5,
        disclaimer: "Bu, gerçek teknik ölçümdür; gerçek kullanıcı deneyimini temsil etmez.",
      },
    });
    renderReportDetail(report);

    await waitFor(() => expect(screen.getByText("Ağ ve cihaz testi")).toBeInTheDocument());
    expect(
      screen.getByText("Bu, gerçek teknik ölçümdür; gerçek kullanıcı deneyimini temsil etmez."),
    ).toBeInTheDocument();
    expect(screen.getByText("150 ms")).toBeInTheDocument();
    expect(screen.getByText(/Başarısız: Sayfa zaman aşımına uğradı/)).toBeInTheDocument();
  });

  it("yasakli iddia ifadeleri icermez", async () => {
    renderReportDetail(baseReport());

    await waitFor(() => expect(screen.getByText("Kritik bulgular")).toBeInTheDocument());

    const bodyText = document.body.textContent ?? "";
    for (const forbidden of ["kazandı", "kanıtlandı", "gerçek dönüşüm", "göz takibi"]) {
      expect(bodyText.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("A/B karsilastirmasi varsa iki seri icin legend ve degerleri gosterir", async () => {
    const report = baseReport({
      ab_comparison: {
        comparisons: {
          task_completion_probability: { variant_a: 0.6, variant_b: 0.7, delta: 0.1 },
        },
        sampled_synthetic_persona_count: { variant_a: 500, variant_b: 500 },
        calibration_status: "uncalibrated",
        note: "Bu bir simülasyon farkıdır; istatistiksel anlamlılık iddia edilmez.",
        this_variant_role: "variant_a",
        sibling_variant_name: "Yeni Tasarım",
      },
    });
    renderReportDetail(report);

    await waitFor(() => expect(screen.getByText("A/B metrik karşılaştırması")).toBeInTheDocument());
    expect(screen.getByText("Bu rapor (A)")).toBeInTheDocument();
    expect(screen.getByText("Yeni Tasarım (B)")).toBeInTheDocument();
  });

  it("A/B karsilastirmasi yoksa bolumu gostermez", async () => {
    renderReportDetail(baseReport());
    await waitFor(() => expect(screen.getByText("Kritik bulgular")).toBeInTheDocument());
    expect(screen.queryByText("A/B metrik karşılaştırması")).not.toBeInTheDocument();
  });

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
              {
                text: "Görev tamamlama olasılığı %62 nokta tahminine dayanır.",
                metric_ids: ["task_completion_probability"],
              },
            ],
            possible_explanations: [
              {
                text: "Sayfa düzeni görevi tamamlamayı zorlaştırıyor olabilir.",
                metric_ids: ["task_completion_probability"],
              },
            ],
            suggested_verification_experiment: "Gerçek kullanıcılarla A/B testi yapılabilir.",
            limitations:
              "Bu açıklama sentetik verilere dayanır; gerçek kullanıcı davranışını yansıtmaz.",
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

    const generateButton = await screen.findByRole("button", { name: "AI destekli açıklama üret" });
    fireEvent.click(generateButton);

    await waitFor(() =>
      expect(
        screen.getByText("Görev tamamlama olasılığı orta seviyede; belirsizlik aralığı geniştir."),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText(
        "Bu açıklama sentetik verilere dayanır; gerçek kullanıcı davranışını yansıtmaz.",
      ),
    ).toBeInTheDocument();
  });

  it("AI destekli aciklama uretilemedigi zaman hata mesaji gosterir", async () => {
    const report = baseReport();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url.includes("/ai-explanation") && init?.method === "POST") {
          return jsonResponse(500, { detail: "Sunucu hatası" });
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

    const generateButton = await screen.findByRole("button", { name: "AI destekli açıklama üret" });
    fireEvent.click(generateButton);

    await waitFor(() =>
      expect(
        screen.getByText("AI destekli açıklama üretilemedi. Lütfen tekrar deneyin."),
      ).toBeInTheDocument(),
    );
  });
});
