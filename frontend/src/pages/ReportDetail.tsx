import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  type AIPipelineStatusResponse,
  type CtaOverlayClassification,
  type ReportAbComparison,
  type ReportCampaignCta,
  type ReportCtaOverlayBox,
  type ReportCtaOverlaySection,
  type ReportDetailResponse,
  type ReportHeatmapLevel,
  type ReportHeatmapRegion,
  type ReportHeatmapSection,
  type ReportInteractionHeatmapHotspot,
  type ReportInteractionHeatmapSection,
  type ReportNetworkDevice,
  type ReportVisualAttentionCell,
  getAiPipelineStatus,
  getReport,
  getReportExportUrl,
  getReportHeatmapScreenshotUrl,
} from "../api/client";
import { calibrationStatusLabel, normalizeTurkishSystemCopy } from "../lib/turkishCopy";
import AiReportTab from "./AiReportTab";

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
      <h2 className="result-summary-card__title">
        Tasarımlar arasında sentetik metrik farkları var
      </h2>
      <p className="result-summary-card__body">
        {labelA} ve {labelB} için gösterilen sentetik tahminler bazı metriklerde farklı. Bu bir
        istatistiksel anlamlılık iddiası değildir; yalnızca simülasyon farkıdır ve tek başına bir
        tasarımı "kazanan" ilan etmez.
      </p>
      <ul className="result-summary-card__diff-list">
        {differing.map((key) => {
          const entry = ab.comparisons[key];
          const isPercent = key !== "task_duration_seconds";
          const format = (value: number) =>
            isPercent ? formatPercent(value) : formatSeconds(value);
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
    action:
      "Form alanlarını ve gezinme adımlarını azaltmayı, birincil CTA'yı ilk ekrana taşımayı değerlendirin.",
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
  too_many_ctas: {
    title: "CTA sayısını azaltın",
    action:
      "Birincil eylemi öne çıkarın; ikincil bağlantıları görsel olarak geri plana alarak karar yükünü azaltın.",
  },
  cta_not_above_fold: {
    title: "Birincil CTA'yı ilk ekranda gösterin",
    action: "Kullanıcının ana eylemi kaydırma yapmadan görebileceği bir konuma taşıyın.",
  },
  low_contrast: {
    title: "CTA kontrastını güçlendirin",
    action: "CTA metni, buton ve arka plan arasındaki görsel farkı artırın.",
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
  const seen = new Set<string>();
  return combined
    .map((finding) => ({
      ...finding,
      text: normalizeTurkishSystemCopy(finding.text),
    }))
    .filter((finding) => {
      if (finding.key === "no_threshold_triggered") return false;
      const identity = finding.key;
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    });
}

function TopFindingsSection({ report }: { report: ReportDetailResponse }) {
  const findings = collectFindings(report)
    .sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "warning" ? -1 : 1))
    .slice(0, 3);

  return (
    <section className="report-section" aria-labelledby="report-top-findings-heading">
      <h2 id="report-top-findings-heading" className="report-section__heading">
        Kritik bulgular ve öneriler
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
                <p className="priority-finding-card__action">
                  {copy?.action ?? "İlgili metrikleri gözden geçirin."}
                </p>
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
          {otherLabel ? ` ${otherLabel} için ayrıntılı aralık verisi bu görünümde yok.` : ""}
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
          const otherValue = ab
            ? thisIsA
              ? ab.comparisons[key].variant_b
              : ab.comparisons[key].variant_a
            : null;
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
  "Isı haritasında yeşil düşük, sarı artan, kırmızı ise en yüksek tahmini yoğunluğu gösterir. Sonuçlar gerçek kullanıcı tıklaması veya göz takibi değil; sayfa yapısından üretilen sentetik tahminlerdir.";

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

// Yesil (dusuk) -> sari/turuncu (orta) -> kirmizi (yuksek); renk TEK BASINA
// anlam tasimaz, her zaman metin etiketiyle (HEATMAP_LEVEL_LABELS) birlikte
// gosterilir (bkz. erisilebilirlik gereksinimi).
const HEATMAP_LEVEL_COLORS: Record<ReportHeatmapLevel, string> = {
  low: "34, 197, 94",
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

type AttentionMode = "click" | "gaze";

function HeatmapLegend({ mode }: { mode: AttentionMode }) {
  const levels: ReportHeatmapLevel[] = ["low", "medium", "high"];
  const suffix = mode === "click" ? "beklenen tıklama" : "görsel odak";
  return (
    <ul className="heatmap-legend">
      {levels.map((level) => (
        <li key={level} className="heatmap-legend__item">
          <span
            className="heatmap-legend__swatch"
            style={{ backgroundColor: `rgba(${HEATMAP_LEVEL_COLORS[level]}, 0.55)` }}
            aria-hidden="true"
          />
          {HEATMAP_LEVEL_LABELS[level]} {suffix}
        </li>
      ))}
    </ul>
  );
}

function HeatmapRegionMarker({
  region,
  mode,
}: {
  region: ReportHeatmapRegion;
  mode: AttentionMode;
}) {
  if (!region.box) return null;
  const color = HEATMAP_LEVEL_COLORS[region.level];
  const opacity = Math.min(0.92, 0.36 + region.score * 1.7);
  const isClick = mode === "click";
  // DOM kutuları çoğu zaman çok yatay ve incedir (ör. navigasyon veya buton).
  // Kutuyu doğrudan boyamak çizgi görünümü oluşturur. Merkez koordinatını
  // koruyup boyutu sınırlayarak örneklerdeki okunabilir, yumuşak sıcak noktayı üretiriz.
  const width = isClick
    ? Math.min(20, Math.max(8, region.box.width_pct * 1.8))
    : Math.min(38, Math.max(18, region.box.width_pct * 0.72));
  const height = isClick
    ? Math.min(6, Math.max(2.25, region.box.height_pct * 2.2))
    : Math.min(18, Math.max(9, region.box.height_pct * 4));
  const centerX = region.box.x_pct + region.box.width_pct / 2;
  const centerY = region.box.y_pct + region.box.height_pct / 2;
  const left = Math.max(0, Math.min(100 - width, centerX - width / 2));
  const top = Math.max(0, Math.min(100 - height, centerY - height / 2));
  const metricLabel = isClick ? "beklenen tıklama payı" : "görsel odak payı";
  const accessibleLabel = `${region.label}: ${metricLabel} %${Math.round(region.score * 100)} (${HEATMAP_LEVEL_LABELS[region.level]})`;
  return (
    <button
      type="button"
      className={`heatmap-region heatmap-region--${mode}`}
      style={{
        left: `${left}%`,
        top: `${top}%`,
        width: `${width}%`,
        height: `${height}%`,
        background: `radial-gradient(ellipse at center, rgba(${color}, ${opacity}) 0%, rgba(${color}, ${opacity * 0.9}) 28%, rgba(${color}, ${opacity * 0.58}) 58%, rgba(${color}, 0) 100%)`,
        borderColor: "transparent",
        boxShadow: `0 0 ${isClick ? 12 : 18}px rgba(${color}, ${region.level === "high" ? 0.9 : 0.68}), 0 0 ${isClick ? 28 : 42}px rgba(${color}, ${region.level === "high" ? 0.62 : 0.46})`,
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
function VisualAttentionCellMarker({ cell }: { cell: ReportVisualAttentionCell }) {
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
        background: `radial-gradient(ellipse at center, rgba(${color}, ${0.3 + cell.intensity * 0.66}) 0%, rgba(${color}, ${0.18 + cell.intensity * 0.46}) 42%, rgba(${color}, ${0.08 + cell.intensity * 0.24}) 68%, rgba(${color}, 0) 100%)`,
        boxShadow: `0 0 18px rgba(${color}, ${0.4 + cell.intensity * 0.42}), 0 0 34px rgba(${color}, ${0.24 + cell.intensity * 0.3})`,
      }}
    />
  );
}

function HeatmapInsights({
  regions,
  mode,
  ctaCount,
}: {
  regions: ReportHeatmapRegion[];
  mode: AttentionMode;
  ctaCount: number;
}) {
  const headingId = useId();
  const ranked = [...regions].sort((a, b) => b.score - a.score);
  const strongest = ranked[0];
  if (!strongest) return null;
  const hasStrongTaskSignal = strongest.level !== "low";
  const topTwoShare = ranked.slice(0, 2).reduce((sum, region) => sum + region.score, 0);
  const metric = mode === "click" ? "tıklama ilgisi" : "görsel odak";

  return (
    <section className="heatmap-insights" aria-labelledby={headingId}>
      <h4 id={headingId} className="heatmap-insights__heading">
        Harita ne anlatıyor?
      </h4>
      <div className="heatmap-insights__grid">
        <article className="heatmap-insight-card heatmap-insight-card--primary">
          <span className="heatmap-insight-card__eyebrow">
            {hasStrongTaskSignal ? "En güçlü sinyal" : "Sınırlı sinyal"}
          </span>
          <strong>
            {hasStrongTaskSignal ? strongest.label : "Görevle güçlü eşleşen alan bulunamadı"}
          </strong>
          <p>
            {hasStrongTaskSignal
              ? `Bu bölgenin göreli sentetik ${metric} payı %${Math.round(strongest.score * 100)}.`
              : "Gösterilen düşük sinyaller görev hedefiyle güçlü bir eşleşme kurmuyor."} Bu değer gerçek
            bir tıklanma veya bakış oranı değildir.
          </p>
        </article>
        <article className="heatmap-insight-card">
          <span className="heatmap-insight-card__eyebrow">Dağılım</span>
          <strong>
            {topTwoShare >= 0.55 ? "Belirli alanlarda yoğun" : "Sayfaya daha dengeli yayılmış"}
          </strong>
          <p>
            {topTwoShare >= 0.55
              ? `İlk iki bölge göreli sentetik ${metric} payının %${Math.round(topTwoShare * 100)} kadarını topluyor.`
              : "Tek bir bölge diğer tüm alanları baskılamıyor."}
          </p>
        </article>
        {ctaCount > 0 && (
          <article className="heatmap-insight-card heatmap-insight-card--cta">
            <span className="heatmap-insight-card__eyebrow">CTA kontrolü</span>
            <strong>{ctaCount} olası etkileşim alanı</strong>
            <p>Buton ve bağlantı adaylarını görmek için ayrı CTA katmanını açabilirsiniz.</p>
          </article>
        )}
      </div>
    </section>
  );
}

function HeatmapEvidenceSummary({
  mode,
  featureSource,
  hasConfirmedCta,
}: {
  mode: AttentionMode;
  featureSource?: string | null;
  hasConfirmedCta: boolean;
}) {
  const isClick = mode === "click";
  const source = isClick
    ? featureSource === "dom"
      ? "Sayfanın DOM yapısındaki buton ve bağlantılar"
      : "Ekran görüntüsündeki görsel etkileşim adayları"
    : "Ekran görüntüsündeki renk, kontrast ve yerleşim özellikleri";
  const method = isClick
    ? featureSource === "dom"
      ? "DOM tabanlı"
      : "Görsel sezgi"
    : "Görsel belirginlik";
  const reason = isClick
    ? featureSource === "dom"
      ? hasConfirmedCta
        ? "Etkileşimli öğeler DOM'dan bulundu ve kullanıcı seçimiyle desteklendi; gerçek olay verisiyle kalibre edilmedi."
        : "Etkileşimli öğeler DOM'dan bulundu; hangi öğenin gerçekten tıklanacağını gösteren olay verisi yok."
      : "Adaylar yalnızca görsel özelliklerden çıkarıldı; DOM ve gerçek olay verisi yok."
    : "Bu bir görsel belirginlik sezgisidir; gerçek göz takibi verisiyle kalibre edilmedi.";

  return (
    <div className="heatmap-evidence" role="note" aria-label="Analiz kaynağı ve yöntem">
      <span>
        <small>Kaynak</small>
        <strong>{source}</strong>
      </span>
      <span>
        <small>Yöntem</small>
        <strong>{method}</strong>
      </span>
      <p>{reason}</p>
    </div>
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

// Görselde her kutunun sol üstünde gösterilecek okunabilir numara. Kullanıcının
// onayladığı CTA numara yerine ✓ ile işaretlenir; adaylar 1'den başlayarak
// numaralandırılır (bkz. plan "CTA BÖLGELERİ" - "Kutunun sol üstünde CTA 1...").
function ctaBoxNumbers(boxes: ReportCtaOverlayBox[]): Map<ReportCtaOverlayBox, string> {
  const numbers = new Map<ReportCtaOverlayBox, string>();
  let candidateIndex = 0;
  for (const box of boxes) {
    if (box.classification === "user_confirmed_cta") {
      numbers.set(box, "✓");
    } else {
      candidateIndex += 1;
      numbers.set(box, String(candidateIndex));
    }
  }
  return numbers;
}

function CtaOverlayBoxMarker({
  box,
  shortLabel,
  ctaNumber,
  focused,
  onFocus,
  onBlur,
}: {
  box: ReportCtaOverlayBox;
  shortLabel: string;
  ctaNumber: string | null;
  focused: boolean;
  onFocus: () => void;
  onBlur: () => void;
}) {
  const scoreText =
    box.heuristic_score != null ? ` (aday sıralama skoru: ${box.heuristic_score.toFixed(2)})` : "";
  const accessibleLabel = `${shortLabel}: ${box.label}${scoreText}`;
  const badgeText = ctaNumber === "✓" ? "✓" : ctaNumber != null ? `CTA ${ctaNumber}` : shortLabel;
  return (
    <button
      type="button"
      className={`cta-overlay-box ${CTA_CLASSIFICATION_STYLE_CLASS[box.classification]}${focused ? " cta-overlay-box--focused" : ""}`}
      style={{
        left: `${box.x * 100}%`,
        top: `${box.y * 100}%`,
        width: `${box.w * 100}%`,
        height: `${box.h * 100}%`,
      }}
      aria-label={accessibleLabel}
      title={accessibleLabel}
      onFocus={onFocus}
      onBlur={onBlur}
      onMouseEnter={onFocus}
      onMouseLeave={onBlur}
    >
      <span className="cta-overlay-box__badge" aria-hidden="true">
        {badgeText}
      </span>
    </button>
  );
}

const CTA_CLASSIFICATION_LEGEND: { classification: CtaOverlayClassification; hint: string }[] = [
  {
    classification: "dom_interactive_candidate",
    hint: "Sayfa yapısından tespit edilen etkileşimli öğe",
  },
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
          <span
            className={`cta-overlay-legend__swatch ${CTA_CLASSIFICATION_STYLE_CLASS[box.classification]}`}
            aria-hidden="true"
          />
          <span>
            <strong>{shortLabels.get(box) ?? box.label}</strong> — {box.label}
            {box.heuristic_score != null
              ? ` — aday sıralama skoru: ${box.heuristic_score.toFixed(2)}`
              : ""}
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
  const [backgroundMuted, setBackgroundMuted] = useState(true);
  // CTA kutuları ısı haritasından farklı, sezgisel bir teknik katmandır. Ana
  // görseli kalabalıklaştırmaması için yalnızca kullanıcı isterse açılır.
  const [ctaLayerVisible, setCtaLayerVisible] = useState(false);
  const [focusedCtaIndex, setFocusedCtaIndex] = useState<number | null>(null);
  const idPrefix = useId();
  const tabVisualId = `${idPrefix}-tab-visual`;
  const tabTableId = `${idPrefix}-tab-table`;
  const panelVisualId = `${idPrefix}-panel-visual`;
  const panelTableId = `${idPrefix}-panel-table`;

  // "Görsel dikkat tahmini" görünümü YALNIZCA görsel odak/dikkat tahminini
  // gösterir. DOM/heuristik "Sentetik tıklama eğilimi" alt seçeneği kaldırıldı;
  // tıklama tahmini artık yalnızca "AI tıklama tahmini" görünümünde üretilir
  // (bkz. AiInteractionHeatmapPanel) ve onunla rekabet eden ikinci bir tıklama
  // haritası burada gösterilmez.
  const isVisualOverlay = heatmap.overlay_kind === "synthetic_visual_attention";
  const visualCells = heatmap.visual_cells ?? [];
  const gazeRegions = deriveHeatmapRegions(heatmap.regions, heatmap.grid);
  const hasGazeData = isVisualOverlay
    ? Boolean(heatmap.available && visualCells.length > 0)
    : Boolean(heatmap.available && heatmap.grid && heatmap.grid.length > 0);
  const regions = gazeRegions;
  const hasAttentionData = hasGazeData;
  const hasCtaData = Boolean(ctaOverlay.available && ctaOverlay.boxes.length > 0);
  const hasAnyData = hasAttentionData || hasCtaData;

  const ctaVisible = useCtaVisibleSet(ctaOverlay.boxes);
  const ctaShortLabelMap = ctaShortLabels(ctaVisible.allBoxes);
  const ctaNumberMap = ctaBoxNumbers(ctaVisible.allBoxes);

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
              <HeatmapAccessibleTable regions={gazeRegions} />
            ))}
          {hasCtaData && (
            <CtaOverlayAccessibleList boxes={ctaVisible.visible} shortLabels={ctaShortLabelMap} />
          )}
        </>
      )}

      {hasAnyData && imageAvailable && (
        <>
          <div
            className="report-tabs"
            role="tablist"
            aria-label={`${title} görsel katman görünümü`}
          >
            <button
              type="button"
              role="tab"
              id={tabVisualId}
              aria-selected={activeTab === "visual"}
              aria-controls={panelVisualId}
              className={`report-tab${activeTab === "visual" ? " report-tab--active" : ""}`}
              onClick={() => setActiveTab("visual")}
            >
              Isı haritası
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
              Yoğunluk tablosu
            </button>
          </div>

          {activeTab === "visual" && (
            <div role="tabpanel" id={panelVisualId} aria-labelledby={tabVisualId}>
              {hasAttentionData && (
                <div className="attention-mode-explanation" aria-live="polite">
                  <strong>Görsel dikkat tahmini</strong>
                  <span>
                    Renk, kontrast, boyut ve yerleşimin bakışı çekmesi beklenen alanları gösterir.
                    Tıklama tahmini için "AI tıklama tahmini" görünümünü kullanın.
                  </span>
                </div>
              )}
              <div className="heatmap-layer-toggles">
                {hasAttentionData && (
                  <label className="heatmap-layer-toggle">
                    <input
                      type="checkbox"
                      checked={backgroundMuted}
                      onChange={(event) => setBackgroundMuted(event.target.checked)}
                    />
                    Vurgular için arka planı soluklaştır
                  </label>
                )}
                {hasCtaData && (
                  <label className="heatmap-layer-toggle">
                    <input
                      type="checkbox"
                      checked={ctaLayerVisible}
                      onChange={(event) => setCtaLayerVisible(event.target.checked)}
                    />
                    CTA bölgelerini göster (buton ve bağlantı adayları)
                  </label>
                )}
              </div>

              {hasAttentionData && (
                <HeatmapEvidenceSummary
                  mode="gaze"
                  featureSource={heatmap.feature_source}
                  hasConfirmedCta={ctaOverlay.boxes.some(
                    (box) => box.classification === "user_confirmed_cta",
                  )}
                />
              )}
              {hasCtaData && ctaLayerVisible && (
                <p className="methodology-note">{CTA_SHARED_DISCLAIMER}</p>
              )}

              <div className="heatmap-visual-layout">
                <div className="heatmap-image-wrap">
                  <img
                    src={getReportHeatmapScreenshotUrl(screenshotUrl as string)}
                    alt={`${title}: sayfanın/tasarımın analiz anında alınmış güvenli görsel kopyası`}
                    className={`heatmap-screenshot${backgroundMuted ? " heatmap-screenshot--muted" : ""}`}
                  />
                  {hasAttentionData && (
                    <div
                      className="heatmap-base-layer heatmap-base-layer--gaze"
                      aria-hidden="true"
                      title="Sayfanın düşük yoğunluklu temel alanı"
                    />
                  )}
                  {/* Katman sırası: ekran görüntüsü → ısı haritası → CTA sınırları → etiketler */}
                  {hasAttentionData &&
                    (isVisualOverlay
                      ? visualCells.map((cell, index) => (
                          <VisualAttentionCellMarker key={index} cell={cell} />
                        ))
                      : gazeRegions.map((region) => (
                          <HeatmapRegionMarker key={region.key} region={region} mode="gaze" />
                        )))}
                  {hasCtaData &&
                    ctaLayerVisible &&
                    ctaVisible.visible.map((box, index) => (
                      <CtaOverlayBoxMarker
                        key={index}
                        box={box}
                        shortLabel={ctaShortLabelMap.get(box) ?? box.label}
                        ctaNumber={ctaNumberMap.get(box) ?? null}
                        focused={focusedCtaIndex === index}
                        onFocus={() => setFocusedCtaIndex(index)}
                        onBlur={() => setFocusedCtaIndex(null)}
                      />
                    ))}
                </div>
                <div className="heatmap-legends-stack">
                  {hasAttentionData && !isVisualOverlay && <HeatmapLegend mode="gaze" />}
                  {hasCtaData && ctaLayerVisible && <CtaOverlayLegend boxes={ctaVisible.visible} />}
                </div>
              </div>

              {hasCtaData && ctaLayerVisible && ctaVisible.hiddenCount > 0 && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => ctaVisible.setShowAll(true)}
                >
                  Tüm adayları göster ({ctaVisible.total})
                </button>
              )}
              <HeatmapInsights
                regions={regions}
                mode="gaze"
                ctaCount={ctaVisible.total}
              />
            </div>
          )}

          {activeTab === "table" && (
            <div role="tabpanel" id={panelTableId} aria-labelledby={tabTableId}>
              {hasAttentionData &&
                (isVisualOverlay ? (
                  <VisualAttentionAccessibleTable cells={visualCells} />
                ) : (
                  <HeatmapAccessibleTable regions={gazeRegions} />
                ))}
              {hasCtaData && (
                <CtaOverlayAccessibleList
                  boxes={ctaVisible.visible}
                  shortLabels={ctaShortLabelMap}
                />
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

const AI_INTERACTION_HEATMAP_DISCLAIMER =
  "Bu harita hedef görev, hedef kitle ve sayfadaki gerçek etkileşim alanları kullanılarak oluşturulan sentetik bir AI tahminidir. Gerçek kullanıcı tıklaması veya göz takibi verisi değildir.";

const AI_HEATMAP_CONFIDENCE_LABELS: Record<ReportInteractionHeatmapHotspot["confidence"], string> = {
  high: "Yüksek güven",
  medium: "Orta güven",
  low: "Düşük güven",
};

const AI_HEATMAP_RELEVANCE_LABELS: Record<ReportInteractionHeatmapHotspot["task_relevance"], string> = {
  direct: "Görevle doğrudan eşleşiyor",
  related: "Görevle ilişkili",
  weak: "Görevle zayıf ilişkili",
  none: "Görevle eşleşmiyor",
};

// AI olasılık skoru -> yüksek (kırmızı) / orta (turuncu-sarı) / düşük (yeşil).
// Renk TEK BAŞINA anlam taşımaz; her hotspot kartında sayısal skor + güven +
// görev ilişkisi metinle de gösterilir.
function aiHotspotLevel(score: number): ReportHeatmapLevel {
  if (score >= 0.66) return "high";
  if (score >= 0.4) return "medium";
  return "low";
}

// Görsel analiz gerçekten YAPILDI mı? Yöntem "openai_vision_candidate_selection"
// ise model temiz görüntü + numaralı overlay ile analiz etti; "openai_candidate_
// selection" ise model görüntüyü desteklemedi (yalnızca DOM/çıkarılmış özellik) -
// bu durumda "görsel analiz edildi" DENMEZ.
function aiVisionUsed(section: ReportInteractionHeatmapSection | undefined): boolean {
  return section?.method === "openai_vision_candidate_selection";
}

// Sağlayıcı hatasının ham içeriği kullanıcıya gösterilmez; yalnızca güvenli,
// tiplenmiş `error_code` -> anlaşılır bir duruma eşlenir (queued/running/
// succeeded/empty/failed/provider_unavailable/unsupported_vision_model/
// invalid_provider_output).
type AiHeatmapState =
  | "not_requested"
  | "queued"
  | "running"
  | "succeeded"
  | "empty"
  | "failed"
  | "provider_unavailable"
  | "invalid_provider_output"
  | "invalid_target_task";

const AI_PROVIDER_UNAVAILABLE_CODES = new Set([
  "provider_unavailable",
  "provider_authentication_error",
  "provider_permission_error",
  "provider_configuration",
  "provider_invalid_request",
]);
const AI_INVALID_OUTPUT_CODES = new Set(["provider_invalid_output", "output_validation_failed"]);

function resolveAiHeatmapState(section: ReportInteractionHeatmapSection | undefined): AiHeatmapState {
  if (!section || !section.requested) return "not_requested";
  if (section.status === "queued") return "queued";
  if (section.status === "running") return "running";
  if (section.status === "succeeded") {
    return (section.hotspots?.length ?? 0) > 0 ? "succeeded" : "empty";
  }
  if (section.status === "failed") {
    const code = section.error_code ?? "";
    if (code === "invalid_target_task" || code === "missing_target_task")
      return "invalid_target_task";
    if (AI_INVALID_OUTPUT_CODES.has(code)) return "invalid_provider_output";
    if (AI_PROVIDER_UNAVAILABLE_CODES.has(code)) return "provider_unavailable";
    return "failed";
  }
  return "not_requested";
}

function AiInteractionHotspotMarker({
  hotspot,
  rank,
  selected,
  onSelect,
}: {
  hotspot: ReportInteractionHeatmapHotspot;
  rank: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const level = aiHotspotLevel(hotspot.score);
  const color = HEATMAP_LEVEL_COLORS[level];
  // Nokta boyutu/opaklığı AI skoruna göre değişir; küçük butonlar da görünür
  // kalması için minimum boyut uygulanır. Yüzde tabanlı olduğu için görsel
  // yeniden boyutlandığında koordinatlar doğru ölçeklenir.
  const sizeBoost = 0.9 + hotspot.score * 0.6;
  const width = Math.min(34, Math.max(6, hotspot.w * 100 * sizeBoost));
  const height = Math.min(26, Math.max(5, hotspot.h * 100 * (sizeBoost + 0.5)));
  const centerX = (hotspot.x + hotspot.w / 2) * 100;
  const centerY = (hotspot.y + hotspot.h / 2) * 100;
  const left = Math.max(0, Math.min(100 - width, centerX - width / 2));
  const top = Math.max(0, Math.min(100 - height, centerY - height / 2));
  const opacity = Math.min(0.95, 0.5 + hotspot.score * 0.5);
  const label =
    `AI hotspot ${rank}: ${hotspot.label} — olasılık skoru %${Math.round(hotspot.score * 100)}, ` +
    `${AI_HEATMAP_CONFIDENCE_LABELS[hotspot.confidence]}, ${AI_HEATMAP_RELEVANCE_LABELS[hotspot.task_relevance]}`;
  return (
    <button
      type="button"
      className={`ai-interaction-hotspot ai-interaction-hotspot--${level}${selected ? " ai-interaction-hotspot--selected" : ""}`}
      style={{
        left: `${left}%`,
        top: `${top}%`,
        width: `${width}%`,
        height: `${height}%`,
        background: `radial-gradient(ellipse at center, rgba(${color}, ${opacity}) 0%, rgba(${color}, ${opacity * 0.72}) 42%, rgba(${color}, ${opacity * 0.34}) 70%, rgba(${color}, 0) 100%)`,
        boxShadow: `0 0 14px rgba(${color}, ${opacity}), 0 0 30px rgba(${color}, ${opacity * 0.6})`,
      }}
      aria-label={label}
      aria-pressed={selected}
      title={label}
      onClick={onSelect}
    >
      <span className="ai-interaction-hotspot__rank" aria-hidden="true">
        {rank}
      </span>
    </button>
  );
}

function AiInteractionHeatmapPanel({
  section,
  ctaOverlay,
  headingId,
  title,
  sourceType,
}: {
  section: ReportInteractionHeatmapSection | undefined;
  ctaOverlay?: ReportCtaOverlaySection;
  headingId: string;
  title: string;
  sourceType?: string | null;
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [ctaLayerVisible, setCtaLayerVisible] = useState(false);
  const [focusedCtaIndex, setFocusedCtaIndex] = useState<number | null>(null);

  const state = resolveAiHeatmapState(section);
  const hotspots = section?.hotspots ?? [];
  const hasMap = Boolean(
    state === "succeeded" &&
      section?.available &&
      section.coordinates_available &&
      section.screenshot_url &&
      hotspots.length > 0,
  );
  const visionUsed = aiVisionUsed(section);

  const ctaBoxes = ctaOverlay?.boxes ?? [];
  const ctaVisible = useCtaVisibleSet(ctaBoxes);
  const ctaNumberMap = ctaBoxNumbers(ctaVisible.allBoxes);
  const ctaShortLabelMap = ctaShortLabels(ctaVisible.allBoxes);
  const hasCtaData = Boolean(ctaOverlay?.available && ctaBoxes.length > 0 && ctaOverlay?.screenshot_url);

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

      <p className="report-section__intro report-disclaimer-strong">
        {normalizeTurkishSystemCopy(section?.disclaimer ?? AI_INTERACTION_HEATMAP_DISCLAIMER)}
      </p>

      {(section?.analysis_limited || section?.analysis_mode === "lite") && (
        <p className="report-analysis-lite-notice" role="note">
          Bu sayfa kaynak kullanımını azaltmak için hafif analiz modunda incelendi. Sonuçlar
          görünür ekran alanını (ilk ekran) temel alır.
        </p>
      )}

      {state === "not_requested" && (
        <div className="report-heatmap-empty">
          <p>Bu çalışma için AI tıklama tahmini modülü seçilmedi.</p>
        </div>
      )}

      {(state === "queued" || state === "running") && (
        <p className="auth-notice" role="status">
          {state === "queued"
            ? "AI tıklama tahmini sıraya alındı."
            : "AI görsel ve etkileşim alanlarını değerlendiriyor."}{" "}
          {section?.status_note && normalizeTurkishSystemCopy(section.status_note)}
        </p>
      )}

      {state === "provider_unavailable" && (
        <p className="auth-error" role="alert">
          AI tıklama tahmini şu anda üretilemiyor: yapay zeka sağlayıcısına ulaşılamadı veya
          yapılandırma tamamlanmadı. Deterministik bir sonuç AI çıktısı gibi gösterilmedi.
        </p>
      )}

      {state === "invalid_provider_output" && (
        <p className="auth-error" role="alert">
          AI tıklama tahmini üretilemedi: sağlayıcı çıktısı doğrulama kurallarını karşılamadı
          (yalnızca gerçek aday alanları kabul edilir). Deterministik bir sonuç AI çıktısı gibi
          gösterilmedi.
        </p>
      )}

      {state === "invalid_target_task" && (
        <p className="auth-error" role="alert">
          AI tıklama tahmini üretilemedi: bu test için tanımlı hedef görev, kullanıcının ne
          yapacağını açıklayan anlamlı bir ifade değil. Testi düzenleyip hedef görevi netleştirin
          (örn. “Kırmızı spor ayakkabıyı bul ve sepete ekle”), ardından yeniden başlatın.
        </p>
      )}

      {state === "failed" && (
        <p className="auth-error" role="alert">
          AI tıklama tahmini bu çalışma için üretilemedi. Deterministik bir sonuç AI çıktısı gibi
          gösterilmedi. Yeni bir test ile yeniden deneyebilirsiniz.
        </p>
      )}

      {state === "empty" && (
        <>
          {section?.summary && (
            <p className="report-section__intro">{normalizeTurkishSystemCopy(section.summary)}</p>
          )}
          <p className="auth-notice" role="status">
            {normalizeTurkishSystemCopy(
              section?.unmatched_task_warning ??
                "Görevle güçlü eşleşen bir etkileşim alanı bulunamadı.",
            )}
          </p>
          {section?.method_label && (
            <p className="methodology-note">
              Yöntem: {normalizeTurkishSystemCopy(section.method_label)}
              {section.model_name ? ` · Model: ${section.model_name}` : ""}
            </p>
          )}
        </>
      )}

      {state === "succeeded" && (
        <>
          {section?.summary && (
            <p className="report-section__intro">
              {normalizeTurkishSystemCopy(section.summary)} AI’ın hedef görev için daha olası gördüğü
              etkileşim alanları aşağıda gösterilir.
            </p>
          )}
          {!visionUsed && section?.provider === "openai" && (
            <p className="auth-notice" role="status">
              Kullanılan model görüntü girdisini desteklemedi; sonuç yalnızca DOM ve çıkarılmış
              görsel özelliklerden üretildi (sayfa görüntüsü görsel olarak analiz edilmedi).
            </p>
          )}

          {hasMap && (
            <>
              {hasCtaData && (
                <div className="heatmap-layer-toggles">
                  <label className="heatmap-layer-toggle">
                    <input
                      type="checkbox"
                      checked={ctaLayerVisible}
                      onChange={(event) => setCtaLayerVisible(event.target.checked)}
                    />
                    CTA bölgelerini göster (buton ve bağlantı adayları)
                  </label>
                </div>
              )}
              <div className="heatmap-visual-layout">
                <div className="heatmap-image-wrap">
                  {/* Katman sırası: ekran görüntüsü → ısı haritası → CTA sınırları → etiketler */}
                  <img
                    src={getReportHeatmapScreenshotUrl(section!.screenshot_url as string)}
                    alt={`${title}: AI'ın hedef görev için daha olası gördüğü gerçek etkileşim alanları`}
                    className="heatmap-screenshot heatmap-screenshot--muted"
                  />
                  <div className="ai-interaction-base-layer" aria-hidden="true" />
                  {hotspots.map((hotspot, index) => (
                    <AiInteractionHotspotMarker
                      key={hotspot.candidate_id}
                      hotspot={hotspot}
                      rank={index + 1}
                      selected={selectedId === hotspot.candidate_id}
                      onSelect={() =>
                        setSelectedId((current) =>
                          current === hotspot.candidate_id ? null : hotspot.candidate_id,
                        )
                      }
                    />
                  ))}
                  {hasCtaData &&
                    ctaLayerVisible &&
                    ctaVisible.allBoxes.map((box, index) => (
                      <CtaOverlayBoxMarker
                        key={index}
                        box={box}
                        shortLabel={ctaShortLabelMap.get(box) ?? box.label}
                        ctaNumber={ctaNumberMap.get(box) ?? null}
                        focused={focusedCtaIndex === index}
                        onFocus={() => setFocusedCtaIndex(index)}
                        onBlur={() => setFocusedCtaIndex(null)}
                      />
                    ))}
                </div>
                <div className="heatmap-legends-stack">
                  <ul className="heatmap-legend">
                    {(["high", "medium", "low"] as ReportHeatmapLevel[]).map((level) => (
                      <li key={level} className="heatmap-legend__item">
                        <span
                          className="heatmap-legend__swatch"
                          style={{ backgroundColor: `rgba(${HEATMAP_LEVEL_COLORS[level]}, 0.6)` }}
                          aria-hidden="true"
                        />
                        {HEATMAP_LEVEL_LABELS[level]} AI olasılığı
                      </li>
                    ))}
                  </ul>
                  {hasCtaData && ctaLayerVisible && <CtaOverlayLegend boxes={ctaVisible.allBoxes} />}
                </div>
              </div>
              {hasCtaData && ctaLayerVisible && (
                <p className="methodology-note">{CTA_SHARED_DISCLAIMER}</p>
              )}
            </>
          )}

          {hotspots.length > 0 && (
            <div className="ai-interaction-hotspot-cards" aria-label="AI tıklama tahmini açıklamaları">
              {hotspots.map((hotspot, index) => (
                <article
                  className={`ai-interaction-hotspot-card${selectedId === hotspot.candidate_id ? " ai-interaction-hotspot-card--selected" : ""}`}
                  key={hotspot.candidate_id}
                  aria-current={selectedId === hotspot.candidate_id ? "true" : undefined}
                >
                  <button
                    type="button"
                    className="ai-interaction-hotspot-card__select"
                    onClick={() =>
                      setSelectedId((current) =>
                        current === hotspot.candidate_id ? null : hotspot.candidate_id,
                      )
                    }
                    aria-pressed={selectedId === hotspot.candidate_id}
                  >
                    <span className="heatmap-insight-card__eyebrow">Öncelik {index + 1}</span>
                    <h4>{normalizeTurkishSystemCopy(hotspot.label)}</h4>
                  </button>
                  <p>{normalizeTurkishSystemCopy(hotspot.reason)}</p>
                  <dl>
                    <div>
                      <dt>AI olasılık skoru</dt>
                      <dd>%{Math.round(hotspot.score * 100)}</dd>
                    </div>
                    <div>
                      <dt>Güven</dt>
                      <dd>{AI_HEATMAP_CONFIDENCE_LABELS[hotspot.confidence]}</dd>
                    </div>
                    <div>
                      <dt>Görev ilişkisi</dt>
                      <dd>{AI_HEATMAP_RELEVANCE_LABELS[hotspot.task_relevance]}</dd>
                    </div>
                    <div>
                      <dt>Konum</dt>
                      <dd>{hotspot.above_fold ? "İlk ekranda" : "İlk ekranın altında"}</dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          )}

          {section?.method_label && (
            <p className="methodology-note">
              Yöntem: {normalizeTurkishSystemCopy(section.method_label)}
              {section.model_name ? ` · Model: ${section.model_name}` : ""}
            </p>
          )}
        </>
      )}
    </section>
  );
}

function VisualComparisonTab({ report }: { report: ReportDetailResponse }) {
  const { ab_comparison: ab } = report;
  const hasAiInteractionHeatmap = Boolean(
    report.interaction_heatmap_requested ||
      report.interaction_heatmap?.requested ||
      ab?.sibling_interaction_heatmap?.requested,
  );
  const [view, setView] = useState<"ai" | "visual">(
    hasAiInteractionHeatmap ? "ai" : "visual",
  );
  const viewPrefix = useId();

  return (
    <div>
      {hasAiInteractionHeatmap && (
        <div className="report-tabs" role="tablist" aria-label="Isı haritası görünümü">
          <button
            type="button"
            role="tab"
            id={`${viewPrefix}-tab-ai`}
            aria-selected={view === "ai"}
            aria-controls={`${viewPrefix}-panel-ai`}
            className={`report-tab${view === "ai" ? " report-tab--active" : ""}`}
            onClick={() => setView("ai")}
          >
            AI tıklama tahmini
          </button>
          <button
            type="button"
            role="tab"
            id={`${viewPrefix}-tab-visual`}
            aria-selected={view === "visual"}
            aria-controls={`${viewPrefix}-panel-visual`}
            className={`report-tab${view === "visual" ? " report-tab--active" : ""}`}
            onClick={() => setView("visual")}
          >
            Görsel dikkat tahmini
          </button>
        </div>
      )}

      {hasAiInteractionHeatmap && view === "ai" && (
        <div
          role="tabpanel"
          id={`${viewPrefix}-panel-ai`}
          aria-labelledby={`${viewPrefix}-tab-ai`}
        >
          {!ab ? (
            <AiInteractionHeatmapPanel
              section={report.interaction_heatmap}
              ctaOverlay={report.cta_overlay}
              headingId="report-ai-interaction-heatmap-heading"
              title="Tasarım"
              sourceType={report.info_box.input_summary.source_type}
            />
          ) : (
            <div className="report-ab-columns">
              <AiInteractionHeatmapPanel
                section={
                  ab.this_variant_role === "variant_a"
                    ? report.interaction_heatmap
                    : ab.sibling_interaction_heatmap
                }
                ctaOverlay={
                  ab.this_variant_role === "variant_a"
                    ? report.cta_overlay
                    : ab.sibling_cta_overlay
                }
                headingId="report-ai-interaction-heatmap-heading-a"
                title="Tasarım A"
                sourceType={
                  ab.this_variant_role === "variant_a"
                    ? ab.this_source_type
                    : ab.sibling_source_type
                }
              />
              <AiInteractionHeatmapPanel
                section={
                  ab.this_variant_role === "variant_a"
                    ? ab.sibling_interaction_heatmap
                    : report.interaction_heatmap
                }
                ctaOverlay={
                  ab.this_variant_role === "variant_a"
                    ? ab.sibling_cta_overlay
                    : report.cta_overlay
                }
                headingId="report-ai-interaction-heatmap-heading-b"
                title="Tasarım B"
                sourceType={
                  ab.this_variant_role === "variant_a"
                    ? ab.sibling_source_type
                    : ab.this_source_type
                }
              />
            </div>
          )}
        </div>
      )}

      {(!hasAiInteractionHeatmap || view === "visual") && (
        <div
          role={hasAiInteractionHeatmap ? "tabpanel" : undefined}
          id={hasAiInteractionHeatmap ? `${viewPrefix}-panel-visual` : undefined}
          aria-labelledby={hasAiInteractionHeatmap ? `${viewPrefix}-tab-visual` : undefined}
        >
          <p className="report-section__intro">{HEATMAP_VISUAL_DISCLAIMER}</p>
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
          Byte düzeyinde aynı snapshot karşılaştırılıyor: Tasarım A ve Tasarım B aynı görsel kopyaya
          bağlı. SHA-256 eşitliği yalnızca byte-düzeyinde özdeşliği gösterir; iki tarafın gerçekten
          aynı görsel içeriğe sahip olduğunun kanıtıdır (eşitsizlik ise görsel farklılığın kesin
          kanıtı sayılmaz).
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
              {formatPercent(cta.click_probability.low)} –{" "}
              {formatPercent(cta.click_probability.high)})
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
  const findings = collectFindings(report).sort((a, b) =>
    a.severity === b.severity ? 0 : a.severity === "warning" ? -1 : 1,
  );

  return (
    <div>
      {findings.length === 0 ? (
        <p className="report-section__intro">Tanımlı eşik tabanlı bir bulgu tetiklenmedi.</p>
      ) : (
        <section className="report-section" aria-labelledby="findings-action-heading">
          <h2 id="findings-action-heading" className="report-section__heading">
            Ne bulduk, ne yapmalısınız?
          </h2>
          <p className="report-section__intro">
            Her kart tek bir bulguyu ve ona karşılık gelen önerilen düzeltmeyi birlikte gösterir.
          </p>
          <ul className="report-findings">
            {findings.map((finding) => {
              const copy = FINDING_COPY[finding.key];
              return (
                <li key={`${finding.key}:${finding.text}`} className="finding-detail-card">
                  <span className={`finding-priority finding-priority--${finding.severity}`}>
                    {finding.severity === "warning" ? "Öncelikli" : "İyileştirme fırsatı"}
                  </span>
                  <h3 className="finding-detail-card__title">{copy?.title ?? finding.text}</h3>
                  {copy?.title && <p className="finding-detail-card__row">{finding.text}</p>}
                  <p className="finding-detail-card__action">
                    <strong>Önerilen adım:</strong>{" "}
                    {copy?.action ??
                      "İlgili metriği inceleyip küçük bir tasarım deneyiyle doğrulayın."}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
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
    const level =
      contrast.avg_ratio >= contrast.threshold
        ? "Yüksek"
        : contrast.avg_ratio >= contrast.threshold * 0.75
          ? "Orta"
          : "Düşük";
    return (
      <div className="result-metric">
        <span className="result-metric__label">Bölgesel görsel kontrast tahmini</span>
        <span className="result-metric__value">{level}</span>
        <span className="result-metric__range">
          Tahmini oran: {contrast.avg_ratio}. Bu değer ekran görüntüsü piksellerinden tahmin
          edilmiştir; kesin WCAG uygunluk testi değildir.
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
            <dd>{calibrationStatusLabel(report.info_box.calibration_status)}</dd>
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
          Nokta tahminleriyle birlikte üçgen dağılım yöntemiyle hesaplanan belirsizlik aralıkları
          gösterilir.
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
            {calibrationStatusLabel(ab.calibration_status)}.
          </p>
        </section>
      )}

      <section className="report-section" aria-labelledby="report-technical-visual-heading">
        <h2 id="report-technical-visual-heading" className="report-section__heading">
          Tam görsel veri tabloları
        </h2>
        <TechnicalVisualDataPanel
          heatmap={report.heatmap}
          ctaOverlay={report.cta_overlay}
          title="Bu tasarım"
        />
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
  { id: "visual", label: "Isı Haritası" },
  { id: "findings", label: "Bulgular ve Öneriler" },
  { id: "technical", label: "Teknik Detaylar" },
];

const AI_REPORT_TAB_ID = "ai-report";

// AI pipeline sondasi: `ai_report` modulu secilmemis run'larda pipeline YOKTUR
// (kontrollu 404) -> sekme hic gosterilmez. Basarili sonda -> sekme + ilk durum.
// 404 DISI gercek hata (ag/500/integrity) -> sekme gosterilir ama guvenli bir
// hata durumuyla acilir (bkz. AiReportTab); boylece kontrollu 404, gercek
// hatadan ayrilir ve normal rapor davranisi hicbir durumda degismez.
type AiProbe =
  | { state: "loading" }
  | { state: "absent" }
  | { state: "present"; status: AIPipelineStatusResponse }
  | { state: "error" };

export default function ReportDetail() {
  const { reportId } = useParams<{ reportId: string }>();
  const [report, setReport] = useState<ReportDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [activeTab, setActiveTab] = useState("summary");
  const [aiProbe, setAiProbe] = useState<AiProbe>({ state: "loading" });
  const idPrefix = useId();

  const loadReport = useCallback(() => {
    if (!reportId) return;
    setError(null);
    setNotFound(false);
    return getReport(reportId)
      .then((data) => setReport(data))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError("Rapor yüklenemedi.");
        }
      });
  }, [reportId]);

  useEffect(() => {
    void loadReport();
  }, [loadReport]);

  // Rapor yuklendikten sonra, ait oldugu SimulationRun icin bir AI pipeline
  // olup olmadigini TEK sefer sonda ile belirle (sekme gorunurlugu icin).
  // Bu, standart rapor yuklemesinden BAGIMSIZDIR: sonda basarisiz olsa bile
  // normal rapor davranisi degismez.
  const runId = report?.simulation_run_id;
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setAiProbe({ state: "loading" });
    getAiPipelineStatus(runId)
      .then((status) => {
        if (!cancelled) setAiProbe({ state: "present", status });
      })
      .catch((err) => {
        if (cancelled) return;
        // Kontrollu 404 (pipeline yok VEYA run baska tenant'a ait) -> sekme yok.
        if (err instanceof ApiError && err.status === 404) {
          setAiProbe({ state: "absent" });
        } else {
          setAiProbe({ state: "error" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

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
    return (
      <div className="dashboard-error" role="alert">
        <p>{error}</p>
        <button type="button" className="btn-secondary" onClick={() => void loadReport()}>
          Tekrar dene
        </button>
      </div>
    );
  }

  if (!report) {
    return <p className="page-placeholder">Yükleniyor…</p>;
  }

  const isAbReport = Boolean(report.ab_comparison);
  // Kontrollu 404 (pipeline yok) DISINDAKI her sonuçta sekme gosterilir; boylece
  // gercek hata sessizce gizlenmez ("absent" -> gizli, "present"/"error" -> acik).
  const showAiTab =
    aiProbe.state === "error" ||
    aiProbe.state === "present" ||
    (aiProbe.state === "absent" && Boolean(report.ai_report_requested));
  const tabs = showAiTab
    ? [...TOP_LEVEL_TABS, { id: AI_REPORT_TAB_ID, label: "AI Raporu" }]
    : TOP_LEVEL_TABS;

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
            {isAbReport ? "A/B Tasarım Karşılaştırması" : normalizeTurkishSystemCopy(report.title)}
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

      <TopLevelTabs tabs={tabs} activeTab={activeTab} onChange={setActiveTab} idPrefix={idPrefix} />

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

      {showAiTab && activeTab === AI_REPORT_TAB_ID && (
        <div
          role="tabpanel"
          id={`${idPrefix}-panel-${AI_REPORT_TAB_ID}`}
          aria-labelledby={`${idPrefix}-tab-${AI_REPORT_TAB_ID}`}
        >
          <AiReportTab
            runId={report.simulation_run_id}
            isAbComparison={isAbReport}
            initialStatus={aiProbe.state === "present" ? aiProbe.status : null}
            initialError={aiProbe.state === "error" || aiProbe.state === "absent"}
            initialMissing={aiProbe.state === "absent" && Boolean(report.ai_report_requested)}
          />
        </div>
      )}
    </section>
  );
}
