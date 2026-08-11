import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import type { AIPipelineStatusResponse, AIReportResponse } from "../api/client";
import { ApiError } from "../api/client";
import AiReportTab from "./AiReportTab";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getAiPipelineStatus: vi.fn(),
    getAiReport: vi.fn(),
  };
});

import { getAiPipelineStatus, getAiReport } from "../api/client";

const runningStatus: AIPipelineStatusResponse = {
  pipeline_id: "p1",
  simulation_run_id: "r1",
  status: "running",
  created_at: "2026-08-01T10:00:00Z",
  started_at: "2026-08-01T10:00:01Z",
  finished_at: null,
  cancel_requested: false,
  expected_stage_count: 8,
  completed_stage_count: 3,
  succeeded_stage_count: 3,
  running_stage_count: 1,
  queued_stage_count: 4,
  failed_stage_count: 0,
  progress_percent: 38,
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
    {
      stage_type: "persona_behavior",
      status: "running",
      batch_index: 0,
      attempt_count: 1,
      error_code: null,
      created_at: "2026-08-01T10:00:03Z",
      started_at: "2026-08-01T10:00:04Z",
      finished_at: null,
    },
  ],
};

const succeededStatus: AIPipelineStatusResponse = {
  ...runningStatus,
  status: "succeeded",
  progress_percent: 100,
  report_available: true,
  running_stage_count: 0,
  queued_stage_count: 0,
  succeeded_stage_count: 8,
  completed_stage_count: 8,
  finished_at: "2026-08-01T10:05:00Z",
};

