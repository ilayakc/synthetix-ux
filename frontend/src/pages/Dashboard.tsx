import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  type ChipLedgerEntryResponse,
  type ChipLedgerEntryType,
  type ProjectResponse,
  type ReportListItemResponse,
  type SimulationRunResponse,
  type SimulationRunStatus,
  type UsageSummaryResponse,
  getChipLedger,
  getUsageSummary,
  listProjects,
  listReports,
  listSimulationRuns,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  ActivityIcon,
  CheckCircleIcon,
  ChipCoinIcon,
  FolderIcon,
  InfoIcon,
  ShieldCheckIcon,
  TicketIcon,
} from "../components/icons";

interface DashboardData {
  projects: ProjectResponse[];
  runs: SimulationRunResponse[];
  usage: UsageSummaryResponse;
  reports: ReportListItemResponse[];
  ledger: ChipLedgerEntryResponse[];
}

const RUN_STATUS_LABELS: Record<SimulationRunStatus, string> = {
  queued: "Kuyrukta",
  running: "Çalışıyor",
  succeeded: "Tamamlandı",
  failed: "Başarısız",
  cancelled: "İptal edildi",
};

const ENTITLEMENT_STATUS_LABELS: Record<string, string> = {
  available: "Kullanılabilir",
  reserved: "Rezerve edildi",
  consumed: "Kullanıldı",
};

function StatusBadge({ status }: { status: SimulationRunStatus }) {
  return (
    <span className={`status-badge status-badge--${status}`}>{RUN_STATUS_LABELS[status]}</span>
  );
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("tr-TR");
}

function SummaryCard({
  icon,
  title,
  value,
  helper,
  to,
  linkLabel,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  helper?: string;
  to?: string;
  linkLabel?: string;
}) {
  return (
    <div className="summary-card">
      <div className="summary-card__icon" aria-hidden="true">
        {icon}
      </div>
      <h3 className="summary-card__title">{title}</h3>
      <p className="summary-card__value">{value}</p>
      {helper && <p className="summary-card__helper">{helper}</p>}
      {to && (
        <Link to={to} className="summary-card__link">
          {linkLabel ?? "Görüntüle"}
        </Link>
      )}
    </div>
  );
}

function SummaryCardSkeleton() {
  return (
    <div className="summary-card summary-card--skeleton" aria-hidden="true">
      <div className="skeleton-block skeleton-block--icon" />
      <div className="skeleton-block skeleton-block--title" />
      <div className="skeleton-block skeleton-block--value" />
    </div>
  );
}

function entitlementFor(usage: UsageSummaryResponse, featureKey: string) {
  const entitlement = usage.entitlements.find((item) => item.feature_key === featureKey);
  if (!entitlement) {
    return { remaining: 0, statusLabel: "Hak bilgisi bulunamadı" };
  }
  const remaining = entitlement.status === "available" ? entitlement.quantity : 0;
  return { remaining, statusLabel: ENTITLEMENT_STATUS_LABELS[entitlement.status] };
}

function findActiveRun(runs: SimulationRunResponse[]): SimulationRunResponse | null {
  const active = runs.filter((run) => run.status === "queued" || run.status === "running");
  if (active.length === 0) return null;
  return [...active].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )[0];
}

interface ActivityItem {
  id: string;
  label: string;
  timestamp: string;
  href: string;
}

const LEDGER_ACTIVITY_LABELS: Partial<Record<ChipLedgerEntryType, string>> = {
  reserve: "Chip rezerve edildi",
  consume: "Chip tüketildi",
  release: "Chip iade edildi",
};

