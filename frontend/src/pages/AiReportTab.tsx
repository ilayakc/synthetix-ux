import { useEffect, useState } from "react";
import {
  ApiError,
  type AIPipelineStatusResponse,
  type AIReportFinding,
  type AIReportResponse,
  getAiPipelineStatus,
  getAiReport,
} from "../api/client";

// Bu sekme, tamamlanmis `ai_report` pipeline'inin NIHAI ciktisini gosterir ve
// standart raporu sadelestiren "AI destekli aciklama" ozelliginden (bkz.
// ReportDetail.AiExplanationSection) TAMAMEN AYRIDIR. Kanonik kaynak backend'in
// GET /ai-report endpoint'idir; cikti burada kod icine gomulmez, cevaptan
// render edilir.

const POLL_INTERVAL_MS = 5000;

// AIPipelineStatus terminal durumlari (bkz. backend app.models.ai_pipeline).
const TERMINAL_STATUSES = new Set(["succeeded", "failed", "partial", "cancelled"]);

const STAGE_TYPE_LABELS: Record<string, string> = {
  evidence_preparation: "Kanıt hazırlığı",
  persona_batch_preparation: "Persona grupları hazırlığı",
  scenario_interpretation: "Senaryo yorumlama",
  persona_behavior: "Persona davranışı",
  aggregation: "Toplulaştırma",
  ux_report: "UX raporu",
};

const STAGE_STATUS_LABELS: Record<string, string> = {
  queued: "Sırada",
  running: "Çalışıyor",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
  skipped: "Atlandı",
};

const PRIORITY_LABELS: Record<string, string> = {
  high: "Yüksek öncelik",
  medium: "Orta öncelik",
  low: "Düşük öncelik",
};

// Renk YALNIZCA ikincil bir ipucudur; oncelik her zaman metinle (PRIORITY_LABELS)
// de gosterilir (bkz. erisilebilirlik gereksinimi "yalnizca renkle anlatma").
const PRIORITY_COLORS: Record<string, string> = {
  high: "220, 38, 38",
  medium: "217, 119, 6",
  low: "37, 99, 235",
};

const TERMINAL_FAILURE_MESSAGES: Record<string, string> = {
  failed: "AI raporu oluşturulamadı; işlem hattı başarısız oldu. Nihai rapor üretilmedi.",
  partial: "AI raporu yalnızca kısmen tamamlandı; doğrulanmış nihai rapor üretilmedi.",
  cancelled: "AI raporu iptal edildi; nihai rapor üretilmedi.",
};

const GENERIC_ERROR =
  "AI raporu durumu şu anda alınamadı. Bu geçici bir sorun olabilir; lütfen tekrar deneyin.";

function confidenceLabel(confidence: number): string {
  if (confidence < 0.45) return "Düşük — gerçek kullanıcı testiyle doğrulayın";
  if (confidence < 0.75) return "Orta — küçük bir A/B deneyiyle doğrulayın";
  return "Yüksek — yine de sentetik bir tahmin";
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleString("tr-TR");
}

/**
 * İlk mock raporlar Türkçe karakterler olmadan kaydedildi. Bu kayıtlar
 * değiştirilemediği için yalnızca bilinen eski mock ifadelerini sunum
 * katmanında düzeltiriz. Yeni/provider bağımsız metinlere müdahale edilmez.
 */
function polishLegacyMockCopy(value: string): string {
  const withoutRepeatedWarning = value
    .replace(/ Bu rapor Mock\/sablon tabanlidir ve gercek kullanici olcumu degildir\.?$/, "")
    .replace(
      "Bu rapor Mock/sablon tarafindan uretilmistir; kalibre edilmemis sentetik tahminlere dayanir ve gercek bir olcum degildir.",
      "Mock/şablon sağlayıcı deterministik çıktılar üretir; bağlamsal çeşitlilik ve genelleme kapasitesi sınırlıdır.",
    );

  const replacements: Array<[RegExp, string]> = [
    [/\bKanit\b/g, "Kanıt"],
    [/\biliskili\b/g, "ilişkili"],
    [/\bolasi\b/g, "olası"],
    [/\bsurtunme\b/g, "sürtünme"],
    [/\bdogrulanmamis\b/g, "doğrulanmamış"],
    [/\boraninda\b/g, "oranında"],
    [/\bgorunuyor\b/g, "görünüyor"],
    [/\bnoktasi\b/g, "noktası"],
    [/\bnoktasini\b/g, "noktasını"],
    [/\bgercek\b/g, "gerçek"],
    [/\bkullanici\b/g, "kullanıcı"],
    [/\bdogrulamayi\b/g, "doğrulamayı"],
    [/\bdusunun\b/g, "düşünün"],
    [/\buretildi\b/g, "üretildi"],
    [/\buretilmistir\b/g, "üretilmiştir"],
    [/\bMock\/sablon\b/g, "Mock/şablon"],
  ];

  return replacements.reduce(
    (text, [pattern, replacement]) => text.replace(pattern, replacement),
    withoutRepeatedWarning,
  );
}