const reportResponse: AIReportResponse = {
  pipeline_id: "p1",
  simulation_run_id: "r1",
  generated_at: "2026-08-06T12:00:00Z",
  content_format: "structured_json",
  synthetic_disclaimer: "Sunucu sabiti: bu rapor gerçek kullanıcı araştırması değildir.",
  provider: "openai",
  model_name: "gpt-5.6-terra",
  instruction_version: "v3",
  report: {
    report_version: "ux-report-v1",
    summary: "1 sentetik bulgu üretildi.",
    findings: [
      {
        finding_id: "finding:hyp:1",
        priority: "medium",
        finding: "Olası bir sürtünme noktası (sentetik tahmin, doğrulanmamış).",
        source_stage: "aggregation",
        evidence_references: ["metric:abandonment_probability"],
        affected_persona_groups: [],
        estimated_affected_users: 220,
        recommendation: "Bu sürtünme noktasını gerçek kullanıcı testiyle doğrulayın.",
        confidence: 0.3895,
      },
    ],
    limitations: "Bu rapor Mock/şablon tarafından üretilmiştir.",
    disclaimer: "Kalibre edilmemiş sentetik senaryo tahminlerine dayanır.",
  },
};

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("AiReportTab", () => {
  it("RUNNING durumunda hazırlanıyor + ilerleme + aşama listesi gösterir, rapor çağırmaz", () => {
    render(<AiReportTab runId="r1" initialStatus={runningStatus} />);

    expect(screen.getByText(/AI raporu hazırlanıyor/)).toBeInTheDocument();
    expect(screen.getByText(/%38/)).toBeInTheDocument();
    // Kanonik aşamalar Türkçe etiketle görünür.
    expect(screen.getByText(/Kanıt hazırlığı/)).toBeInTheDocument();
    expect(screen.getByText(/Persona davranışı/)).toBeInTheDocument();
    expect(getAiReport).not.toHaveBeenCalled();
  });

  it("SUCCEEDED + report_available: raporu çağırır ve tüm bulgu alanlarını render eder", async () => {
    vi.mocked(getAiReport).mockResolvedValue(reportResponse);

    render(<AiReportTab runId="r1" initialStatus={succeededStatus} />);

    await waitFor(() => expect(getAiReport).toHaveBeenCalledWith("r1"));

    expect(await screen.findByText("1 sentetik bulgu üretildi.")).toBeInTheDocument();
    expect(screen.getByText(/Olası bir sürtünme noktası/)).toBeInTheDocument();
    // Öncelik metinle gösterilir (yalnızca renk değil).
    expect(screen.getByText("Orta öncelik")).toBeInTheDocument();
    expect(screen.getByText(/Güven düzeyi: Düşük/)).toBeInTheDocument();
    expect(
      screen.getByText(/Bu sürtünme noktasını gerçek kullanıcı testiyle doğrulayın/),
    ).toBeInTheDocument();
    expect(screen.getByText(/220/)).toBeInTheDocument();
    expect(screen.queryByText(/metric:abandonment_probability/)).not.toBeInTheDocument();
    expect(screen.getByText(/ux-report-v1/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-5.6-terra/)).toBeInTheDocument();
    expect(screen.getByText(/AI sağlayıcısı: openai/)).toBeInTheDocument();
    // Sınırlamalar görünür; yinelenen rapor-içi uyarı gösterilmez. Genel
    // sentetik-veri uyarısı uygulama kabuğundaki IntegrityBanner'dadır.
    expect(screen.getByText("Bu rapor Mock/şablon tarafından üretilmiştir.")).toBeInTheDocument();
    expect(
      screen.queryByText("Kalibre edilmemiş sentetik senaryo tahminlerine dayanır."),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/gerçek kullanıcı araştırması değildir/)).not.toBeInTheDocument();
  });

  it("eski mock kaydındaki ASCII Türkçe metinleri düzeltir ve yinelenen uyarıyı kaldırır", async () => {
    vi.mocked(getAiReport).mockResolvedValue({
      ...reportResponse,
      report: {
        ...reportResponse.report,
        summary:
          "1 sentetik bulgu uretildi. Bu rapor Mock/sablon tabanlidir ve gercek kullanici olcumu degildir.",
        findings: [
          {
            ...reportResponse.report.findings[0],
            finding:
              "Kanit ile iliskili olasi bir surtunme noktasi (sentetik tahmin, dogrulanmamis).",
            recommendation: "Bu surtunme noktasini gercek kullanici testiyle dogrulamayi dusunun.",
          },
        ],
        limitations:
          "Bu rapor Mock/sablon tarafindan uretilmistir; kalibre edilmemis sentetik tahminlere dayanir ve gercek bir olcum degildir.",
      },
    });

    render(<AiReportTab runId="r1" initialStatus={succeededStatus} />);

    expect(await screen.findByText("1 sentetik bulgu üretildi.")).toBeInTheDocument();
    expect(screen.getByText(/Kanıt ile ilişkili olası bir sürtünme noktası/)).toBeInTheDocument();
    expect(
      screen.getByText(/Bu sürtünme noktasını gerçek kullanıcı testiyle doğrulamayı düşünün/),
    ).toBeInTheDocument();
    expect(screen.getByText(/bağlamsal çeşitlilik/)).toBeInTheDocument();
    expect(screen.queryByText(/gercek kullanici olcumu degildir/)).not.toBeInTheDocument();
  });

  it("FAILED/PARTIAL/CANCELLED terminal durumları açık mesajla gösterir, rapor çağırmaz", () => {
    render(
      <AiReportTab
        runId="r1"
        initialStatus={{ ...succeededStatus, status: "failed", report_available: false }}
      />,
    );
    expect(screen.getByText(/işlem hattı başarısız oldu/)).toBeInTheDocument();
    expect(getAiReport).not.toHaveBeenCalled();
  });

  it("409 report_not_ready: hata değil, hazırlık durumu olarak gösterilir", async () => {
    vi.mocked(getAiReport).mockRejectedValue(new ApiError(409, "ai_report_not_ready", null));

    render(<AiReportTab runId="r1" initialStatus={succeededStatus} />);

    expect(await screen.findByText(/AI raporu hazırlanıyor/)).toBeInTheDocument();
    // Sert hata mesajı GÖSTERİLMEZ.
    expect(screen.queryByText(/getirilemedi/)).not.toBeInTheDocument();
  });

  it("gerçek API hatası (500) güvenli mesaj + tekrar dene gösterir", async () => {
    vi.mocked(getAiReport).mockRejectedValue(new ApiError(500, "ai_report_integrity_error", null));

    render(<AiReportTab runId="r1" initialStatus={succeededStatus} />);

    expect(await screen.findByText(/AI raporu getirilemedi/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
    // Ham backend kodu ekrana basılmaz.
    expect(screen.queryByText(/integrity/)).not.toBeInTheDocument();
  });

  it("ilk sonda hatası (initialError) güvenli mesaj + tekrar dene gösterir", () => {
    render(<AiReportTab runId="r1" initialStatus={null} initialError />);
    expect(screen.getByText(/AI raporu durumu şu anda alınamadı/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Tekrar dene" })).toBeInTheDocument();
  });

  it("polling terminal duruma ulaşınca durur (yinelenen istek yok)", async () => {
    vi.useFakeTimers();
    vi.mocked(getAiPipelineStatus).mockResolvedValue(succeededStatus);
    vi.mocked(getAiReport).mockResolvedValue(reportResponse);

    render(<AiReportTab runId="r1" initialStatus={runningStatus} />);

    // İlk 5 sn: bir poll -> succeeded (terminal).
    await vi.advanceTimersByTimeAsync(5000);
    expect(getAiPipelineStatus).toHaveBeenCalledTimes(1);

    // Terminal olduktan sonra daha fazla poll YAPILMAZ.
    await vi.advanceTimersByTimeAsync(15000);
    expect(getAiPipelineStatus).toHaveBeenCalledTimes(1);
  });

  it("unmount sonrası polling temizlenir (timer sızmaz)", async () => {
    vi.useFakeTimers();
    vi.mocked(getAiPipelineStatus).mockResolvedValue(runningStatus);

    const { unmount } = render(<AiReportTab runId="r1" initialStatus={runningStatus} />);
    unmount();

    await vi.advanceTimersByTimeAsync(15000);
    expect(getAiPipelineStatus).not.toHaveBeenCalled();
  });
});