// Yalnizca gercek API yanitlarindan guvenilir bicimde cikarilabilen olaylar
// listelenir (bkz. gereksinim - ayri bir aktivite API'si yok, sahte aktivite
// uretilmez). Ayri bir "olustu" olayi olmayan ara durumlar (orn. "running")
// buraya dahil edilmez.
function buildActivity(data: DashboardData): ActivityItem[] {
  const items: ActivityItem[] = [];

  for (const project of data.projects) {
    items.push({
      id: `project-created-${project.id}`,
      label: `Proje oluşturuldu: ${project.name}`,
      timestamp: project.created_at,
      href: `/projeler/${project.id}`,
    });
  }

  for (const run of data.runs) {
    const shortId = run.id.slice(0, 8);
    items.push({
      id: `run-started-${run.id}`,
      label: `Simülasyon başlatıldı: Çalıştırma ${shortId}`,
      timestamp: run.created_at,
      href: "/simulasyonlar",
    });
    if (run.status === "succeeded" && run.finished_at) {
      items.push({
        id: `run-succeeded-${run.id}`,
        label: `Simülasyon tamamlandı: Çalıştırma ${shortId}`,
        timestamp: run.finished_at,
        href: "/simulasyonlar",
      });
    }
    if (run.status === "failed" && run.finished_at) {
      items.push({
        id: `run-failed-${run.id}`,
        label: `Simülasyon başarısız oldu: Çalıştırma ${shortId}`,
        timestamp: run.finished_at,
        href: "/simulasyonlar",
      });
    }
  }

  for (const report of data.reports) {
    items.push({
      id: `report-created-${report.id}`,
      label: `Rapor oluşturuldu: ${report.test_definition_name} — ${report.variant_name}`,
      timestamp: report.created_at,
      href: `/raporlar/${report.id}`,
    });
  }

  for (const entry of data.ledger) {
    const label = LEDGER_ACTIVITY_LABELS[entry.entry_type];
    if (!label) continue;
    items.push({
      id: `ledger-${entry.id}`,
      label: entry.reason ? `${label}: ${entry.reason}` : label,
      timestamp: entry.created_at,
      href: "/kullanim-ve-chip",
    });
  }

  return items
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, 5);
}

