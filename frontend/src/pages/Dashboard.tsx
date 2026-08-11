import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  type ChipLedgerEntryResponse,
  type ChipLedgerEntryType,
  type ProjectResponse,
  type ReportListItemResponse,
  type SimulationRunResponse,
  type UsageSummaryResponse,
  type WizardDraftResponse,
  getChipLedger,
  getUsageSummary,
  listProjects,
  listReports,
  listSimulationRuns,
  listWizardDrafts,
} from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { normalizeTurkishSystemCopy } from "../lib/turkishCopy";
import {
  ActivityIcon,
  CheckCircleIcon,
  ChipCoinIcon,
  FolderIcon,
  ShieldCheckIcon,
} from "../components/icons";

interface DashboardData {
  projects: ProjectResponse[];
  runs: SimulationRunResponse[];
  usage: UsageSummaryResponse;
  reports: ReportListItemResponse[];
  ledger: ChipLedgerEntryResponse[];
  drafts: WizardDraftResponse[];
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
        to.includes("#") ? (
          <a href={to} className="summary-card__link">
            {linkLabel ?? "Görüntüle"}
          </a>
        ) : (
          <Link to={to} className="summary-card__link">
            {linkLabel ?? "Görüntüle"}
          </Link>
        )
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
      label: `Rapor oluşturuldu: ${report.test_definition_name} — ${normalizeTurkishSystemCopy(report.variant_name)}`,
      timestamp: report.created_at,
      href: `/raporlar/${report.id}`,
    });
  }

  for (const entry of data.ledger) {
    const label = LEDGER_ACTIVITY_LABELS[entry.entry_type];
    if (!label) continue;
    items.push({
      id: `ledger-${entry.id}`,
      label: entry.reason ? `${label}: ${normalizeTurkishSystemCopy(entry.reason)}` : label,
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
  const isDemo = Boolean(session?.is_demo);
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
      listWizardDrafts(),
    ])
      .then(([projects, runs, usage, reports, ledger, drafts]) => {
        if (
          !Array.isArray(projects) ||
          !Array.isArray(runs) ||
          !Array.isArray(reports) ||
          !Array.isArray(ledger) ||
          !Array.isArray(drafts)
        ) {
          throw new Error("Beklenmeyen veri biçimi");
        }
        setData({ projects, runs, usage, reports, ledger, drafts });
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

  const activity = data ? buildActivity(data) : [];
  const dashboardSummary = useMemo(() => {
    if (!data) return null;

    const completedDefinitionIds = new Set(
      data.reports.map((report) => report.test_definition_id),
    );
    const activeRuns = data.runs.filter(
      (run) => run.status === "queued" || run.status === "running",
    ).length;
    const availableModuleRights = data.usage.entitlements.reduce(
      (total, entitlement) =>
        total + (entitlement.status === "available" ? entitlement.quantity : 0),
      0,
    );

    return {
      completedTests: completedDefinitionIds.size,
      continuingWork: data.drafts.length + activeRuns,
      availableModuleRights,
      draftCount: data.drafts.length,
      activeRuns,
    };
  }, [data]);

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

      {data && dashboardSummary && (
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
            icon={<ShieldCheckIcon />}
            title="Analiz Modülleri"
            value={dashboardSummary.availableModuleRights.toString()}
            helper="Kullanılabilir ücretsiz analiz hakkı"
            to="/analiz-modulleri"
            linkLabel="Haklarımı görüntüle"
          />
          <SummaryCard
            icon={<FolderIcon />}
            title="Toplam Proje"
            value={data.projects.length.toString()}
            helper="Aktif ve arşivlenmiş tüm projeler"
            to="/projeler"
            linkLabel="Projeleri görüntüle"
          />
          <SummaryCard
            icon={<ActivityIcon />}
            title="Devam Eden Çalışmalar"
            value={dashboardSummary.continuingWork.toString()}
            helper={`${dashboardSummary.draftCount} yarım kalan taslak · ${dashboardSummary.activeRuns} çalışan test`}
            to="/raporlar#yarim-kalan-testler"
            linkLabel="Yarım kalanları görüntüle"
          />
          <SummaryCard
            icon={<CheckCircleIcon />}
            title="Tamamlanan Testler"
            value={dashboardSummary.completedTests.toString()}
            helper="Raporu hazır olan benzersiz testler"
            to="/raporlar"
            linkLabel="Raporları görüntüle"
          />
        </div>
      )}

      <div className="dashboard-columns">
        <section id="project-tests-heading" className="dashboard-section" aria-labelledby="project-tests-title">
          <div className="dashboard-section__heading-row">
            <div>
              <h2 id="project-tests-title" className="dashboard-section__title">
                Projelerim ve Testler
              </h2>
              <p>Bir projeyi açarak tamamlanan testleri ve yarım kalan taslakları görüntüleyin.</p>
            </div>
            <Link to="/projeler" className="dashboard-section__text-link">
              Tüm projeler
            </Link>
          </div>

          {isLoading && !data && !hasError && (
            <div className="dashboard-card-skeleton" aria-hidden="true" />
          )}

          {data && data.projects.length === 0 && (
            <div className="empty-state">
              <p>Henüz bir projeniz yok.</p>
              <Link to="/projeler" className="btn-secondary">
                İlk projeyi oluştur
              </Link>
            </div>
          )}

          {data && data.projects.length > 0 && (
            <div className="project-test-list">
              {data.projects.map((project) => {
                const projectDrafts = data.drafts.filter(
                  (draft) => draft.payload.project_id === project.id,
                );
                const completedByDefinition = new Map<string, ReportListItemResponse>();
                data.reports
                  .filter((report) => report.project_id === project.id)
                  .forEach((report) => {
                    if (!completedByDefinition.has(report.test_definition_id)) {
                      completedByDefinition.set(report.test_definition_id, report);
                    }
                  });
                const completedTests = [...completedByDefinition.values()];
                const processingCount = Math.max(project.test_count - completedTests.length, 0);
                const totalCount = project.test_count + projectDrafts.length;

                return (
                  <details key={project.id} className="project-test-group">
                    <summary>
                      <span>
                        <strong>{project.name}</strong>
                        <small>
                          {project.test_count} başlatılmış test · {projectDrafts.length} taslak ·{" "}
                          {completedTests.length} tamamlandı
                        </small>
                      </span>
                      <span className="project-test-group__count">{totalCount}</span>
                    </summary>
                    <div className="project-test-group__content">
                      {projectDrafts.length > 0 && (
                        <div className="project-test-subsection">
                          <h3>Yarım kalan testler</h3>
                          <ul>
                            {projectDrafts.map((draft) => (
                              <li key={draft.id}>
                                <Link to={`/tests/new?draft=${draft.id}`}>
                                  <span>
                                    <strong>
                                      {draft.payload.name?.trim() || "Adsız test taslağı"}
                                    </strong>
                                    <small>
                                      {draft.current_step}. adımda bırakıldı · Son değişiklik{" "}
                                      {formatDateTime(draft.updated_at)}
                                    </small>
                                  </span>
                                  <span>Devam et →</span>
                                </Link>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {completedTests.length > 0 && (
                        <div className="project-test-subsection">
                          <h3>Tamamlanan testler</h3>
                          <ul>
                            {completedTests.map((report) => (
                              <li key={report.test_definition_id}>
                                <Link to={`/raporlar/${report.id}`}>
                                  <span>
                                    <strong>{report.test_definition_name}</strong>
                                    <small>Rapor hazır · {formatDateTime(report.created_at)}</small>
                                  </span>
                                  <span>Raporu aç →</span>
                                </Link>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}

                      {processingCount > 0 && (
                        <Link to="/simulasyonlar" className="project-test-processing">
                          {processingCount} test çalışıyor veya sonuç bekliyor →
                        </Link>
                      )}

                      {totalCount === 0 && (
                        <p className="project-test-group__empty">
                          Bu projede henüz test bulunmuyor.
                        </p>
                      )}
                    </div>
                  </details>
                );
              })}
            </div>
          )}

          {data && data.drafts.some((draft) => !draft.payload.project_id) && (
            <div className="unassigned-drafts">
              <strong>Projeye bağlanmamış taslaklar</strong>
              {data.drafts
                .filter((draft) => !draft.payload.project_id)
                .map((draft) => (
                  <Link key={draft.id} to={`/tests/new?draft=${draft.id}`}>
                    {draft.payload.name?.trim() || "Adsız test taslağı"} · {draft.current_step}.
                    adımdan devam et →
                  </Link>
                ))}
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
          {!isDemo && (
            <Link to="/tests/new" className="quick-action-card">
              Yeni test oluştur
            </Link>
          )}
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
    </section>
  );
}
