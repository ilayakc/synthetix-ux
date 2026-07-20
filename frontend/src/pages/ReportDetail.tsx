import { useEffect, useId, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  type AIExplanationResponse,
  type ReportAbComparison,
  type ReportCampaignCta,
  type ReportDetailResponse,
  type ReportHeatmapLevel,
  type ReportHeatmapRegion,
  type ReportNetworkDevice,
  generateReportAiExplanation,
  getReport,
  getReportExportUrl,
  getReportHeatmapScreenshotUrl,
} from "../api/client";

const METRIC_LABELS: Record<string, string> = {
  task_completion_probability: "Görev tamamlama olasılığı",
  task_duration_seconds: "Tahmini görev süresi",
  misclick_probability: "Yanlış tıklama olasılığı",
  abandonment_probability: "Terk (abandonment) olasılığı",
  readability_score: "Okunabilirlik skoru",
};

function formatPercent(value: number): string {
  return `%${Math.round(value * 100)}`;
}

interface UncertaintyBarProps {
  label: string;
  pointEstimate: number;
  low: number;
  high: number;
  unit: "percent" | "seconds";
  extraNote?: string;
}

function UncertaintyBar({ label, pointEstimate, low, high, unit, extraNote }: UncertaintyBarProps) {
  const tooltipId = useId();
  const isPercent = unit === "percent";
  const scaleMax = isPercent ? 1 : Math.max(high * 1.15, 1);
  const toPercentOfTrack = (value: number) => Math.max(0, Math.min(100, (value / scaleMax) * 100));

  const format = (value: number) => (isPercent ? formatPercent(value) : `${Math.round(value)} sn`);

  const valueText = `Nokta tahmini ${format(pointEstimate)}, belirsizlik aralığı ${format(low)} – ${format(high)}${
    extraNote ? `. ${extraNote}` : ""
  }`;

  return (
    <div className="uncertainty-bar">
      <div className="uncertainty-bar__label-row">
        <span className="uncertainty-bar__label">{label}</span>
        <span className="uncertainty-bar__value">
          {format(pointEstimate)} ({format(low)} – {format(high)})
        </span>
      </div>
      <div className="uncertainty-bar__track">
        <div
          className="uncertainty-bar__range"
          style={{
            left: `${toPercentOfTrack(low)}%`,
            width: `${Math.max(0, toPercentOfTrack(high) - toPercentOfTrack(low))}%`,
          }}
        />
        <button
          type="button"
          className="uncertainty-bar__point"
          style={{ left: `${toPercentOfTrack(pointEstimate)}%` }}
          aria-describedby={tooltipId}
          aria-label={`${label}: ${valueText}`}
        />
        <span role="tooltip" id={tooltipId} className="uncertainty-bar__tooltip">
          {valueText}
        </span>
      </div>
    </div>
  );
}