interface AiReportTabProps {
  runId: string;
  isAbComparison?: boolean;
  /** Ust bilesenin (ReportDetail) sekme gorunurlugu icin yaptigi ilk durum
   * sondasinin sonucu - cift istek yapmamak icin buradan devralinir. */
  initialStatus: AIPipelineStatusResponse | null;
  /** Ilk sonda kontrollu 404 DISINDA (gercek ag/sunucu hatasi) basarisiz
   * olduysa `true` - sekme guvenli bir hata + "Tekrar dene" ile acilir. */
  initialError?: boolean;
  /** AI modulu secilmis olmasina ragmen ilk sonda 404 donduyse sekmeyi
   * gizlemek yerine acik bir hazirlik mesaji ve yeniden deneme sunulur. */
  initialMissing?: boolean;
}

type ReportFetchState = "idle" | "loading" | "not_ready" | "error";

export default function AiReportTab({
  runId,
  isAbComparison = false,
  initialStatus,
  initialError,
  initialMissing = false,
}: AiReportTabProps) {
  const [status, setStatus] = useState<AIPipelineStatusResponse | null>(initialStatus);
  const [statusError, setStatusError] = useState<boolean>(Boolean(initialError));
  const [report, setReport] = useState<AIReportResponse | null>(null);
  const [reportState, setReportState] = useState<ReportFetchState>("idle");
  const [pipelineMissing, setPipelineMissing] = useState(initialMissing);

  const statusKey = status?.status ?? null;
  const reportAvailable = status?.report_available ?? false;

  // --- Kontrollu polling: yalnizca non-terminal durumda; unmount'ta durur;
  // terminal sonuca ulasinca YENIDEN PLANLAMAYI durdurur; self-rescheduling
  // setTimeout oldugu icin ayni anda yinelenen istek YAPISAL olarak olusmaz
  // (bir sonraki istek yalnizca oncekisi bittikten sonra planlanir). ---
  useEffect(() => {
    if (statusError) return; // sert hata -> otomatik poll yok, kullanici "Tekrar dene" ile tetikler
    if (statusKey !== null && TERMINAL_STATUSES.has(statusKey)) return; // terminal -> poll yok

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const scheduleNext = () => {
      if (cancelled) return;
      timer = setTimeout(runPoll, POLL_INTERVAL_MS);
    };

    const runPoll = async () => {
      if (cancelled) return;
      try {
        const next = await getAiPipelineStatus(runId);
        if (cancelled) return;
        setStatus(next);
        setStatusError(false);
        // Yalnizca HALA non-terminal ise tekrar planla; terminal sonuc bir
        // sonraki poll'u tetiklemez (fazladan istek yok).
        if (!TERMINAL_STATUSES.has(next.status)) scheduleNext();
      } catch {
        // Bu noktada 404 beklenmez (sekme yalnizca pipeline mevcut/hataliyken
        // acilir); gercek hata guvenli bir mesaja donusur (ham detay sizmaz) ve
        // otomatik yeniden planlama YAPILMAZ - kullanici "Tekrar dene" ile tetikler.
        if (!cancelled) setStatusError(true);
      }
    };

    // Ilk yukleme / "Tekrar dene" sonrasi (status yok) hemen dene; aksi halde
    // ilk poll'u normal aralikla zamanla.
    if (status === null) void runPoll();
    else scheduleNext();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // Bagimlilik olarak status OBJESI degil YALNIZCA status STRING'i: ilerleme
    // sayaci degistikce efekt gereksiz yere sifirlanmaz, ama running->succeeded
    // gecisinde yeniden calisip (terminal) poll'u durdurur.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, statusKey, statusError]);

  // --- Pipeline SUCCEEDED + rapor mevcutsa nihai raporu getir. ---
  useEffect(() => {
    if (statusKey !== "succeeded" || !reportAvailable) return;
    let cancelled = false;
    setReportState("loading");
    getAiReport(runId)
      .then((r) => {
        if (!cancelled) {
          setReport(r);
          setReportState("idle");
        }
      })
      .catch((err) => {
        if (cancelled) return;
        // 409 (ai_report_not_ready): hata degil, hazirlik durumu olarak ele alinir.
        if (err instanceof ApiError && err.status === 409) {
          setReportState("not_ready");
        } else {
          setReportState("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runId, statusKey, reportAvailable]);

  const retryStatus = () => {
    setPipelineMissing(false);
    setStatus(null);
    setStatusError(false);
    setReport(null);
    setReportState("idle");
  };

  const retryReport = () => {
    // Durumu yeniden sondalayarak (ve report state'ini sifirlayarak) tekrar dene.
    setReport(null);
    setReportState("idle");
    setStatus(null);
    setStatusError(false);
  };

  return (
    <section className="report-section" aria-labelledby="ai-report-tab-heading">
      <h2 id="ai-report-tab-heading" className="report-section__heading">
        AI Raporu
      </h2>
      <p className="report-section__intro">
        Öncelikli riskleri, dayandıkları kanıtları ve uygulanabilir iyileştirme adımlarını gösterir.
      </p>

      {isAbComparison && (
        <div className="ai-ab-guidance">
          <strong>A/B testi nasıl okunmalı?</strong>
          <p>
            Varyant A aynı ürünün orijinal arayüzü, Varyant B ise yalnızca ölçmek istediğiniz
            değişikliği içeren alternatif olmalıdır. Örneğin buton rengi, CTA metni veya yerleşim
            değişir; hedef görev ve persona grubu aynı kalır. Böylece farkın hangi tasarım
            değişikliğinden kaynaklandığını daha güvenli yorumlayabilirsiniz.
          </p>
        </div>
      )}

      {pipelineMissing ? (
        <div role="status">
          <p className="auth-error">
            AI raporu seçildi ancak işlem hattı henüz oluşturulmadı. Birkaç saniye sonra yeniden
            deneyin.
          </p>
          <button type="button" className="btn-secondary" onClick={retryStatus}>
            Durumu yeniden kontrol et
          </button>
        </div>
      ) : (
        <AiReportBody
          status={status}
          statusError={statusError}
          report={report}
          reportState={reportState}
          onRetryStatus={retryStatus}
          onRetryReport={retryReport}
        />
      )}
    </section>
  );
}

function AiReportBody({
  status,
  statusError,
  report,
  reportState,
  onRetryStatus,
  onRetryReport,
}: {
  status: AIPipelineStatusResponse | null;
  statusError: boolean;
  report: AIReportResponse | null;
  reportState: ReportFetchState;
  onRetryStatus: () => void;
  onRetryReport: () => void;
}) {
  // 1) Durum hic alinamadi (sert hata) -> guvenli mesaj + tekrar dene.
  if (statusError && status === null) {
    return (
      <div role="status">
        <p className="auth-error">{GENERIC_ERROR}</p>
        <button type="button" className="btn-secondary" onClick={onRetryStatus}>
          Tekrar dene
        </button>
      </div>
    );
  }

  // Henuz ilk durum yuklenmediyse.
  if (status === null) {
    return <p className="page-placeholder">AI raporu durumu yükleniyor…</p>;
  }

  const isTerminal = TERMINAL_STATUSES.has(status.status);

  // 2) Terminal ama basarisiz (failed/partial/cancelled).
  if (isTerminal && status.status !== "succeeded") {
    return (
      <div>
        <p className="auth-error" role="status">
          {TERMINAL_FAILURE_MESSAGES[status.status] ??
            "AI raporu tamamlanmadı; nihai rapor üretilmedi."}
        </p>
        <PipelineStageList status={status} />
      </div>
    );
  }

  // 3) SUCCEEDED: raporu getirme durumuna gore.
  if (status.status === "succeeded") {
    if (reportState === "loading") {
      return (
        <p className="page-placeholder" role="status" aria-live="polite">
          AI raporu yükleniyor…
        </p>
      );
    }
    if (reportState === "not_ready") {
      // 409 report_not_ready: hata degil, hazirlik olarak sunulur.
      return <PreparingState status={status} onRefresh={onRetryReport} />;
    }
    if (reportState === "error") {
      return (
        <div role="status">
          <p className="auth-error">
            AI raporu getirilemedi. Bu geçici bir sorun olabilir; lütfen tekrar deneyin.
          </p>
          <button type="button" className="btn-secondary" onClick={onRetryReport}>
            Tekrar dene
          </button>
        </div>
      );
    }
    if (report) {
      return <AiReportContent report={report} />;
    }
    // SUCCEEDED ama report_available=false gibi beklenmedik ara durum.
    return <PreparingState status={status} onRefresh={onRetryReport} />;
  }

  // 4) QUEUED / RUNNING -> hazirlaniyor.
  return <PreparingState status={status} onRefresh={undefined} />;
}

function PreparingState({
  status,
  onRefresh,
}: {
  status: AIPipelineStatusResponse;
  onRefresh?: () => void;
}) {
  return (
    <div role="status" aria-live="polite">
      <p className="report-section__intro">AI raporu hazırlanıyor… (%{status.progress_percent})</p>
      <progress
        className="ai-report-progress"
        value={status.progress_percent}
        max={100}
        aria-label={`AI raporu ilerlemesi: %${status.progress_percent}`}
      />
      <p className="methodology-note">
        {status.succeeded_stage_count} / {status.expected_stage_count} aşama tamamlandı.
      </p>
      <PipelineStageList status={status} />
      {onRefresh && (
        <button type="button" className="btn-secondary" onClick={onRefresh}>
          Durumu yenile
        </button>
      )}
    </div>
  );
}

function PipelineStageList({ status }: { status: AIPipelineStatusResponse }) {
  return (
    <ol className="report-findings" aria-label="AI işlem hattı aşamaları">
      {status.stages.map((stage, index) => {
        const typeLabel = STAGE_TYPE_LABELS[stage.stage_type] ?? stage.stage_type;
        const statusLabel = STAGE_STATUS_LABELS[stage.status] ?? stage.status;
        const batchSuffix = stage.batch_index != null ? ` (grup ${stage.batch_index + 1})` : "";
        return (
          <li
            key={`${stage.stage_type}-${stage.batch_index ?? "x"}-${index}`}
            className="report-finding"
          >
            <span>
              <strong>{typeLabel}</strong>
              {batchSuffix} — {statusLabel}
              {stage.error_code ? ` (hata: ${stage.error_code})` : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function AiReportContent({ report }: { report: AIReportResponse }) {
  const ux = report.report;
  return (
    <div className="ai-report-content">
      <div className="ai-summary-card">
        <span className="ai-summary-card__label">Yönetici özeti</span>
        <p>{polishLegacyMockCopy(ux.summary)}</p>
      </div>

      <h3>Önerilen iyileştirmeler</h3>
      {ux.findings.length === 0 ? (
        <p className="report-section__intro">Bu raporda listelenen bir bulgu bulunmuyor.</p>
      ) : (
        <ul className="report-findings">
          {ux.findings.map((finding) => (
            <AiFindingCard key={finding.finding_id} finding={finding} />
          ))}
        </ul>
      )}

      <details className="ai-technical-notes">
        <summary>Teknik bilgiler ve sınırlamalar</summary>
        <p>{polishLegacyMockCopy(ux.limitations)}</p>
        <p className="methodology-note">
          Üretim tarihi: {formatDateTime(report.generated_at)} · Rapor sürümü: {ux.report_version}
        </p>
        <p className="methodology-note">
          AI sağlayıcısı: {report.provider ?? "—"} · Model: {report.model_name ?? "—"} · İstem
          sürümü: {report.instruction_version ?? "—"}
        </p>
      </details>
    </div>
  );
}

function AiFindingCard({ finding }: { finding: AIReportFinding }) {
  const priorityLabel = PRIORITY_LABELS[finding.priority] ?? finding.priority;
  const priorityColor = PRIORITY_COLORS[finding.priority] ?? "100, 116, 139";

  return (
    <li className="finding-detail-card">
      <p className="finding-detail-card__row">
        <span
          className="status-badge"
          style={{
            backgroundColor: `rgba(${priorityColor}, 0.14)`,
            borderColor: `rgba(${priorityColor}, 0.6)`,
          }}
        >
          {priorityLabel}
        </span>{" "}
        <span className="methodology-note">
          Güven düzeyi: {confidenceLabel(finding.confidence)}
        </span>
      </p>
      <p className="finding-detail-card__row">
        <strong>Ne anlama geliyor?</strong> {polishLegacyMockCopy(finding.finding)}
      </p>
      <p className="finding-detail-card__action">
        <strong>Önerilen adım:</strong> {polishLegacyMockCopy(finding.recommendation)}
      </p>
      <p className="finding-detail-card__row">
        <strong>Tahmini etki alanı:</strong>{" "}
        {finding.estimated_affected_users.toLocaleString("tr-TR")}
        {" sentetik persona"}
      </p>
    </li>
  );
}
