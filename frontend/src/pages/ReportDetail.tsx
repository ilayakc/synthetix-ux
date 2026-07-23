import { useEffect, useId, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  type AIExplanationResponse,
  type CtaOverlayClassification,
  type ReportAbComparison,
  type ReportCampaignCta,
  type ReportCtaOverlayBox,
  type ReportCtaOverlaySection,
  type ReportDetailResponse,
  type ReportHeatmapLevel,
  type ReportHeatmapRegion,
  type ReportHeatmapSection,
  type ReportNetworkDevice,
  type ReportVisualAttentionCell,
  generateReportAiExplanation,
  getReport,
  getReportExportUrl,
  getReportHeatmapScreenshotUrl,
} from "../api/client";

const SOURCE_TYPE_LABELS: Record<string, string> = {
  url: "URL",
  screenshot: "Yüklenen ekran görüntüsü",
  ai_generated: "AI tarafından oluşturuldu",
};

function SourceTypeBadge({ sourceType }: { sourceType: string | null | undefined }) {
  if (!sourceType) return null;
  return (
    <span className="status-badge status-badge--draft">
      Kaynak: {SOURCE_TYPE_LABELS[sourceType] ?? sourceType}
    </span>
  );
}

const METRIC_LABELS: Record<string, string> = {
  task_completion_probability: "Görevi tamamlama tahmini",
  task_duration_seconds: "Tahmini görev süresi",
  misclick_probability: "Yanlış tıklama riski",
  abandonment_probability: "Terk riski",
  readability_score: "Okunabilirlik skoru",
};

function formatPercent(value: number): string {
  return `%${Math.round(value * 100)}`;
}

function formatSeconds(value: number): string {
  return `${Math.round(value)} sn`;
}

// --- Genel sekme (tablist) bileseni: Ozet / Gorsel Karsilastirma /
// Bulgular ve Oneriler / Teknik Detaylar. Klavye ile ok tuslariyla gezinme
// destekler (bkz. Ayarlar ekranindaki benzer desen).
interface TabDef {
  id: string;
  label: string;
}