function PerformanceSummary({ report }: { report: ReportDetailResponse }) {
  const { metrics } = report;
  return (
    <section className="report-section" aria-labelledby="report-performance-heading">
      <h2 id="report-performance-heading" className="report-section__heading">
        Performans özeti
      </h2>
      <p className="report-section__intro">
        Nokta tahminleri ile birlikte belirsizlik aralıkları (üçgen dağılım) gösterilir. Bu bir
        sentetik senaryo tahminidir; gerçek kullanıcı verisi değildir.
      </p>

      <UncertaintyBar
        label={METRIC_LABELS.task_completion_probability}
        pointEstimate={metrics.task_completion_probability.point_estimate}
        low={metrics.task_completion_probability.low}
        high={metrics.task_completion_probability.high}
        unit="percent"
      />
      <UncertaintyBar
        label={METRIC_LABELS.misclick_probability}
        pointEstimate={metrics.misclick_probability.point_estimate}
        low={metrics.misclick_probability.low}
        high={metrics.misclick_probability.high}
        unit="percent"
      />
      <UncertaintyBar
        label={METRIC_LABELS.abandonment_probability}
        pointEstimate={metrics.abandonment_probability.point_estimate}
        low={metrics.abandonment_probability.low}
        high={metrics.abandonment_probability.high}
        unit="percent"
      />
      <UncertaintyBar
        label={METRIC_LABELS.task_duration_seconds}
        pointEstimate={metrics.task_duration_seconds.point_estimate}
        low={metrics.task_duration_seconds.p10}
        high={metrics.task_duration_seconds.p90}
        unit="seconds"
        extraNote="Aralık p10–p90 olarak gösterilir."
      />

      <div className="report-visually-hidden">
        {report.accessible_chart_summaries.map((summary) => (
          <p key={summary.chart_key}>{summary.text}</p>
        ))}
      </div>

      <div className="result-metric-grid">
        <div className="result-metric">
          <span className="result-metric__label">Okunabilirlik skoru</span>
          <span className="result-metric__value">{metrics.readability_score} / 100</span>
        </div>
        <div className="result-metric">
          <span className="result-metric__label">Kontrast kontrolü (WCAG AA)</span>
          <span className="result-metric__value">
            {metrics.contrast_check.pass ? "Geçti" : "Geçmedi"}
          </span>
          <span className="result-metric__range">
            Ortalama oran: {metrics.contrast_check.avg_ratio} (eşik:{" "}
            {metrics.contrast_check.threshold})
          </span>
        </div>
      </div>
    </section>
  );
}

function AbComparisonSection({ comparison }: { comparison: ReportAbComparison }) {
  const rows = Object.entries(comparison.comparisons);
  const labelA =
    comparison.this_variant_role === "variant_a"
      ? "Bu rapor (A)"
      : comparison.sibling_variant_name + " (A)";
  const labelB =
    comparison.this_variant_role === "variant_b"
      ? "Bu rapor (B)"
      : comparison.sibling_variant_name + " (B)";

  return (
    <section className="report-section" aria-labelledby="report-ab-heading">
      <h2 id="report-ab-heading" className="report-section__heading">
        A/B metrik karşılaştırması
      </h2>
      <p className="report-section__intro">{comparison.note}</p>

      <div className="ab-comparison-legend">
        <span className="ab-comparison-legend__item">
          <span
            className="ab-comparison-legend__swatch ab-comparison-legend__swatch--a"
            aria-hidden="true"
          />
          {labelA}
        </span>
        <span className="ab-comparison-legend__item">
          <span
            className="ab-comparison-legend__swatch ab-comparison-legend__swatch--b"
            aria-hidden="true"
          />
          {labelB}
        </span>
      </div>

      {rows.map(([key, values]) => {
        const maxValue = Math.max(Math.abs(values.variant_a), Math.abs(values.variant_b), 1e-9);
        const widthA = (Math.abs(values.variant_a) / maxValue) * 100;
        const widthB = (Math.abs(values.variant_b) / maxValue) * 100;
        const isPercentMetric = key !== "task_duration_seconds" && key !== "readability_score";
        const format = (value: number) =>
          isPercentMetric ? formatPercent(value) : value.toString();

        return (
          <div className="ab-comparison-row" key={key}>
            <div className="ab-comparison-row__label">{METRIC_LABELS[key] ?? key}</div>
            <div className="ab-comparison-row__bars">
              <div className="ab-comparison-row__bar-track">
                <div className="ab-comparison-row__bar-outer">
                  <div
                    className="ab-comparison-row__bar-fill ab-comparison-row__bar-fill--a"
                    style={{ width: `${widthA}%` }}
                  />
                </div>
                <span className="ab-comparison-row__bar-value">{format(values.variant_a)}</span>
              </div>
              <div className="ab-comparison-row__bar-track">
                <div className="ab-comparison-row__bar-outer">
                  <div
                    className="ab-comparison-row__bar-fill ab-comparison-row__bar-fill--b"
                    style={{ width: `${widthB}%` }}
                  />
                </div>
                <span className="ab-comparison-row__bar-value">{format(values.variant_b)}</span>
              </div>
            </div>
          </div>
        );
      })}

      <p className="methodology-note">
        Örneklenen sentetik persona sayısı — A:{" "}
        {comparison.sampled_synthetic_persona_count.variant_a}, B:{" "}
        {comparison.sampled_synthetic_persona_count.variant_b}. Kalibrasyon durumu:{" "}
        {comparison.calibration_status}.
      </p>
    </section>
  );
}