export default function Dashboard() {
  const { session } = useAuth();
  const [data, setData] = useState<DashboardData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [hasError, setHasError] = useState(false);

  const load = useCallback(() => {
    setIsLoading(true);
    setHasError(false);
    Promise.all([
      listProjects(),
      listSimulationRuns(),
      getUsageSummary(),
      listReports(),
      getChipLedger(),
    ])
      .then(([projects, runs, usage, reports, ledger]) => {
        if (
          !Array.isArray(projects) ||
          !Array.isArray(runs) ||
          !Array.isArray(reports) ||
          !Array.isArray(ledger)
        ) {
          throw new Error("Beklenmeyen veri biçimi");
        }
        setData({ projects, runs, usage, reports, ledger });
      })
      .catch(() => {
        setHasError(true);
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const today = useMemo(
    () =>
      new Date().toLocaleDateString("tr-TR", { day: "numeric", month: "long", year: "numeric" }),
    [],
  );

  const activeRun = data ? findActiveRun(data.runs) : null;
  const activity = data ? buildActivity(data) : [];

  return (
    <section aria-labelledby="dashboard-heading">
      <h1 id="dashboard-heading" className="page-heading">
        Genel Bakış
      </h1>

      <div className="dashboard-welcome">
        <div className="dashboard-welcome__text">
          <p className="dashboard-welcome__date">{today}</p>
          <h2 className="dashboard-welcome__title">
            Hoş geldiniz{session?.display_name ? `, ${session.display_name}` : ""}
          </h2>
          <p className="dashboard-welcome__subtitle">
            {session?.organization_name ?? "Organizasyonunuzun"} adına proje, test ve Chip
            kullanımına dair güncel özet aşağıda.
          </p>
        </div>
        <Link to="/tests/new" className="auth-submit dashboard-welcome__cta">
          Yeni Test Başlat
        </Link>
      </div>

      {hasError && (
        <div className="dashboard-error" role="alert">
          <p>Özet veriler yüklenemedi.</p>
          <button type="button" className="btn-secondary" onClick={load}>
            Tekrar dene
          </button>
        </div>
      )}

      {isLoading && !data && !hasError && (
        <div className="dashboard-grid dashboard-grid--summary">
          {Array.from({ length: 6 }).map((_, index) => (
            <SummaryCardSkeleton key={index} />
          ))}
        </div>
      )}

      {data && (
        <div className="dashboard-grid dashboard-grid--summary">
          <SummaryCard
            icon={<ChipCoinIcon />}
            title="Chip Bakiyesi"
            value={data.usage.chip_balance.toString()}
            helper="Kalan Chip bakiyeniz"
            to="/kullanim-ve-chip"
            linkLabel="Chip kullanımını görüntüle"
          />
          <SummaryCard
            icon={<TicketIcon />}
            title="Ücretsiz Temel UX Testi"
            value={entitlementFor(data.usage, "basic_ux_test").remaining.toString()}
            helper={entitlementFor(data.usage, "basic_ux_test").statusLabel}
            to="/kullanim-ve-chip"
            linkLabel="Haklarımı görüntüle"
          />
          <SummaryCard
            icon={<ShieldCheckIcon />}
            title="Ücretsiz Erişilebilirlik Ön Kontrolü"
            value={entitlementFor(data.usage, "accessibility_precheck").remaining.toString()}
            helper={entitlementFor(data.usage, "accessibility_precheck").statusLabel}
            to="/kullanim-ve-chip"
            linkLabel="Haklarımı görüntüle"
          />
          <SummaryCard
            icon={<FolderIcon />}
            title="Aktif Proje Sayısı"
            value={data.projects.filter((project) => project.status === "active").length.toString()}
            helper="Arşivlenmemiş projeleriniz"
            to="/projeler"
            linkLabel="Projeleri görüntüle"
          />
          <SummaryCard
            icon={<ActivityIcon />}
            title="Devam Eden Simülasyon Sayısı"
            value={data.runs
              .filter((run) => run.status === "queued" || run.status === "running")
              .length.toString()}
            helper="Kuyrukta veya çalışan testler"
            to="/simulasyonlar"
            linkLabel="Simülasyonları görüntüle"
          />
          <SummaryCard
            icon={<CheckCircleIcon />}
            title="Tamamlanan Simülasyonlar"
            value={data.runs.filter((run) => run.status === "succeeded").length.toString()}
            helper="Rapor üretilen çalıştırmalar"
            to="/raporlar"
            linkLabel="Raporları görüntüle"
          />
        </div>
      )}

      <div className="dashboard-columns">
        <section className="dashboard-section" aria-labelledby="active-test-heading">
          <h2 id="active-test-heading" className="dashboard-section__title">
            Aktif Test
          </h2>

          {isLoading && !data && !hasError && (
            <div className="dashboard-card-skeleton" aria-hidden="true" />
          )}

          {data && activeRun && (
            <div className="active-test-card">
              <div className="active-test-card__header">
                <span className="active-test-card__name">
                  Çalıştırma {activeRun.id.slice(0, 8)}
                </span>
                <StatusBadge status={activeRun.status} />
              </div>
              <dl className="active-test-card__meta">
                <div>
                  <dt>Başlangıç tarihi</dt>
                  <dd>{formatDateTime(activeRun.created_at)}</dd>
                </div>
                <div>
                  <dt>İşlem aşaması</dt>
                  <dd>{activeRun.progress_message ?? RUN_STATUS_LABELS[activeRun.status]}</dd>
                </div>
              </dl>
              {activeRun.status === "running" && (
                <div
                  className="progress-bar"
                  role="progressbar"
                  aria-valuenow={activeRun.progress_percent}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label="Simülasyon ilerlemesi"
                >
                  <div
                    className="progress-bar__fill"
                    style={{ width: `${activeRun.progress_percent}%` }}
                  />
                </div>
              )}
              <Link to="/simulasyonlar" className="active-test-card__link">
                Simülasyon detayına git →
              </Link>
            </div>
          )}

          {data && !activeRun && (
            <div className="empty-state">
              <p>Şu anda devam eden test bulunmuyor.</p>
              <Link to="/tests/new" className="auth-submit">
                Yeni Test Başlat
              </Link>
            </div>
          )}
        </section>

        <section className="dashboard-section" aria-labelledby="activity-heading">
          <h2 id="activity-heading" className="dashboard-section__title">
            Son Aktiviteler
          </h2>

          {isLoading && !data && !hasError && (
            <div className="dashboard-card-skeleton" aria-hidden="true" />
          )}

          {data && activity.length === 0 && (
            <p className="page-placeholder">Henüz bir aktivite yok.</p>
          )}

          {data && activity.length > 0 && (
            <ul className="activity-list">
              {activity.map((item) => (
                <li key={item.id} className="activity-list__item">
                  <Link to={item.href} className="activity-list__link">
                    <span className="activity-list__label">{item.label}</span>
                    <span className="activity-list__time">{formatDateTime(item.timestamp)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section className="dashboard-section" aria-labelledby="quick-actions-heading">
        <h2 id="quick-actions-heading" className="dashboard-section__title">
          Hızlı İşlemler
        </h2>
        <div className="quick-actions">
          <Link to="/tests/new" className="quick-action-card">
            Yeni test oluştur
          </Link>
          <Link to="/projeler" className="quick-action-card">
            Projeleri görüntüle
          </Link>
          <Link to="/analiz-modulleri" className="quick-action-card">
            Analiz modüllerini incele
          </Link>
          <Link to="/kullanim-ve-chip" className="quick-action-card">
            Chip kullanımını görüntüle
          </Link>
        </div>
      </section>

      <div className="integrity-card" role="note">
        <InfoIcon className="integrity-card__icon" />
        <p>
          Sonuçlar sentetik model tahminleridir; gerçek kullanıcı ölçümü veya doğrulanmış insan
          davranışı değildir.
        </p>
      </div>
    </section>
  );
}