function TopLevelTabs({
  tabs,
  activeTab,
  onChange,
  idPrefix,
}: {
  tabs: TabDef[];
  activeTab: string;
  onChange: (id: string) => void;
  idPrefix: string;
}) {
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const handleKeyDown = (event: React.KeyboardEvent, index: number) => {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    onChange(nextTab.id);
    tabRefs.current[nextTab.id]?.focus();
  };

  return (
    <div className="report-top-tablist" role="tablist" aria-label="Rapor bölümleri">
      {tabs.map((tab, index) => (
        <button
          key={tab.id}
          ref={(el) => {
            tabRefs.current[tab.id] = el;
          }}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${tab.id}`}
          aria-selected={activeTab === tab.id}
          aria-controls={`${idPrefix}-panel-${tab.id}`}
          tabIndex={activeTab === tab.id ? 0 : -1}
          className={`report-top-tab${activeTab === tab.id ? " report-top-tab--active" : ""}`}
          onClick={() => onChange(tab.id)}
          onKeyDown={(event) => handleKeyDown(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// --- Kucuk "Disa aktar" menusu: JSON/CSV linklerini ayri buyuk butonlar
// yerine tek bir acilir menu icine alir (bkz. plan §1, birincil vurgu
// sonuc/yapilacaklar olmali, disa aktarma ikincil kalmali).
function ExportMenu({ jsonUrl, csvUrl }: { jsonUrl: string; csvUrl: string }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div className="export-menu" ref={containerRef}>
      <button
        type="button"
        className="btn-secondary export-menu__trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        Dışa aktar ▾
      </button>
      {open && (
        <div className="export-menu__list" role="menu" aria-label="Dışa aktarma seçenekleri">
          <a role="menuitem" className="export-menu__item" href={getReportExportUrl(jsonUrl)}>
            JSON olarak dışa aktar
          </a>
          <a role="menuitem" className="export-menu__item" href={getReportExportUrl(csvUrl)}>
            CSV olarak dışa aktar
          </a>
        </div>
      )}
    </div>
  );
}

const SCIENTIFIC_DISCLAIMER =
  "Bu rapor sentetik ve kalibre edilmemiş tahminler içerir; gerçek kullanıcı testi değildir.";

// --- Sonuc karti: global bir "kazanan" uydurmaz. Metrikler tam esitse
// esitlik acikca soylenir ("esit performans kanitlandi" DENMEZ); farkli ise
// tarafsizca sayisal fark bildirilir, istatistiksel anlamlilik iddia
// edilmez (bkz. docs/scientific-integrity.md).
const HEADLINE_METRIC_KEYS = [
  "task_completion_probability",
  "misclick_probability",
  "abandonment_probability",
  "task_duration_seconds",
] as const;

function getAbSideLabels(comparison: ReportAbComparison): { labelA: string; labelB: string } {
  const labelA =
    comparison.this_variant_role === "variant_a"
      ? "Bu rapor (A)"
      : `${comparison.sibling_variant_name} (A)`;
  const labelB =
    comparison.this_variant_role === "variant_b"
      ? "Bu rapor (B)"
      : `${comparison.sibling_variant_name} (B)`;
  return { labelA, labelB };
}

function ResultSummaryCard({ report }: { report: ReportDetailResponse }) {
  const ab = report.ab_comparison;

  if (!ab) {
    return (
      <div className="result-summary-card">
        <h2 className="result-summary-card__title">Sentetik simülasyon özeti</h2>
        <p className="result-summary-card__body">
          Bu rapor tek bir tasarım için sentetik senaryo tahminleri içerir. Karşılaştırma için bir
          A/B testi tanımlanmadıysa yalnızca bu tasarımın tahminleri gösterilir.
        </p>
      </div>
    );
  }

  const allEqual = HEADLINE_METRIC_KEYS.every((key) => {
    const entry = ab.comparisons[key];
    return !entry || Math.abs(entry.delta) < 1e-9;
  });

  if (allEqual) {
    return (
      <div className="result-summary-card">
        <h2 className="result-summary-card__title">
          İki tasarım arasında belirgin metrik farkı görülmedi
        </h2>
        <p className="result-summary-card__body">
          Gösterilen sentetik görev, süre ve hata tahminleri iki tasarım için aynı çıktı. Karar
          verirken CTA görünürlüğü, kontrast ve görsel dikkat dağılımını inceleyin.
        </p>
      </div>
    );
  }

  const { labelA, labelB } = getAbSideLabels(ab);
  const differing = HEADLINE_METRIC_KEYS.filter((key) => {
    const entry = ab.comparisons[key];
    return entry && Math.abs(entry.delta) > 1e-9;
  });

  return (
    <div className="result-summary-card">
      <h2 className="result-summary-card__title">Tasarımlar arasında sentetik metrik farkları var</h2>
      <p className="result-summary-card__body">
        {labelA} ve {labelB} için gösterilen sentetik tahminler bazı metriklerde farklı. Bu bir
        istatistiksel anlamlılık iddiası değildir; yalnızca simülasyon farkıdır ve tek başına bir
        tasarımı "kazanan" ilan etmez.
      </p>
      <ul className="result-summary-card__diff-list">
        {differing.map((key) => {
          const entry = ab.comparisons[key];
          const isPercent = key !== "task_duration_seconds";
          const format = (value: number) => (isPercent ? formatPercent(value) : formatSeconds(value));
          return (
            <li key={key}>
              {METRIC_LABELS[key]}: A {format(entry.variant_a)} — B {format(entry.variant_b)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// --- Bulgu metaverisi: backend'in ürettigi sabit `key` degerlerini kisa
// baslik + uygulanabilir oneriye esler. Bilinmeyen anahtarlar icin ozgun
// metin fallback olarak kullanilir (yeni bilimsel iddia uydurulmaz).
const FINDING_COPY: Record<string, { title: string; action: string }> = {
  contrast_below_threshold: {
    title: "Kontrastı güçlendirin",
    action:
      "CTA ve metin çevresindeki bölgesel görsel kontrast düşük görünüyor. Buton ve arka plan renkleri arasındaki farkı artırın.",
  },
  low_task_completion: {
    title: "Görev tamamlamayı kolaylaştırın",
    action: "Form alanlarını ve gezinme adımlarını azaltmayı, birincil CTA'yı ilk ekrana taşımayı değerlendirin.",
  },
  high_abandonment: {
    title: "Terk riskini azaltın",
    action: "Sayfa uzunluğunu ve gezinme derinliğini azaltın; mobil uyumluluğu kontrol edin.",
  },
  high_misclick: {
    title: "Yanlış tıklama riskini azaltın",
    action: "Birincil CTA sayısını azaltın ve CTA çevresindeki kontrastı artırın.",
  },
  low_readability: {
    title: "Okunabilirliği artırın",
    action: "Cümleleri kısaltın, başlık kullanımını artırın, kelime yoğunluğunu azaltın.",
  },
  cta_too_many_ctas: {
    title: "CTA görünürlüğünü sadeleştirin",
    action: "Sayfadaki birincil CTA sayısını azaltarak dikkatin dağılmasını önleyin.",
  },
};

interface CombinedFinding {
  key: string;
  severity: "info" | "warning";
  text: string;
}

function collectFindings(report: ReportDetailResponse): CombinedFinding[] {
  const combined: CombinedFinding[] = [
    ...report.critical_findings,
    ...(report.campaign_cta?.message_clarity_findings ?? []),
  ];
  return combined.filter((finding) => finding.key !== "no_threshold_triggered");
}

function TopFindingsSection({ report }: { report: ReportDetailResponse }) {
  const findings = collectFindings(report)
    .sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "warning" ? -1 : 1))
    .slice(0, 3);

  return (
    <section className="report-section" aria-labelledby="report-top-findings-heading">
      <h2 id="report-top-findings-heading" className="report-section__heading">
        Öncelikli bulgular
      </h2>
      {findings.length === 0 ? (
        <p className="report-section__intro">
          Bu raporda tanımlı eşik tabanlı kritik bir bulgu tetiklenmedi.
        </p>
      ) : (
        <div className="priority-finding-grid">
          {findings.map((finding) => {
            const copy = FINDING_COPY[finding.key];
            return (
              <div
                key={finding.key}
                className={`priority-finding-card priority-finding-card--${finding.severity}`}
              >
                <span className="priority-finding-card__severity">
                  {finding.severity === "warning" ? "Önemli" : "Bilgi"}
                </span>
                <h3 className="priority-finding-card__title">{copy?.title ?? finding.text}</h3>
                <p className="priority-finding-card__desc">{finding.text}</p>
                <p className="priority-finding-card__action">{copy?.action ?? "İlgili metrikleri gözden geçirin."}</p>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

// --- Hizli metrik karsilastirmasi: yalnizca dort sade satir. Belirsizlik
// araligi varsayilan gorunumde uzun metin olarak TEKRAR EDILMEZ; her satirda
// kucuk bir "Araligi goster" anahtari kullanilir.
const QUICK_METRIC_KEYS = HEADLINE_METRIC_KEYS;

function QuickMetricRow({
  label,
  isPercent,
  thisValue,
  thisRange,
  otherValue,
  otherLabel,
  thisLabel,
}: {
  label: string;
  isPercent: boolean;
  thisValue: number;
  thisRange: [number, number] | null;
  otherValue: number | null;
  otherLabel: string | null;
  thisLabel: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const format = (value: number) => (isPercent ? formatPercent(value) : formatSeconds(value));
  const delta = otherValue == null ? null : otherValue - thisValue;
  const noDifference = delta != null && Math.abs(delta) < 1e-9;

  return (
    <div className="quick-metric-row">
      <div className="quick-metric-row__main">
        <span className="quick-metric-row__label">{label}</span>
        <span className="quick-metric-row__value">
          {thisLabel}: {format(thisValue)}
          {otherValue != null && otherLabel ? ` · ${otherLabel}: ${format(otherValue)}` : ""}
        </span>
        <span className="quick-metric-row__delta">
          {otherValue == null ? "—" : noDifference ? "Fark yok" : format(Math.abs(delta as number))}
        </span>
        <button
          type="button"
          className="quick-metric-row__toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          Aralığı göster
        </button>
      </div>
      {expanded && (
        <p className="quick-metric-row__range">
          {thisLabel} belirsizlik aralığı:{" "}
          {thisRange ? `${format(thisRange[0])} – ${format(thisRange[1])}` : "—"}.
          {otherLabel
            ? ` ${otherLabel} için ayrıntılı aralık verisi bu görünümde yok.`
            : ""}
        </p>
      )}
    </div>
  );
}

function QuickMetricComparison({ report }: { report: ReportDetailResponse }) {
  const { metrics } = report;
  const ab = report.ab_comparison;
  const thisIsA = ab?.this_variant_role === "variant_a";
  const { labelA, labelB } = ab ? getAbSideLabels(ab) : { labelA: "", labelB: "" };
  const thisLabel = ab ? (thisIsA ? labelA : labelB) : "Bu tasarım";
  const otherLabel = ab ? (thisIsA ? labelB : labelA) : null;

  const rangeOf = (key: (typeof QUICK_METRIC_KEYS)[number]): [number, number] => {
    if (key === "task_duration_seconds") {
      return [metrics.task_duration_seconds.p10, metrics.task_duration_seconds.p90];
    }
    const metric = metrics[key];
    return [metric.low, metric.high];
  };

  const valueOf = (key: (typeof QUICK_METRIC_KEYS)[number]): number => {
    if (key === "task_duration_seconds") return metrics.task_duration_seconds.point_estimate;
    return metrics[key].point_estimate;
  };

  return (
    <section className="report-section" aria-labelledby="report-quick-metrics-heading">
      <h2 id="report-quick-metrics-heading" className="report-section__heading">
        {ab ? "Hızlı metrik karşılaştırması" : "Metrik özeti"}
      </h2>
      <div className="quick-metric-table">
        {QUICK_METRIC_KEYS.map((key) => {
          const isPercent = key !== "task_duration_seconds";
          const thisValue = valueOf(key);
          const otherValue = ab ? (thisIsA ? ab.comparisons[key].variant_b : ab.comparisons[key].variant_a) : null;
          return (
            <QuickMetricRow
              key={key}
              label={METRIC_LABELS[key]}
              isPercent={isPercent}
              thisValue={thisValue}
              thisRange={rangeOf(key)}
              otherValue={otherValue}
              otherLabel={otherLabel}
              thisLabel={thisLabel}
            />
          );
        })}
      </div>
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

// Bilimsel durustluk (bkz. docs/scientific-integrity.md): bu metin, gorsel
// katmanin HEMEN uzerinde, her zaman gorunur bicimde gosterilir. "Goz
// takibi"/"kullanicilar buraya bakti" gibi yasakli iddialar KESINLIKLE
// kullanilmaz.
const HEATMAP_VISUAL_DISCLAIMER =
  "Renkli katmanlar algoritmanın tahmini görsel belirginlik dağılımını gösterir. Gerçek göz takibi veya tıklama verisi değildir.";

const CTA_SHARED_DISCLAIMER =
  "CTA adayları piksel veya DOM özelliklerinden türetilir. Sizin seçtiğiniz CTA ayrıca işaretlenir.";

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

// En yuksek 5 ve en dusuk 5 kaydi varsayilan olarak gosterir; "Tumunu
// goster" ile tam liste acilir (bkz. plan §2 "Heatmap tablosu"). `forceAll`
// Teknik Detaylar sekmesinde tam veriyi hicbir zaman kirpmadan gosterir.
function topBottomSlice<T>(items: T[], scoreOf: (item: T) => number, n: number): T[] {
  if (items.length <= n * 2) return items;
  const sorted = [...items].sort((a, b) => scoreOf(b) - scoreOf(a));
  const top = sorted.slice(0, n);
  const bottom = sorted.slice(-n);
  const keys = new Set([...top, ...bottom]);
  return items.filter((item) => keys.has(item));
}

function HeatmapAccessibleTable({
  regions,
  forceAll = false,
}: {
  regions: ReportHeatmapRegion[];
  forceAll?: boolean;
}) {
  const [showAll, setShowAll] = useState(forceAll);
  const shown = showAll ? regions : topBottomSlice(regions, (r) => r.score, 5);
  const isLimited = !showAll && shown.length < regions.length;

  return (
    <>
      <table className="persona-segment-table">
        <caption className="report-visually-hidden">
          Bölge başına sentetik dikkat payı ve düşük/orta/yüksek etiketi
          {isLimited ? " (en yüksek ve en düşük 5 bölge gösteriliyor)" : ""}
        </caption>
        <thead>
          <tr>
            <th scope="col">Bölge</th>
            <th scope="col">Sentetik dikkat payı</th>
            <th scope="col">Seviye</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((region) => (
            <tr key={region.key}>
              <td>{region.label}</td>
              <td>{formatPercent(region.score)}</td>
              <td>{HEATMAP_LEVEL_LABELS[region.level]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!forceAll && isLimited && (
        <button type="button" className="btn-secondary" onClick={() => setShowAll(true)}>
          Tüm {regions.length} bölgeyi göster
        </button>
      )}
    </>
  );
}

function visualCellLevel(intensity: number): ReportHeatmapLevel {
  if (intensity < 0.34) return "low";
  if (intensity > 0.67) return "high";
  return "medium";
}

function VisualAttentionAccessibleTable({
  cells,
  forceAll = false,
}: {
  cells: ReportVisualAttentionCell[];
  forceAll?: boolean;
}) {
  const [showAll, setShowAll] = useState(forceAll);
  const indexed = cells.map((cell, index) => ({ cell, index }));
  const shown = showAll ? indexed : topBottomSlice(indexed, (item) => item.cell.intensity, 5);
  const isLimited = !showAll && shown.length < indexed.length;

  return (
    <>
      <table className="persona-segment-table">
        <caption className="report-visually-hidden">
          Piksel tabanlı sentetik dikkat grid hücreleri, konum ve düşük/orta/yüksek etiketi
          {isLimited ? " (en yüksek ve en düşük 5 hücre gösteriliyor)" : ""}
        </caption>
        <thead>
          <tr>
            <th scope="col">Hücre</th>
            <th scope="col">Konum (sol, üst)</th>
            <th scope="col">Tahmini yoğunluk</th>
            <th scope="col">Seviye</th>
          </tr>
        </thead>
        <tbody>
          {shown.map(({ cell, index }) => {
            const level = visualCellLevel(cell.intensity);
            return (
              <tr key={index}>
                <td>Hücre {index + 1}</td>
                <td>
                  %{Math.round(cell.x * 100)}, %{Math.round(cell.y * 100)}
                </td>
                <td>{formatPercent(cell.intensity)}</td>
                <td>{HEATMAP_LEVEL_LABELS[level]}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {!forceAll && isLimited && (
        <button type="button" className="btn-secondary" onClick={() => setShowAll(true)}>
          Tüm {indexed.length} hücreyi göster
        </button>
      )}
    </>
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

// Piksel-tabanli gercek OpenCV grid hucresi (screenshot/AI kaynagi) -
// bolge-tabanli `HeatmapRegionMarker`dan (5 isimli DOM bolgesi) BILEREK
// AYRI bir bilesendir: farkli veri sekli (yogunluk grid'i), etkilesimli
// DEGIL (48 hucreyi tek tek Tab ile gezmek erisilebilirligi bozar -
// erisilebilir alternatif VisualAttentionAccessibleTable'dir).
function VisualAttentionCellMarker({
  cell,
  layerVisible,
}: {
  cell: ReportVisualAttentionCell;
  layerVisible: boolean;
}) {
  const level = visualCellLevel(cell.intensity);
  const color = HEATMAP_LEVEL_COLORS[level];
  return (
    <div
      aria-hidden="true"
      className="heatmap-visual-cell"
      style={{
        left: `${cell.x * 100}%`,
        top: `${cell.y * 100}%`,
        width: `${cell.w * 100}%`,
        height: `${cell.h * 100}%`,
        backgroundColor: `rgba(${color}, ${0.15 + cell.intensity * 0.45})`,
        visibility: layerVisible ? "visible" : "hidden",
      }}
    />
  );
}

const CTA_CLASSIFICATION_STYLE_CLASS: Record<CtaOverlayClassification, string> = {
  dom_interactive_candidate: "cta-overlay-box--dom-candidate",
  visual_cta_candidate: "cta-overlay-box--visual-candidate",
  user_confirmed_cta: "cta-overlay-box--confirmed",
};

// CTA kutu onceligi: kullanicinin onayladigi CTA her zaman ilk sirada
// (bkz. plan §2 "CTA kalabalığını azalt" - "Kullanıcının CTA'sı her zaman
// öncelikli olsun"); digerleri skoruna gore azalan sirada.
function ctaPriorityRank(box: ReportCtaOverlayBox): number {
  if (box.classification === "user_confirmed_cta") return -1;
  return -(box.heuristic_score ?? -Infinity);
}

// Aralarindaki normalize merkez mesafesi kucuk olan kutulari coklama olarak
// gorup ilk (=en yuksek oncelikli, zaten siralanmis) kaydi tutar.
const CTA_DEDUPE_DISTANCE = 0.03;

function dedupeCtaBoxes(boxes: ReportCtaOverlayBox[]): ReportCtaOverlayBox[] {
  const sorted = [...boxes].sort((a, b) => ctaPriorityRank(a) - ctaPriorityRank(b));
  const kept: ReportCtaOverlayBox[] = [];
  for (const box of sorted) {
    const cx = box.x + box.w / 2;
    const cy = box.y + box.h / 2;
    const isDuplicate = kept.some((existing) => {
      const ecx = existing.x + existing.w / 2;
      const ecy = existing.y + existing.h / 2;
      return Math.abs(cx - ecx) < CTA_DEDUPE_DISTANCE && Math.abs(cy - ecy) < CTA_DEDUPE_DISTANCE;
    });
    if (!isDuplicate) kept.push(box);
  }
  return kept;
}

function ctaShortLabels(boxes: ReportCtaOverlayBox[]): Map<ReportCtaOverlayBox, string> {
  const labels = new Map<ReportCtaOverlayBox, string>();
  let candidateIndex = 0;
  for (const box of boxes) {
    if (box.classification === "user_confirmed_cta") {
      labels.set(box, "Sizin CTA'nız");
    } else {
      candidateIndex += 1;
      labels.set(box, `CTA adayı ${candidateIndex}`);
    }
  }
  return labels;
}

function CtaOverlayBoxMarker({
  box,
  shortLabel,
  layerVisible,
  focused,
  onFocus,
  onBlur,
}: {
  box: ReportCtaOverlayBox;
  shortLabel: string;
  layerVisible: boolean;
  focused: boolean;
  onFocus: () => void;
  onBlur: () => void;
}) {
  const scoreText =
    box.heuristic_score != null ? ` (aday sıralama skoru: ${box.heuristic_score.toFixed(2)})` : "";
  const accessibleLabel = `${shortLabel}: ${box.label}${scoreText}`;
  return (
    <button
      type="button"
      className={`cta-overlay-box ${CTA_CLASSIFICATION_STYLE_CLASS[box.classification]}${focused ? " cta-overlay-box--focused" : ""}`}
      style={{
        left: `${box.x * 100}%`,
        top: `${box.y * 100}%`,
        width: `${box.w * 100}%`,
        height: `${box.h * 100}%`,
        visibility: layerVisible ? "visible" : "hidden",
      }}
      aria-label={accessibleLabel}
      title={accessibleLabel}
      onFocus={onFocus}
      onBlur={onBlur}
      onMouseEnter={onFocus}
      onMouseLeave={onBlur}
    />
  );
}

const CTA_CLASSIFICATION_LEGEND: { classification: CtaOverlayClassification; hint: string }[] = [
  { classification: "dom_interactive_candidate", hint: "Sayfa yapısından tespit edilen etkileşimli öğe" },
  { classification: "visual_cta_candidate", hint: "Görsel piksel sezgisinden türetilen aday" },
  { classification: "user_confirmed_cta", hint: "Sizin seçtiğiniz/onayladığınız CTA" },
];

function CtaOverlayLegend({ boxes }: { boxes: ReportCtaOverlayBox[] }) {
  const present = new Set(boxes.map((b) => b.classification));
  const items = CTA_CLASSIFICATION_LEGEND.filter((item) => present.has(item.classification));
  if (items.length === 0) return null;
  return (
    <ul className="heatmap-legend">
      {items.map((item) => (
        <li key={item.classification} className="heatmap-legend__item">
          <span
            className={`cta-overlay-legend__swatch ${CTA_CLASSIFICATION_STYLE_CLASS[item.classification]}`}
            aria-hidden="true"
          />
          {item.hint}
        </li>
      ))}
    </ul>
  );
}

function CtaOverlayAccessibleList({
  boxes,
  shortLabels,
}: {
  boxes: ReportCtaOverlayBox[];
  shortLabels: Map<ReportCtaOverlayBox, string>;
}) {
  return (
    <ul className="report-findings">
      {boxes.map((box, index) => (
        <li key={index} className="report-finding report-finding--info">
          <span className={`cta-overlay-legend__swatch ${CTA_CLASSIFICATION_STYLE_CLASS[box.classification]}`} aria-hidden="true" />
          <span>
            <strong>{shortLabels.get(box) ?? box.label}</strong> — {box.label}
            {box.heuristic_score != null ? ` — aday sıralama skoru: ${box.heuristic_score.toFixed(2)}` : ""}
            {" — konum: sol %"}
            {Math.round(box.x * 100)}, üst %{Math.round(box.y * 100)}
          </span>
        </li>
      ))}
    </ul>
  );
}

// --- CTA gorunumu: dedupe + varsayilan olarak yalnizca kullanicinin
// onayladigi CTA + en yuksek skorlu ilk 3 aday gosterilir; "Tum adaylari
// goster" ile kalanlar acilir (bkz. plan §2 "CTA kalabalığını azalt").
function useCtaVisibleSet(boxes: ReportCtaOverlaySection["boxes"]) {
  const [showAll, setShowAll] = useState(false);
  const deduped = dedupeCtaBoxes(boxes);
  const confirmed = deduped.filter((b) => b.classification === "user_confirmed_cta");
  const candidates = deduped.filter((b) => b.classification !== "user_confirmed_cta");
  const defaultCandidates = candidates.slice(0, 3);
  const visible = showAll ? deduped : [...confirmed, ...defaultCandidates];
  const hiddenCount = deduped.length - visible.length;
  return { visible, allBoxes: deduped, showAll, setShowAll, hiddenCount, total: deduped.length };
}

function VisualReportPanel({
  heatmap,
  ctaOverlay,
  headingId,
  title,
  sourceType,
}: {
  heatmap: ReportHeatmapSection;
  ctaOverlay: ReportCtaOverlaySection;
  headingId: string;
  title: string;
  sourceType?: string | null;
}) {
  const [activeTab, setActiveTab] = useState<"visual" | "table">("visual");
  const [attentionLayerVisible, setAttentionLayerVisible] = useState(true);
  const [ctaLayerVisible, setCtaLayerVisible] = useState(true);
  const [focusedCtaIndex, setFocusedCtaIndex] = useState<number | null>(null);
  const idPrefix = useId();
  const tabVisualId = `${idPrefix}-tab-visual`;
  const tabTableId = `${idPrefix}-tab-table`;
  const panelVisualId = `${idPrefix}-panel-visual`;
  const panelTableId = `${idPrefix}-panel-table`;

  const isVisualOverlay = heatmap.overlay_kind === "synthetic_visual_attention";
  const visualCells = heatmap.visual_cells ?? [];
  const regions = deriveHeatmapRegions(heatmap.regions, heatmap.grid);
  const hasAttentionData = isVisualOverlay
    ? Boolean(heatmap.available && visualCells.length > 0)
    : Boolean(heatmap.available && heatmap.grid && heatmap.grid.length > 0);
  const hasCtaData = Boolean(ctaOverlay.available && ctaOverlay.boxes.length > 0);
  const hasAnyData = hasAttentionData || hasCtaData;

  const ctaVisible = useCtaVisibleSet(ctaOverlay.boxes);
  const ctaShortLabelMap = ctaShortLabels(ctaVisible.allBoxes);

  const screenshotUrl = heatmap.screenshot_url ?? ctaOverlay.screenshot_url ?? null;
  const imageAvailable = Boolean(screenshotUrl);
  const coordinatesUnavailableReason =
    heatmap.coordinates_unavailable_reason ?? ctaOverlay.coordinates_unavailable_reason ?? null;

  return (
    <section className="report-section" aria-labelledby={headingId}>
      <h3 id={headingId} className="report-section__heading">
        {title}
        {sourceType && (
          <>
            {" "}
            <SourceTypeBadge sourceType={sourceType} />
          </>
        )}
      </h3>

      {!hasAnyData && (
        <div className="report-heatmap-empty">
          <p>
            Gösterilecek sentetik dikkat tahmini veya CTA adayı bulunamadı
            {isVisualOverlay
              ? "."
              : ' (bu, DOM/URL kaynağında "Sentetik dikkat tahmini" modülü seçilmediğinde beklenen bir durumdur).'}
          </p>
          {!isVisualOverlay && <p>{HEATMAP_MODULE_SELECTION_HINT}</p>}
        </div>
      )}

      {hasAnyData && !imageAvailable && (
        <>
          <p className="report-heatmap-coords-note">
            {coordinatesUnavailableReason ??
              "Görsel katman için gerekli görsel/konum verisi bulunamadı; rastgele koordinat üretilmez, " +
                "bu nedenle yalnızca erişilebilir tablo/liste görünümü gösteriliyor."}
          </p>
          {hasAttentionData &&
            (isVisualOverlay ? (
              <VisualAttentionAccessibleTable cells={visualCells} />
            ) : (
              <HeatmapAccessibleTable regions={regions} />
            ))}
          {hasCtaData && (
            <CtaOverlayAccessibleList boxes={ctaVisible.visible} shortLabels={ctaShortLabelMap} />
          )}
        </>
      )}

      {hasAnyData && imageAvailable && (
        <>
          <div className="report-tabs" role="tablist" aria-label={`${title} görsel katman görünümü`}>
            <button
              type="button"
              role="tab"
              id={tabVisualId}
              aria-selected={activeTab === "visual"}
              aria-controls={panelVisualId}
              className={`report-tab${activeTab === "visual" ? " report-tab--active" : ""}`}
              onClick={() => setActiveTab("visual")}
            >
              Görsel görünüm
            </button>
            <button
              type="button"
              role="tab"
              id={tabTableId}
              aria-selected={activeTab === "table"}
              aria-controls={panelTableId}
              className={`report-tab${activeTab === "table" ? " report-tab--active" : ""}`}
              onClick={() => setActiveTab("table")}
            >
              Erişilebilir tablo
            </button>
          </div>

          {activeTab === "visual" && (
            <div role="tabpanel" id={panelVisualId} aria-labelledby={tabVisualId}>
              <div className="heatmap-layer-toggles">
                {hasAttentionData && (
                  <label className="heatmap-layer-toggle">
                    <input
                      type="checkbox"
                      checked={attentionLayerVisible}
                      onChange={(event) => setAttentionLayerVisible(event.target.checked)}
                    />
                    Sentetik dikkat katmanını göster
                  </label>
                )}
                {hasCtaData && (
                  <label className="heatmap-layer-toggle">
                    <input
                      type="checkbox"
                      checked={ctaLayerVisible}
                      onChange={(event) => setCtaLayerVisible(event.target.checked)}
                    />
                    CTA adaylarını göster
                  </label>
                )}
              </div>

              <div className="heatmap-visual-layout">
                <div className="heatmap-image-wrap">
                  <img
                    src={getReportHeatmapScreenshotUrl(screenshotUrl as string)}
                    alt={`${title}: sayfanın/tasarımın analiz anında alınmış güvenli görsel kopyası`}
                    className="heatmap-screenshot"
                  />
                  {hasAttentionData &&
                    (isVisualOverlay
                      ? visualCells.map((cell, index) => (
                          <VisualAttentionCellMarker
                            key={index}
                            cell={cell}
                            layerVisible={attentionLayerVisible}
                          />
                        ))
                      : regions.map((region) => (
                          <HeatmapRegionMarker
                            key={region.key}
                            region={region}
                            layerVisible={attentionLayerVisible}
                          />
                        )))}
                  {hasCtaData &&
                    ctaVisible.visible.map((box, index) => (
                      <CtaOverlayBoxMarker
                        key={index}
                        box={box}
                        shortLabel={ctaShortLabelMap.get(box) ?? box.label}
                        layerVisible={ctaLayerVisible}
                        focused={focusedCtaIndex === index}
                        onFocus={() => setFocusedCtaIndex(index)}
                        onBlur={() => setFocusedCtaIndex(null)}
                      />
                    ))}
                </div>
                <div className="heatmap-legends-stack">
                  {hasAttentionData && !isVisualOverlay && <HeatmapLegend />}
                  {hasCtaData && <CtaOverlayLegend boxes={ctaVisible.visible} />}
                </div>
              </div>

              {hasCtaData && ctaVisible.hiddenCount > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => ctaVisible.setShowAll(true)}
                >
                  Tüm adayları göster ({ctaVisible.total})
                </button>
              )}
            </div>
          )}

          {activeTab === "table" && (
            <div role="tabpanel" id={panelTableId} aria-labelledby={tabTableId}>
              {hasAttentionData &&
                (isVisualOverlay ? (
                  <VisualAttentionAccessibleTable cells={visualCells} />
                ) : (
                  <HeatmapAccessibleTable regions={regions} />
                ))}
              {hasCtaData && (
                <CtaOverlayAccessibleList boxes={ctaVisible.visible} shortLabels={ctaShortLabelMap} />
              )}
              {hasCtaData && ctaVisible.hiddenCount > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => ctaVisible.setShowAll(true)}
                >
                  Tüm adayları göster ({ctaVisible.total})
                </button>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function VisualComparisonTab({ report }: { report: ReportDetailResponse }) {
  const { ab_comparison: ab } = report;

  return (
    <div>
      <p className="report-section__intro">{HEATMAP_VISUAL_DISCLAIMER}</p>
      <p className="report-section__intro">{CTA_SHARED_DISCLAIMER}</p>

      {!ab ? (
        <VisualReportPanel
          heatmap={report.heatmap}
          ctaOverlay={report.cta_overlay}
          headingId="report-heatmap-heading"
          title="Tasarım"
          sourceType={report.info_box.input_summary.source_type}
        />
      ) : (
        <VisualComparisonAbPanels report={report} ab={ab} />
      )}
    </div>
  );
}

function VisualComparisonAbPanels({
  report,
  ab,
}: {
  report: ReportDetailResponse;
  ab: ReportAbComparison;
}) {
  const thisIsA = ab.this_variant_role === "variant_a";
  const aHeatmap = thisIsA ? report.heatmap : ab.sibling_heatmap;
  const bHeatmap = thisIsA ? ab.sibling_heatmap : report.heatmap;
  const aCtaOverlay = thisIsA ? report.cta_overlay : ab.sibling_cta_overlay;
  const bCtaOverlay = thisIsA ? ab.sibling_cta_overlay : report.cta_overlay;
  const aSourceType = thisIsA ? ab.this_source_type : ab.sibling_source_type;
  const bSourceType = thisIsA ? ab.sibling_source_type : ab.this_source_type;

  return (
    <>
      {ab.same_snapshot_sha256 && (
        <p className="auth-notice" role="status">
          Byte düzeyinde aynı snapshot karşılaştırılıyor: Tasarım A ve Tasarım B aynı görsel
          kopyaya bağlı. SHA-256 eşitliği yalnızca byte-düzeyinde özdeşliği gösterir; iki tarafın
          gerçekten aynı görsel içeriğe sahip olduğunun kanıtıdır (eşitsizlik ise görsel farklılığın
          kesin kanıtı sayılmaz).
        </p>
      )}
      <div className="report-ab-columns">
        <VisualReportPanel
          heatmap={aHeatmap}
          ctaOverlay={aCtaOverlay}
          headingId="report-heatmap-heading-a"
          title="Tasarım A"
          sourceType={aSourceType}
        />
        <VisualReportPanel
          heatmap={bHeatmap}
          ctaOverlay={bCtaOverlay}
          headingId="report-heatmap-heading-b"
          title="Tasarım B"
          sourceType={bSourceType}
        />
      </div>
    </>
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
        <div className="uncertainty-bar" key={cta.key}>
          <div className="uncertainty-bar__label-row">
            <span className="uncertainty-bar__label">
              {cta.label}
              {cta.above_fold ? " (ilk ekranda)" : ""}
            </span>
            <span className="uncertainty-bar__value">
              {formatPercent(cta.click_probability.point_estimate)} (
              {formatPercent(cta.click_probability.low)} – {formatPercent(cta.click_probability.high)})
            </span>
          </div>
        </div>
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

// --- Bulgular ve Oneriler sekmesi: onem sirasina gore gruplanir (Yuksek /
// Dusuk - backend'de su an yalnizca "warning"/"info" siddeti oldugu icin
// "Orta" grubu veri olmadan render edilmez).
function FindingsAndRecommendationsTab({ report }: { report: ReportDetailResponse }) {
  const findings = collectFindings(report);
  const high = findings.filter((f) => f.severity === "warning");
  const low = findings.filter((f) => f.severity === "info");

  const ab = report.ab_comparison;
  const designTag = ab
    ? ab.this_variant_role === "variant_a"
      ? "Tasarım A"
      : "Tasarım B"
    : report.variant_name;

  const renderGroup = (title: string, items: CombinedFinding[]) => {
    if (items.length === 0) return null;
    return (
      <div className="finding-priority-group">
        <h3 className="finding-priority-group__title">{title}</h3>
        <ul className="report-findings">
          {items.map((finding) => {
            const copy = FINDING_COPY[finding.key];
            return (
              <li key={finding.key} className="finding-detail-card">
                <p className="finding-detail-card__row">
                  <strong>Ne bulundu?</strong> {finding.text}
                </p>
                <p className="finding-detail-card__row">
                  <strong>Neden önemli?</strong>{" "}
                  {finding.severity === "warning"
                    ? "Bu tip bulgular sayfa deneyimini ve görevi tamamlama olasılığını olumsuz etkileyebilir."
                    : "Bilgi amaçlıdır; acil bir aksiyon gerekmeyebilir."}
                </p>
                <p className="finding-detail-card__row">
                  <strong>Ne yapılmalı?</strong>{" "}
                  {copy?.action ?? "Belirli bir öneri kaydedilmemiş; ilgili metrikleri gözden geçirin."}
                </p>
                <p className="finding-detail-card__row finding-detail-card__tag">
                  <strong>İlgili tasarım:</strong> {designTag}
                </p>
              </li>
            );
          })}
        </ul>
      </div>
    );
  };

  return (
    <div>
      {findings.length === 0 ? (
        <p className="report-section__intro">
          Tanımlı eşik tabanlı bir bulgu tetiklenmedi.
        </p>
      ) : (
        <>
          {renderGroup("Yüksek öncelik", high)}
          {renderGroup("Düşük öncelik", low)}
        </>
      )}
      <AiExplanationSection reportId={report.id} />
    </div>
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
          className="btn-secondary"
          onClick={handleGenerate}
          disabled={loading}
        >
          {loading ? "Üretiliyor…" : "Bulguları sade dille açıkla"}
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
            className="btn-secondary"
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

// --- Kontrast terminolojisi: kaynak screenshot/AI-uretilen (piksel
// sezgisi) ise kesin WCAG gecti/kaldi dili KULLANILMAZ (bkz. plan
// "Screenshot kontrast terminolojisi"); yalnizca DOM/URL kaynaginda gercek
// WCAG dili korunur. Backend `contrast_check` kendi icinde provenance
// tasimadigi icin `info_box.input_summary.source_type` proxy olarak
// kullanilir (screenshot/ai_generated -> pixel heuristic yol, bkz.
// page_analysis_adapter.adapt_visual_page_analysis).
function ContrastResultCard({ report }: { report: ReportDetailResponse }) {
  const { contrast_check: contrast } = report.metrics;
  const sourceType = report.info_box.input_summary.source_type;
  const isPixelHeuristic = sourceType === "screenshot" || sourceType === "ai_generated";

  if (isPixelHeuristic) {
    const level = contrast.avg_ratio >= contrast.threshold ? "Yüksek" : contrast.avg_ratio >= contrast.threshold * 0.75 ? "Orta" : "Düşük";
    return (
      <div className="result-metric">
        <span className="result-metric__label">Bölgesel görsel kontrast tahmini</span>
        <span className="result-metric__value">{level}</span>
        <span className="result-metric__range">
          Tahmini oran: {contrast.avg_ratio}. Bu değer screenshot piksellerinden tahmin edilmiştir;
          kesin WCAG uygunluk testi değildir.
        </span>
      </div>
    );
  }

  return (
    <div className="result-metric">
      <span className="result-metric__label">Kontrast kontrolü (WCAG AA)</span>
      <span className="result-metric__value">{contrast.pass ? "Geçti" : "Geçmedi"}</span>
      <span className="result-metric__range">
        Ortalama oran: {contrast.avg_ratio} (eşik: {contrast.threshold})
      </span>
    </div>
  );
}

function TechnicalDetailsTab({ report }: { report: ReportDetailResponse }) {
  const { metrics } = report;
  const ab = report.ab_comparison;

  return (
    <div>
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

      <section className="report-section" aria-labelledby="report-uncertainty-heading">
        <h2 id="report-uncertainty-heading" className="report-section__heading">
          Belirsizlik dağılımı açıklaması
        </h2>
        <p className="report-section__intro">
          Nokta tahminleri ile birlikte belirsizlik aralıkları (üçgen dağılım) gösterilir. Bu bir
          sentetik senaryo tahminidir; gerçek kullanıcı verisi değildir.
        </p>
        <div className="result-metric-grid">
          <div className="result-metric">
            <span className="result-metric__label">Okunabilirlik skoru</span>
            <span className="result-metric__value">{metrics.readability_score} / 100</span>
          </div>
          <ContrastResultCard report={report} />
        </div>
        <div className="report-visually-hidden">
          {report.accessible_chart_summaries.map((summary) => (
            <p key={summary.chart_key}>{summary.text}</p>
          ))}
        </div>
      </section>

      <PersonaSegmentsSection report={report} />

      {ab && (
        <section className="report-section" aria-labelledby="report-ab-technical-heading">
          <h2 id="report-ab-technical-heading" className="report-section__heading">
            A/B örneklem ayrıntısı
          </h2>
          <p className="methodology-note">
            {ab.note} Örneklenen sentetik persona sayısı — A:{" "}
            {ab.sampled_synthetic_persona_count.variant_a}, B:{" "}
            {ab.sampled_synthetic_persona_count.variant_b}. Kalibrasyon durumu:{" "}
            {ab.calibration_status}.
          </p>
        </section>
      )}

      <section className="report-section" aria-labelledby="report-technical-visual-heading">
        <h2 id="report-technical-visual-heading" className="report-section__heading">
          Tam görsel veri tabloları
        </h2>
        <TechnicalVisualDataPanel heatmap={report.heatmap} ctaOverlay={report.cta_overlay} title="Bu tasarım" />
        {ab && (
          <TechnicalVisualDataPanel
            heatmap={ab.sibling_heatmap}
            ctaOverlay={ab.sibling_cta_overlay}
            title={ab.sibling_variant_name}
          />
        )}
      </section>

      {report.campaign_cta && <CampaignCtaSection campaignCta={report.campaign_cta} />}
      {report.network_device && <NetworkDeviceSection networkDevice={report.network_device} />}

      <section className="report-section" aria-labelledby="report-methodology-heading">
        <h2 id="report-methodology-heading" className="report-section__heading">
          Metodoloji ve sınırlamalar
        </h2>
        <p className="methodology-note">{report.disclaimer}</p>
        <p className="methodology-note">Metodoloji: {report.methodology_reference}</p>
      </section>

      <div className="report-export-actions">
        <a className="btn-secondary" href={getReportExportUrl(report.export_json_url)}>
          JSON olarak dışa aktar
        </a>
        <a className="btn-secondary" href={getReportExportUrl(report.export_csv_url)}>
          CSV olarak dışa aktar
        </a>
      </div>
    </div>
  );
}

function TechnicalVisualDataPanel({
  heatmap,
  ctaOverlay,
  title,
}: {
  heatmap: ReportHeatmapSection;
  ctaOverlay: ReportCtaOverlaySection;
  title: string;
}) {
  const isVisualOverlay = heatmap.overlay_kind === "synthetic_visual_attention";
  const visualCells = heatmap.visual_cells ?? [];
  const regions = deriveHeatmapRegions(heatmap.regions, heatmap.grid);
  const hasAttentionData = isVisualOverlay
    ? Boolean(heatmap.available && visualCells.length > 0)
    : Boolean(heatmap.available && heatmap.grid && heatmap.grid.length > 0);
  const hasCtaData = Boolean(ctaOverlay.available && ctaOverlay.boxes.length > 0);
  const allBoxes = dedupeCtaBoxes(ctaOverlay.boxes);
  const shortLabelMap = ctaShortLabels(allBoxes);

  if (!hasAttentionData && !hasCtaData) return null;

  return (
    <div className="technical-visual-panel">
      <h3>{title}</h3>
      {hasAttentionData &&
        (isVisualOverlay ? (
          <VisualAttentionAccessibleTable cells={visualCells} forceAll />
        ) : (
          <HeatmapAccessibleTable regions={regions} forceAll />
        ))}
      {hasCtaData && <CtaOverlayAccessibleList boxes={allBoxes} shortLabels={shortLabelMap} />}
    </div>
  );
}

const TOP_LEVEL_TABS: TabDef[] = [
  { id: "summary", label: "Özet" },
  { id: "visual", label: "Görsel Karşılaştırma" },
  { id: "findings", label: "Bulgular ve Öneriler" },
  { id: "technical", label: "Teknik Detaylar" },
];

export default function ReportDetail() {
  const { reportId } = useParams<{ reportId: string }>();
  const [report, setReport] = useState<ReportDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [activeTab, setActiveTab] = useState("summary");
  const idPrefix = useId();

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

  const isAbReport = Boolean(report.ab_comparison);

  return (
    <section aria-labelledby="report-detail-heading">
      <nav className="report-breadcrumb" aria-label="Breadcrumb">
        <Link to="/raporlar">Raporlar</Link>
        <span aria-hidden="true">/</span>
        <Link to={`/projeler/${report.project_id}`}>{report.project_name}</Link>
        <span aria-hidden="true">/</span>
        <span>{report.test_definition_name}</span>
      </nav>

      <div className="report-header-row">
        <div>
          <h1 id="report-detail-heading" className="page-heading">
            {isAbReport ? "A/B Tasarım Karşılaştırması" : report.title}
          </h1>
          <p className="report-header-row__subtitle">{report.test_definition_name}</p>
          <div className="report-header-row__badges">
            {isAbReport && (
              <>
                <SourceTypeBadge
                  sourceType={
                    report.ab_comparison!.this_variant_role === "variant_a"
                      ? report.ab_comparison!.this_source_type
                      : report.ab_comparison!.sibling_source_type
                  }
                />
              </>
            )}
            <span className="report-header-row__date">
              Oluşturulma: {new Date(report.created_at).toLocaleString("tr-TR")}
            </span>
          </div>
        </div>
        <ExportMenu jsonUrl={report.export_json_url} csvUrl={report.export_csv_url} />
      </div>

      <p className="report-scientific-note">{SCIENTIFIC_DISCLAIMER}</p>

      <TopLevelTabs
        tabs={TOP_LEVEL_TABS}
        activeTab={activeTab}
        onChange={setActiveTab}
        idPrefix={idPrefix}
      />

      {activeTab === "summary" && (
        <div
          role="tabpanel"
          id={`${idPrefix}-panel-summary`}
          aria-labelledby={`${idPrefix}-tab-summary`}
        >
          <ResultSummaryCard report={report} />
          <TopFindingsSection report={report} />
          <QuickMetricComparison report={report} />
        </div>
      )}

      {activeTab === "visual" && (
        <div
          role="tabpanel"
          id={`${idPrefix}-panel-visual`}
          aria-labelledby={`${idPrefix}-tab-visual`}
        >
          <VisualComparisonTab report={report} />
        </div>
      )}

      {activeTab === "findings" && (
        <div
          role="tabpanel"
          id={`${idPrefix}-panel-findings`}
          aria-labelledby={`${idPrefix}-tab-findings`}
        >
          <FindingsAndRecommendationsTab report={report} />
        </div>
      )}

      {activeTab === "technical" && (
        <div
          role="tabpanel"
          id={`${idPrefix}-panel-technical`}
          aria-labelledby={`${idPrefix}-tab-technical`}
        >
          <TechnicalDetailsTab report={report} />
        </div>
      )}
    </section>
  );
}