function PersonaSegmentsSection({ report }: { report: ReportDetailResponse }) {
  if (report.persona_segments.length === 0) {
    return (
      <section className="report-section" aria-labelledby="report-personas-heading">
        <h2 id="report-personas-heading" className="report-section__heading">
          Persona segmentleri
        </h2>
        <div className="empty-state">
          <p>Bu çalıştırma için bir persona örneklemi kaydedilmemiş.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="report-section" aria-labelledby="report-personas-heading">
      <h2 id="report-personas-heading" className="report-section__heading">
        Persona segmentleri
      </h2>
      <p className="report-section__intro">{report.persona_segment_note}</p>
      <table className="persona-segment-table">
        <thead>
          <tr>
            <th scope="col">Segment</th>
            <th scope="col">Sentetik n</th>
            <th scope="col">Pay</th>
            <th scope="col">Belirsizlik</th>
          </tr>
        </thead>
        <tbody>
          {report.persona_segments.map((segment) => (
            <tr key={segment.key}>
              <td>{segment.label}</td>
              <td>{segment.count}</td>
              <td>{formatPercent(segment.share)}</td>
              <td>
                {segment.small_sample_warning ? (
                  <span className="status-badge status-badge--draft">Küçük örnek (n&lt;30)</span>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function CriticalFindingsSection({ report }: { report: ReportDetailResponse }) {
  return (
    <section className="report-section" aria-labelledby="report-findings-heading">
      <h2 id="report-findings-heading" className="report-section__heading">
        Kritik bulgular
      </h2>
      <ul className="report-findings">
        {report.critical_findings.map((finding) => (
          <li key={finding.key} className={`report-finding report-finding--${finding.severity}`}>
            <span className="report-finding__icon" aria-hidden="true">
              {finding.severity === "warning" ? "⚠" : "ℹ"}
            </span>
            <span>{finding.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

// Bilimsel durustluk (bkz. docs/scientific-integrity.md): bu metin, gorsel
// katmanin HEMEN uzerinde, her zaman gorunur bicimde gosterilir. "Goz
// takibi"/"kullanicilar buraya bakti" gibi yasakli iddialar KESINLIKLE
// kullanilmaz.
const HEATMAP_VISUAL_DISCLAIMER =
  "Bu görsel gerçek göz hareketlerinden veya gerçek kullanıcı oturumlarından üretilmemiştir. " +
  "Sayfa yerleşimi ve anonim element özelliklerinden türetilmiş, kalibre edilmemiş sentetik bir " +
  "tahmindir.";

const HEATMAP_MODULE_SELECTION_HINT =
  'Görsel yoğunluk katmanını görmek için yeni bir testte 4. adımda "Sentetik dikkat tahmini" ' +
  "modülünü seçin ve bu URL için daha önce bir sayfa analizi (ekran görüntüsü) çalıştırılmış olsun.";

const HEATMAP_LEVEL_LABELS: Record<ReportHeatmapLevel, string> = {
  low: "Düşük",
  medium: "Orta",
  high: "Yüksek",
};

// Soguk (dusuk) -> sari/turuncu (orta) -> kirmizi (yuksek); renk TEK BASINA
// anlam tasimaz, her zaman metin etiketiyle (HEATMAP_LEVEL_LABELS) birlikte
// gosterilir (bkz. erisilebilirlik gereksinimi).
const HEATMAP_LEVEL_COLORS: Record<ReportHeatmapLevel, string> = {
  low: "37, 99, 235",
  medium: "217, 119, 6",
  high: "220, 38, 38",
};

function scoreToLevel(score: number): ReportHeatmapLevel {
  if (score < 0.15) return "low";
  if (score > 0.25) return "high";
  return "medium";
}

function deriveHeatmapRegions(
  regions: ReportHeatmapRegion[] | null | undefined,
  grid: Record<string, unknown>[] | null,
): ReportHeatmapRegion[] {
  if (regions) return regions;
  if (!grid) return [];
  return grid.map((cell, index) => {
    const score = Number(cell.score ?? 0);
    return {
      key: String(cell.key ?? index),
      label: String(cell.label ?? cell.key ?? index),
      score,
      level: scoreToLevel(score),
      box: null,
    };
  });
}

function HeatmapAccessibleTable({ regions }: { regions: ReportHeatmapRegion[] }) {
  return (
    <table className="persona-segment-table">
      <caption className="report-visually-hidden">
        Bölge başına sentetik dikkat payı ve düşük/orta/yüksek etiketi
      </caption>
      <thead>
        <tr>
          <th scope="col">Bölge</th>
          <th scope="col">Sentetik dikkat payı</th>
          <th scope="col">Seviye</th>
        </tr>
      </thead>
      <tbody>
        {regions.map((region) => (
          <tr key={region.key}>
            <td>{region.label}</td>
            <td>{formatPercent(region.score)}</td>
            <td>{HEATMAP_LEVEL_LABELS[region.level]}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function HeatmapLegend() {
  const levels: ReportHeatmapLevel[] = ["low", "medium", "high"];
  return (
    <ul className="heatmap-legend">
      {levels.map((level) => (
        <li key={level} className="heatmap-legend__item">
          <span
            className="heatmap-legend__swatch"
            style={{ backgroundColor: `rgba(${HEATMAP_LEVEL_COLORS[level]}, 0.55)` }}
            aria-hidden="true"
          />
          {HEATMAP_LEVEL_LABELS[level]} sentetik dikkat payı
        </li>
      ))}
    </ul>
  );
}

function HeatmapRegionMarker({
  region,
  layerVisible,
}: {
  region: ReportHeatmapRegion;
  layerVisible: boolean;
}) {
  if (!region.box) return null;
  const color = HEATMAP_LEVEL_COLORS[region.level];
  const accessibleLabel = `${region.label}: tahmini görsel yoğunluk %${Math.round(region.score * 100)} (${HEATMAP_LEVEL_LABELS[region.level]})`;
  return (
    <button
      type="button"
      className="heatmap-region"
      style={{
        left: `${region.box.x_pct}%`,
        top: `${region.box.y_pct}%`,
        width: `${region.box.width_pct}%`,
        height: `${region.box.height_pct}%`,
        backgroundColor: `rgba(${color}, 0.38)`,
        borderColor: `rgba(${color}, 0.95)`,
        visibility: layerVisible ? "visible" : "hidden",
      }}
      aria-label={accessibleLabel}
      title={accessibleLabel}
    />
  );
}

function HeatmapSection({ report }: { report: ReportDetailResponse }) {
  const { heatmap } = report;
  const [activeTab, setActiveTab] = useState<"visual" | "table">("visual");
  const [layerVisible, setLayerVisible] = useState(true);

  const hasGrid = Boolean(heatmap.available && heatmap.grid && heatmap.grid.length > 0);
  const regions = deriveHeatmapRegions(heatmap.regions, heatmap.grid);
  const coordinatesAvailable = Boolean(heatmap.coordinates_available && heatmap.screenshot_url);

  return (
    <section className="report-section" aria-labelledby="report-heatmap-heading">
      <h2 id="report-heatmap-heading" className="report-section__heading">
        {heatmap.label}
      </h2>

      {heatmap.disclaimer && (
        <p className="report-section__intro report-disclaimer-strong">{heatmap.disclaimer}</p>
      )}

      {!hasGrid && (
        <div className="report-heatmap-empty">
          <p>
            Bu çalıştırma için "Sentetik dikkat tahmini" modülü seçilmemiş; bu bölüm yalnızca modül
            seçiliyken ve sonuç üretildiğinde doldurulur. Gösterilecek veri yok.
          </p>
          <p>{HEATMAP_MODULE_SELECTION_HINT}</p>
        </div>
      )}

      {hasGrid && !coordinatesAvailable && (
        <>
          <p className="report-heatmap-coords-note">
            {heatmap.coordinates_unavailable_reason ??
              "Görsel katman için gerekli konum verisi bulunamadı; rastgele koordinat üretilmez, " +
                "bu nedenle yalnızca erişilebilir tablo görünümü gösteriliyor."}
          </p>
          <HeatmapAccessibleTable regions={regions} />
        </>
      )}

      {hasGrid && coordinatesAvailable && (
        <>
          <div className="report-tabs" role="tablist" aria-label="Sentetik dikkat tahmini görünümü">
            <button
              type="button"
              role="tab"
              id="heatmap-tab-visual"
              aria-selected={activeTab === "visual"}
              aria-controls="heatmap-panel-visual"
              className={`report-tab${activeTab === "visual" ? " report-tab--active" : ""}`}
              onClick={() => setActiveTab("visual")}
            >
              Görsel görünüm
            </button>
            <button
              type="button"
              role="tab"
              id="heatmap-tab-table"
              aria-selected={activeTab === "table"}
              aria-controls="heatmap-panel-table"
              className={`report-tab${activeTab === "table" ? " report-tab--active" : ""}`}
              onClick={() => setActiveTab("table")}
            >
              Erişilebilir tablo
            </button>
          </div>

          {activeTab === "visual" && (
            <div role="tabpanel" id="heatmap-panel-visual" aria-labelledby="heatmap-tab-visual">
              <p className="report-disclaimer-strong">{HEATMAP_VISUAL_DISCLAIMER}</p>

              <label className="heatmap-layer-toggle">
                <input
                  type="checkbox"
                  checked={layerVisible}
                  onChange={(event) => setLayerVisible(event.target.checked)}
                />
                Yoğunluk katmanını göster
              </label>

              <div className="heatmap-visual-layout">
                <div className="heatmap-image-wrap">
                  <img
                    src={getReportHeatmapScreenshotUrl(heatmap.screenshot_url as string)}
                    alt="Sayfanın önceden alınmış güvenli ekran görüntüsü"
                    className="heatmap-screenshot"
                  />
                  {regions.map((region) => (
                    <HeatmapRegionMarker
                      key={region.key}
                      region={region}
                      layerVisible={layerVisible}
                    />
                  ))}
                </div>
                <HeatmapLegend />
              </div>

              <p className="report-section__intro">
                Bölgeler arasında Tab tuşuyla dolaşabilir, her bölgenin erişilebilir adını ve
                sentetik puanını okuyucunuzla dinleyebilirsiniz. Aşağıdaki tablo aynı verinin
                erişilebilir alternatifidir.
              </p>
              <HeatmapAccessibleTable regions={regions} />
            </div>
          )}

          {activeTab === "table" && (
            <div role="tabpanel" id="heatmap-panel-table" aria-labelledby="heatmap-tab-table">
              <HeatmapAccessibleTable regions={regions} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function CampaignCtaSection({ campaignCta }: { campaignCta: ReportCampaignCta }) {
  return (
    <section className="report-section" aria-labelledby="report-campaign-cta-heading">
      <h2 id="report-campaign-cta-heading" className="report-section__heading">
        Kampanya ve CTA analizi
      </h2>
      <p className="report-section__intro report-disclaimer-strong">{campaignCta.disclaimer}</p>

      {campaignCta.ctas.map((cta) => (
        <UncertaintyBar
          key={cta.key}
          label={`${cta.label}${cta.above_fold ? " (ilk ekranda)" : ""}`}
          pointEstimate={cta.click_probability.point_estimate}
          low={cta.click_probability.low}
          high={cta.click_probability.high}
          unit="percent"
        />
      ))}

      <h3>Mesaj netliği bulguları</h3>
      <ul className="report-findings">
        {campaignCta.message_clarity_findings.map((finding) => (
          <li key={finding.key} className={`report-finding report-finding--${finding.severity}`}>
            <span className="report-finding__icon" aria-hidden="true">
              {finding.severity === "warning" ? "⚠" : "ℹ"}
            </span>
            <span>{finding.text}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function NetworkDeviceSection({ networkDevice }: { networkDevice: ReportNetworkDevice }) {
  return (
    <section className="report-section" aria-labelledby="report-network-device-heading">
      <h2 id="report-network-device-heading" className="report-section__heading">
        Ağ ve cihaz testi
      </h2>
      <p className="report-section__intro report-disclaimer-strong">{networkDevice.disclaimer}</p>

      <table className="persona-segment-table">
        <thead>
          <tr>
            <th scope="col">Profil</th>
            <th scope="col">Durum</th>
            <th scope="col">Yükleme süresi</th>
            <th scope="col">Erişilebilirlik ihlali</th>
          </tr>
        </thead>
        <tbody>
          {networkDevice.profiles.map((profile) => (
            <tr key={profile.profile_key}>
              <td>
                {profile.device_label} — {profile.network_label}
              </td>
              <td>
                {profile.succeeded ? (
                  "Başarılı"
                ) : (
                  <span className="status-badge status-badge--draft">
                    Başarısız: {profile.error}
                  </span>
                )}
              </td>
              <td>
                {profile.timings?.total_navigation_ms != null
                  ? `${Math.round(profile.timings.total_navigation_ms)} ms`
                  : "—"}
              </td>
              <td>{profile.accessibility_violation_count ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="methodology-note">
        Profil hata oranı: {formatPercent(networkDevice.error_rate)}.
      </p>
    </section>
  );
}

function AiExplanationSection({ reportId }: { reportId: string }) {
  const [explanation, setExplanation] = useState<AIExplanationResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleGenerate = () => {
    setLoading(true);
    setError(null);
    generateReportAiExplanation(reportId)
      .then(setExplanation)
      .catch(() => setError("AI destekli açıklama üretilemedi. Lütfen tekrar deneyin."))
      .finally(() => setLoading(false));
  };

  return (
    <section className="report-section" aria-labelledby="report-ai-explanation-heading">
      <h2 id="report-ai-explanation-heading" className="report-section__heading">
        AI destekli açıklama
      </h2>
      <p className="report-section__intro">
        Bu bölüm otomatik olarak üretilmiştir; bir AI kararı değildir ve uzman
        değerlendirmesi/doğrulaması gerektirir. Yalnızca bu rapordaki sentetik, kalibre edilmemiş
        metrikleri temel alır.
      </p>

      {!explanation && (
        <button
          type="button"
          className="auth-google-button"
          onClick={handleGenerate}
          disabled={loading}
        >
          {loading ? "Üretiliyor…" : "AI destekli açıklama üret"}
        </button>
      )}

      {error && <p className="page-placeholder">{error}</p>}

      {explanation && (
        <div className="ai-explanation">
          <p className="report-info-box__title">
            Kalibrasyon durumu: {explanation.calibration_status}
          </p>

          <h3>Kısa özet</h3>
          <p>{explanation.short_summary}</p>

          <h3>Metrik dayanakları</h3>
          <ul className="report-findings">
            {explanation.metric_basis.map((finding, index) => (
              <li key={index}>
                {finding.text}{" "}
                <span className="methodology-note">(dayanak: {finding.metric_ids.join(", ")})</span>
              </li>
            ))}
          </ul>

          <h3>Olası açıklamalar</h3>
          <ul className="report-findings">
            {explanation.possible_explanations.map((finding, index) => (
              <li key={index}>
                {finding.text}{" "}
                <span className="methodology-note">(dayanak: {finding.metric_ids.join(", ")})</span>
              </li>
            ))}
          </ul>

          <h3>Önerilen doğrulama deneyi</h3>
          <p>{explanation.suggested_verification_experiment}</p>

          <h3>Sınırlamalar</h3>
          <p>{explanation.limitations}</p>

          <p className="methodology-note">
            Sağlayıcı: {explanation.provider}
            {explanation.model_name ? ` (${explanation.model_name})` : ""} · Prompt sürümü:{" "}
            {explanation.prompt_version} · Oluşturulma:{" "}
            {new Date(explanation.generated_at).toLocaleString("tr-TR")}
          </p>

          <button
            type="button"
            className="auth-google-button"
            onClick={handleGenerate}
            disabled={loading}
          >
            {loading ? "Üretiliyor…" : "Yeniden üret"}
          </button>
        </div>
      )}
    </section>
  );
}

export default function ReportDetail() {
  const { reportId } = useParams<{ reportId: string }>();
  const [report, setReport] = useState<ReportDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!reportId) return;
    getReport(reportId)
      .then(setReport)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError("Rapor yüklenemedi.");
        }
      });
  }, [reportId]);

  if (notFound) {
    return (
      <section aria-labelledby="report-detail-heading">
        <h1 id="report-detail-heading" className="page-heading">
          Rapor bulunamadı
        </h1>
        <Link to="/raporlar">Raporlar listesine dön</Link>
      </section>
    );
  }

  if (error) {
    return <p className="page-placeholder">{error}</p>;
  }

  if (!report) {
    return <p className="page-placeholder">Yükleniyor…</p>;
  }

  return (
    <section aria-labelledby="report-detail-heading">
      <nav className="report-breadcrumb" aria-label="Breadcrumb">
        <Link to="/raporlar">Raporlar</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/projeler/${report.project_id}`}>{report.project_name}</Link>
        <span aria-hidden="true">/</span>
        <span>{report.test_definition_name}</span>
      </nav>

      <h1 id="report-detail-heading" className="page-heading">
        {report.title} — {report.variant_name}
      </h1>

      <dl className="report-info-box" aria-label="Rapor sabit bilgi kutusu">
        <p className="report-info-box__title">{report.info_box.not_real_user_data_label}</p>
        <div className="report-info-box__grid">
          <div>
            <dt>Model sürümü</dt>
            <dd>{report.info_box.model_version}</dd>
          </div>
          <div>
            <dt>Kalibrasyon durumu</dt>
            <dd>{report.info_box.calibration_status}</dd>
          </div>
          <div>
            <dt>Oluşturulma tarihi</dt>
            <dd>{new Date(report.info_box.generated_at).toLocaleString("tr-TR")}</dd>
          </div>
          <div>
            <dt>Seed</dt>
            <dd>{report.info_box.deterministic_seed}</dd>
          </div>
          <div>
            <dt>Kurallar sürümü</dt>
            <dd>{report.info_box.rules_version ?? "—"}</dd>
          </div>
          <div>
            <dt>URL</dt>
            <dd>{report.info_box.input_summary.url ?? "—"}</dd>
          </div>
          <div>
            <dt>Persona sayısı</dt>
            <dd>{report.info_box.input_summary.persona_count ?? "—"}</dd>
          </div>
          <div>
            <dt>Hedef kitle</dt>
            <dd>{report.info_box.input_summary.target_audience ?? "—"}</dd>
          </div>
        </div>
      </dl>

      <div className="report-export-actions">
        <a className="auth-google-button" href={getReportExportUrl(report.export_json_url)}>
          JSON olarak dışa aktar
        </a>
        <a className="auth-google-button" href={getReportExportUrl(report.export_csv_url)}>
          CSV olarak dışa aktar
        </a>
      </div>

      <PerformanceSummary report={report} />

      {report.ab_comparison && <AbComparisonSection comparison={report.ab_comparison} />}

      <PersonaSegmentsSection report={report} />

      <HeatmapSection report={report} />

      {report.campaign_cta && <CampaignCtaSection campaignCta={report.campaign_cta} />}

      {report.network_device && <NetworkDeviceSection networkDevice={report.network_device} />}

      <CriticalFindingsSection report={report} />

      <AiExplanationSection reportId={report.id} />

      <section className="report-section" aria-labelledby="report-methodology-heading">
        <h2 id="report-methodology-heading" className="report-section__heading">
          Metodoloji ve sınırlamalar
        </h2>
        <p className="methodology-note">{report.disclaimer}</p>
        <p className="methodology-note">Metodoloji: {report.methodology_reference}</p>
      </section>
    </section>
  );
}
