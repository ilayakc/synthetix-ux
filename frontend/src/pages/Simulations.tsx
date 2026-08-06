import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  type PersonaDimensionInfo,
  type SimulationRunPersonaResponse,
  type SimulationRunResponse,
  type SimulationRunStatus,
  cancelSimulationRun,
  getSimulationRunPersonas,
  listPersonaDimensions,
  listSimulationRuns,
  retrySimulationRun,
} from "../api/client";
import { logger } from "../lib/logger";
import { personaIntegrityStatus } from "../lib/personaIntegrity";

const NON_RETRYABLE_ERROR_MESSAGE =
  "Bu çalışma, seçili tasarım kaynağıyla uyumsuz bir analiz modülü nedeniyle tamamlanamadı ve yeniden denenemez.";

const STATUS_LABELS: Record<SimulationRunStatus, string> = {
  queued: "Kuyrukta",
  running: "Çalışıyor",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

const POLL_INTERVAL_MS = 2500;

function StatusBadge({ status }: { status: SimulationRunStatus }) {
  return <span className={`status-badge status-badge--${status}`}>{STATUS_LABELS[status]}</span>;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function UncertaintyMetricCard({
  label,
  metric,
  isPercent = true,
}: {
  label: string;
  metric: { point_estimate: number; low: number; high: number };
  isPercent?: boolean;
}) {
  const format = (value: number) => (isPercent ? formatPercent(value) : value.toString());
  return (
    <div className="result-metric">
      <span className="result-metric__label">{label}</span>
      <span className="result-metric__value">{format(metric.point_estimate)}</span>
      <span className="result-metric__range">
        Belirsizlik aralığı: {format(metric.low)} – {format(metric.high)}
      </span>
    </div>
  );
}

function SimulationResultCard({ run }: { run: SimulationRunResponse }) {
  const result = run.result;
  if (!result) return null;

  return (
    <div className="result-metric-grid-wrapper">
      <div className="result-metric-grid">
        <UncertaintyMetricCard
          label="Görev tamamlama olasılığı"
          metric={result.metrics.task_completion_probability}
        />
        <UncertaintyMetricCard
          label="Yanlış tıklama olasılığı"
          metric={result.metrics.misclick_probability}
        />
        <UncertaintyMetricCard
          label="Terk (abandonment) olasılığı"
          metric={result.metrics.abandonment_probability}
        />
        <div className="result-metric">
          <span className="result-metric__label">Tahmini görev süresi</span>
          <span className="result-metric__value">
            {Math.round(result.metrics.task_duration_seconds.point_estimate)} sn
          </span>
          <span className="result-metric__range">
            p10–p90: {Math.round(result.metrics.task_duration_seconds.p10)}–
            {Math.round(result.metrics.task_duration_seconds.p90)} sn
          </span>
        </div>
        <div className="result-metric">
          <span className="result-metric__label">Okunabilirlik skoru</span>
          <span className="result-metric__value">{result.metrics.readability_score} / 100</span>
        </div>
        <div className="result-metric">
          <span className="result-metric__label">Kontrast kontrolü (WCAG AA)</span>
          <span className="result-metric__value">
            {result.metrics.contrast_check.pass ? "Geçti" : "Geçmedi"}
          </span>
          <span className="result-metric__range">
            Oran: {result.metrics.contrast_check.avg_ratio} (eşik:{" "}
            {result.metrics.contrast_check.threshold})
          </span>
        </div>
      </div>

      {result.metrics.regional_interest.length > 0 && (
        <div className="result-metric-grid">
          {result.metrics.regional_interest.map((region) => (
            <div className="result-metric" key={region.region_key}>
              <span className="result-metric__label">
                Bölgesel tahmini ilgi: {region.region_label}
              </span>
              <span className="result-metric__value">{region.estimate}</span>
              <span className="result-metric__range">{region.disclaimer}</span>
            </div>
          ))}
        </div>
      )}

      <span className="not-real-data-tag">{run.not_real_user_data_label}</span>
      <p className="methodology-note">
        {result.disclaimer} Metodoloji: {run.methodology_reference}
      </p>
    </div>
  );
}

// Sabit kalan 5 persona boyutu (bkz. backend app.services.personas.
// PERSONA_DIMENSIONS) - bu liste yalnizca sutun SIRASINI belirler, Turkce
// ETIKETLERI degil; etiketler `listPersonaDimensions()` uzerinden backend'den
// alinir (Step3Personas.tsx/PersonaDistributionEditor.tsx ile AYNI kaynak,
// burada ikinci bir sozluk olarak KOPYALANMAZ). Bilinmeyen/eksik bir boyutta
// ham anahtar (`humanizeAttributeValue` ile) fallback olarak gosterilir.
const PERSONA_DIMENSION_KEYS = [
  "age_range",
  "region",
  "device_class",
  "digital_literacy",
  "accessibility_need",
] as const;

const PERSONA_LIST_PAGE_SIZE = 20;

// Bir dagilim/bucket key'ini ("device_class", "18_24" gibi) okunabilir bir
// ham metne cevirir - bu bir CEVIRI/ETIKET SOZLUGU DEGILDIR (stereotip/
// cikarim uretmez), yalnizca alt cizgiyi bosluga cevirip ilk harfi
// buyutur; bilinmeyen/beklenmedik degerler icin guvenli, colmeyen bir
// gorunum saglar.
function humanizeAttributeValue(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return trimmed;
  return trimmed
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function personaAttributeDisplay(attributes: Record<string, string>, key: string): string {
  const raw = attributes?.[key];
  if (raw === undefined || raw === null || raw === "") return "—";
  return humanizeAttributeValue(raw);
}

function SimulationPersonasPanel({
  runId,
  personaCount = null,
}: {
  runId: string;
  personaCount?: number | null;
}) {
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [personas, setPersonas] = useState<SimulationRunPersonaResponse[] | null>(null);
  const [dimensionLabels, setDimensionLabels] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const loadPersonas = () => {
    setLoading(true);
    setError(null);
    getSimulationRunPersonas(runId)
      .then((data) => {
        // Backend zaten index ASC dondurur; savunmacı olarak burada da
        // sıralanır (bkz. proje talimati) - sıra istemci tarafında asla
        // population_weight'e gore DEGISTIRILMEZ.
        setPersonas([...data].sort((a, b) => a.index - b.index));
      })
      .catch(() => setError("Persona grupları yüklenemedi."))
      .finally(() => setLoading(false));

    // Boyut etiketleri (ornegin "age_range" -> "Yaş aralığı") en iyi-gayret
    // ile ayrica yuklenir - bu cagri basarisiz olsa bile persona listesi
    // gosterilmeye devam eder (fallback: humanizeAttributeValue).
    listPersonaDimensions()
      .then((dimensions: PersonaDimensionInfo[]) => {
        const map: Record<string, string> = {};
        for (const dimension of dimensions) map[dimension.key] = dimension.label;
        setDimensionLabels(map);
      })
      .catch(() => {
        logger.warn(
          "personas",
          `boyut etiketleri yuklenemedi (run ${runId}) - ham anahtarlar fallback olarak kullanilacak`,
        );
      });
  };

  useEffect(() => {
    if (!expanded || personas !== null || loading) return;
    loadPersonas();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  const totalPopulation = personas ? personas.reduce((sum, p) => sum + p.population_weight, 0) : 0;
  const integrityStatus = personas
    ? personaIntegrityStatus(totalPopulation, personaCount)
    : "unknown";
  if (personas && integrityStatus === "mismatched") {
    logger.warn(
      "personas",
      `agirlik toplami (${totalPopulation}) persona_count (${personaCount}) ile eslesmiyor (run ${runId})`,
    );
  }

  const visiblePersonas = personas
    ? showAll
      ? personas
      : personas.slice(0, PERSONA_LIST_PAGE_SIZE)
    : [];
  const hasMore = (personas?.length ?? 0) > PERSONA_LIST_PAGE_SIZE;

  return (
    <div className="simulation-personas-panel">
      <button
        type="button"
        className="btn-secondary"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setExpanded((value) => !value)}
      >
        Kimler simüle edildi?
      </button>

      {expanded && (
        <div id={panelId} className="simulation-personas-panel__body">
          <p className="report-section__intro">
            Bu profiller gerçek kişiler değildir. Seçilen hedef kitle dağılımını temsil eden
            sentetik kullanıcı gruplarıdır.
          </p>

          {loading && (
            <p className="page-placeholder" aria-live="polite">
              Persona grupları yükleniyor…
            </p>
          )}

          {!loading && error && (
            <>
              <p className="auth-error">{error}</p>
              <button type="button" className="btn-secondary" onClick={loadPersonas}>
                Tekrar dene
              </button>
            </>
          )}

          {!loading && !error && personas !== null && personas.length === 0 && (
            <div className="empty-state">
              <p>Bu simülasyon için kalıcı persona kaydı bulunmuyor.</p>
            </div>
          )}

          {!loading && !error && personas !== null && personas.length > 0 && (
            <>
              <div className="simulation-personas-panel__summary">
                <span>{personas.length} temsilî profil</span>
                <span>{totalPopulation.toLocaleString("tr-TR")} sanal kullanıcı</span>
                {integrityStatus === "matched" && (
                  <span className="simulation-personas-panel__integrity simulation-personas-panel__integrity--ok">
                    <span aria-hidden="true">✓</span> Dağılım toplamı doğrulandı
                  </span>
                )}
                {integrityStatus === "mismatched" && (
                  <span
                    role="status"
                    className="simulation-personas-panel__integrity simulation-personas-panel__integrity--warning"
                  >
                    <span aria-hidden="true">⚠</span> Persona dağılımı toplamı beklenen kullanıcı
                    sayısıyla eşleşmiyor
                  </span>
                )}
              </div>

              <div className="simulation-personas-panel__table-wrapper">
                <table className="persona-segment-table">
                  <thead>
                    <tr>
                      <th scope="col">Persona</th>
                      <th scope="col">Kullanıcı sayısı</th>
                      <th scope="col">Pay</th>
                      {PERSONA_DIMENSION_KEYS.map((key) => (
                        <th scope="col" key={key}>
                          {dimensionLabels[key] ?? humanizeAttributeValue(key)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visiblePersonas.map((persona) => {
                      const share =
                        totalPopulation > 0
                          ? (persona.population_weight / totalPopulation) * 100
                          : 0;
                      return (
                        <tr key={persona.id}>
                          <td>{persona.label}</td>
                          <td>{persona.population_weight.toLocaleString("tr-TR")}</td>
                          <td>%{share.toFixed(1)}</td>
                          {PERSONA_DIMENSION_KEYS.map((key) => (
                            <td key={key}>{personaAttributeDisplay(persona.attributes, key)}</td>
                          ))}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {hasMore && (
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowAll((value) => !value)}
                >
                  {showAll ? "Daha az göster" : `Tümünü göster (${personas.length})`}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function SimulationCard({
  run,
  onCancel,
  onRetry,
}: {
  run: SimulationRunResponse;
  onCancel: (runId: string) => void;
  onRetry: (runId: string) => void;
}) {
  const isActive = run.status === "queued" || run.status === "running";
  const isFailed = run.status === "failed";
  // Retryable karari BACKEND'den gelir (bkz. app.services.simulation_worker.
  // classify_run_failure) - frontend burada HAM hata metninde substring
  // kontrolu YAPMAZ; `run.retryable`, worker'in ayni degismez input_snapshot
  // ile her zaman ayni sekilde basarisiz olacagini bildigi durumlarda
  // (ör. ekran goruntusu kaynagiyla `network_device_test`) false doner.
  const isNonRetryableFailure = isFailed && !run.retryable;

  return (
    <div className="simulation-card">
      <div className="simulation-card__header">
        <h3 className="simulation-card__title">Çalıştırma {run.id.slice(0, 8)}</h3>
        <div className="simulation-card__actions">
          <StatusBadge status={run.status} />
          {isActive && (
            <button type="button" className="btn-secondary" onClick={() => onCancel(run.id)}>
              İptal et
            </button>
          )}
          {isFailed && run.retryable && (
            <button type="button" className="auth-submit" onClick={() => onRetry(run.id)}>
              Yeniden dene
            </button>
          )}
          {isNonRetryableFailure && (
            <Link to="/tests/new" className="btn-secondary">
              Yeni test oluştur
            </Link>
          )}
        </div>
      </div>

      {isActive && (
        <div
          className="progress-bar"
          role="progressbar"
          aria-valuenow={run.progress_percent}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="progress-bar__fill" style={{ width: `${run.progress_percent}%` }} />
        </div>
      )}
      {run.progress_message && <p className="simulation-card__message">{run.progress_message}</p>}
      {run.error &&
        (isNonRetryableFailure ? (
          <p className="simulation-card__error">{NON_RETRYABLE_ERROR_MESSAGE}</p>
        ) : (
          <p className="simulation-card__error">Hata: {run.error}</p>
        ))}

      {run.status === "succeeded" && <SimulationResultCard run={run} />}

      {/* Run durumundan bagimsiz: personalar launch aninda, motor sonucundan
          bagimsiz olarak zaten olusturulmustur (bkz. GET .../personas). */}
      <SimulationPersonasPanel runId={run.id} personaCount={run.persona_count} />
    </div>
  );
}

export default function Simulations() {
  const [runs, setRuns] = useState<SimulationRunResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = () => {
    listSimulationRuns()
      .then((data) => {
        setRuns(data);
        setError(null);
      })
      .catch(() => setError("Simülasyonlar yüklenemedi."));
  };

  useEffect(() => {
    load();
    timerRef.current = setInterval(load, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  const handleCancel = async (runId: string) => {
    try {
      await cancelSimulationRun(runId);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "İptal işlemi başarısız oldu.");
    }
  };

  const handleRetry = async (runId: string) => {
    try {
      await retrySimulationRun(runId);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yeniden deneme başarısız oldu.");
    }
  };

  return (
    <section aria-labelledby="simulations-heading">
      <h1 id="simulations-heading" className="page-heading">
        Simülasyonlar
      </h1>
      <p className="page-placeholder">
        Sentetik senaryo çalışmalarını, ilerleme durumlarını ve üretilen raporları buradan takip
        edebilirsiniz.
      </p>

      {error && <p className="page-placeholder">{error}</p>}

      {runs && runs.length === 0 && (
        <div className="empty-state">
          <p>Henüz bir simülasyon çalıştırması yok.</p>
        </div>
      )}

      {runs && runs.length > 0 && (
        <div className="simulation-list">
          {runs.map((run) => (
            <SimulationCard key={run.id} run={run} onCancel={handleCancel} onRetry={handleRetry} />
          ))}
        </div>
      )}
    </section>
  );
}
