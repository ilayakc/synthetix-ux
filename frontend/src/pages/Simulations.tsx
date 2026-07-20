import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  type SimulationRunResponse,
  type SimulationRunStatus,
  cancelSimulationRun,
  listSimulationRuns,
  retrySimulationRun,
} from "../api/client";

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

  return (
    <div className="simulation-card">
      <div className="simulation-card__header">
        <h3 className="simulation-card__title">Çalıştırma {run.id.slice(0, 8)}</h3>
        <div className="simulation-card__actions">
          <StatusBadge status={run.status} />
          {isActive && (
            <button type="button" className="auth-google-button" onClick={() => onCancel(run.id)}>
              İptal et
            </button>
          )}
          {isFailed && (
            <button type="button" className="auth-submit" onClick={() => onRetry(run.id)}>
              Yeniden dene
            </button>
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
      {run.error && <p className="simulation-card__error">Hata: {run.error}</p>}

      {run.status === "succeeded" && <SimulationResultCard run={run} />}
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
        Kalibre edilmemiş, aciklanabilir bir heuristic motor tarafından üretilen sentetik senaryo
        tahminleri. Gerçek kullanıcı verisi değildir; erken aşama fikir üretimi/hipotez oluşturma
        icin kullanılabilir.
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
