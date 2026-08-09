import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  type ProjectResponse,
  type ReportListItemResponse,
  type WizardDraftResponse,
  deleteWizardDraft,
  listProjects,
  listReports,
  listWizardDrafts,
} from "../api/client";
import { calibrationStatusLabel } from "../lib/turkishCopy";

type ReportGroup = ReportListItemResponse[];
type ReportsView = "completed" | "drafts";

interface ProjectReportGroup {
  project: ProjectResponse | null;
  projectId: string;
  projectName: string;
  reports: ReportGroup[];
}

interface ProjectDraftGroup {
  project: ProjectResponse | null;
  projectId: string;
  projectName: string;
  drafts: WizardDraftResponse[];
}

const UNASSIGNED_PROJECT_ID = "unassigned";

export default function Reports() {
  const [reports, setReports] = useState<ReportListItemResponse[] | null>(null);
  const [drafts, setDrafts] = useState<WizardDraftResponse[] | null>(null);
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const [activeView, setActiveView] = useState<ReportsView>(() =>
    window.location.hash === "#yarim-kalan-testler" ? "drafts" : "completed",
  );
  const [error, setError] = useState<string | null>(null);
  const [draftToDelete, setDraftToDelete] = useState<string | null>(null);
  const [isDeletingDraft, setIsDeletingDraft] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    return Promise.all([listReports(), listWizardDrafts(), listProjects()])
      .then(([reportItems, draftItems, projectItems]) => {
        setReports(reportItems);
        setDrafts(draftItems.filter((draft) => draft.status === "draft"));
        setProjects(projectItems);
      })
      .catch(() => setError("Raporlar yüklenemedi."));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const handleHashChange = () => {
      if (window.location.hash === "#yarim-kalan-testler") setActiveView("drafts");
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  // A/B varyant raporları kullanıcı için tek testtir; önce test, sonra proje altında gruplanır.
  const reportGroups = useMemo(
    () =>
      reports
        ? Array.from(
            reports.reduce((groups, report) => {
              const existing = groups.get(report.test_definition_id) ?? [];
              existing.push(report);
              groups.set(report.test_definition_id, existing);
              return groups;
            }, new Map<string, ReportListItemResponse[]>()),
          ).map(([, items]) => items)
        : null,
    [reports],
  );

  const projectsById = useMemo(
    () => new Map((projects ?? []).map((project) => [project.id, project])),
    [projects],
  );

  const completedByProject = useMemo<ProjectReportGroup[] | null>(() => {
    if (!reportGroups) return null;
    const grouped = new Map<string, ReportGroup[]>();
    reportGroups.forEach((group) => {
      const projectId = group[0].project_id;
      const existing = grouped.get(projectId) ?? [];
      existing.push(group);
      grouped.set(projectId, existing);
    });
    return Array.from(grouped, ([projectId, groupedReports]) => ({
      project: projectsById.get(projectId) ?? null,
      projectId,
      projectName: projectsById.get(projectId)?.name ?? groupedReports[0][0].project_name,
      reports: groupedReports,
    }));
  }, [projectsById, reportGroups]);

  const draftsByProject = useMemo<ProjectDraftGroup[] | null>(() => {
    if (!drafts) return null;
    const grouped = new Map<string, WizardDraftResponse[]>();
    drafts.forEach((draft) => {
      const projectId = draft.payload.project_id ?? UNASSIGNED_PROJECT_ID;
      const existing = grouped.get(projectId) ?? [];
      existing.push(draft);
      grouped.set(projectId, existing);
    });
    return Array.from(grouped, ([projectId, groupedDrafts]) => ({
      project: projectsById.get(projectId) ?? null,
      projectId,
      projectName:
        projectId === UNASSIGNED_PROJECT_ID
          ? "Henüz proje seçilmedi"
          : projectsById.get(projectId)?.name ?? "Seçili proje",
      drafts: groupedDrafts,
    })).sort((a, b) => Number(a.projectId === UNASSIGNED_PROJECT_ID) - Number(b.projectId === UNASSIGNED_PROJECT_ID));
  }, [drafts, projectsById]);

  const completedCount = reportGroups?.length ?? 0;
  const draftCount = drafts?.length ?? 0;

  const handleDeleteDraft = async (draftId: string) => {
    setDeleteError(null);
    setIsDeletingDraft(true);
    try {
      await deleteWizardDraft(draftId);
      setDrafts((current) => current?.filter((draft) => draft.id !== draftId) ?? null);
      setDraftToDelete(null);
    } catch {
      setDeleteError("Yarım kalan test silinemedi. Lütfen tekrar deneyin.");
    } finally {
      setIsDeletingDraft(false);
    }
  };

  return (
    <section aria-labelledby="reports-heading">
      <h1 id="reports-heading" className="page-heading">
        Raporlar
      </h1>
      <p className="page-placeholder">
        Projelerinize ait tamamlanan raporları inceleyin veya yarım kalan testlere devam edin.
      </p>

      {error && (
        <div className="dashboard-error" role="alert">
          <p>{error}</p>
          <button type="button" className="btn-secondary" onClick={() => void load()}>
            Tekrar dene
          </button>
        </div>
      )}

      {reports && drafts && projects && reports.length === 0 && drafts.length === 0 && (
        <div className="empty-state">
          <p>Henüz yarım kalan test veya tamamlanmış rapor yok.</p>
        </div>
      )}

      {reports && drafts && (reports.length > 0 || drafts.length > 0) && (
        <>
          <div className="reports-view-switch" role="tablist" aria-label="Rapor durumu">
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "completed"}
              className={activeView === "completed" ? "is-active" : ""}
              onClick={() => setActiveView("completed")}
            >
              <span>Tamamlanan raporlar</span>
              <strong>{completedCount}</strong>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "drafts"}
              className={activeView === "drafts" ? "is-active" : ""}
              onClick={() => setActiveView("drafts")}
            >
              <span>Yarım kalan testler</span>
              <strong>{draftCount}</strong>
            </button>
          </div>

          {activeView === "completed" && (
            <section
              id="tamamlanan-raporlar"
              className="reports-group"
              aria-labelledby="completed-reports-heading"
              role="tabpanel"
            >
              <div className="reports-group__heading">
                <div>
                  <h2 id="completed-reports-heading">Projeler ve raporları</h2>
                  <p>Önce projeyi, ardından incelemek istediğiniz testi seçin.</p>
                </div>
              </div>
              {completedByProject && completedByProject.length > 0 ? (
                <div className="reports-project-list">
                  {completedByProject.map((projectGroup) => (
                    <article key={projectGroup.projectId} className="reports-project-card">
                      <header className="reports-project-card__header">
                        <div>
                          <span className="reports-project-card__eyebrow">Proje</span>
                          <h3>{projectGroup.projectName}</h3>
                        </div>
                        <div className="reports-project-card__summary">
                          <span>{projectGroup.reports.length} test raporu</span>
                          {projectGroup.project && (
                            <Link to={`/projeler/${projectGroup.projectId}`}>Projeyi aç →</Link>
                          )}
                        </div>
                      </header>
                      <div className="report-list report-list--nested">
                        {projectGroup.reports.map((group) => {
                          const report = group[0];
                          const isComparison = group.length > 1;
                          return (
                            <Link
                              key={report.test_definition_id}
                              to={`/raporlar/${report.id}`}
                              className="report-list-item"
                            >
                              <div className="report-list-item__meta">
                                <span className="report-list-item__title">
                                  {report.test_definition_name}
                                </span>
                                <span className="report-list-item__sub">
                                  {isComparison
                                    ? "A/B karşılaştırması · 2 varyant tek raporda"
                                    : report.variant_name}
                                  {" · "}
                                  {new Date(report.created_at).toLocaleString("tr-TR")}
                                </span>
                              </div>
                              <span className="chip-pill">
                                {isComparison
                                  ? "A/B"
                                  : calibrationStatusLabel(report.calibration_status)}
                              </span>
                            </Link>
                          );
                        })}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="empty-state reports-inline-empty">
                  <p>Henüz tamamlanmış rapor yok.</p>
                </div>
              )}
            </section>
          )}

          {activeView === "drafts" && (
            <section
              id="yarim-kalan-testler"
              className="reports-group"
              aria-labelledby="draft-reports-heading"
              role="tabpanel"
            >
              <div className="reports-group__heading">
                <div>
                  <h2 id="draft-reports-heading">Devam edilecek testler</h2>
                  <p>Testler proje durumuna göre gruplandı; kaldığınız adımdan devam edebilirsiniz.</p>
                </div>
              </div>
              {deleteError && <p className="auth-error">{deleteError}</p>}
              {draftsByProject && draftsByProject.length > 0 ? (
                <div className="reports-project-list">
                  {draftsByProject.map((projectGroup) => (
                    <article
                      key={projectGroup.projectId}
                      className={`reports-project-card${
                        projectGroup.projectId === UNASSIGNED_PROJECT_ID ? " is-unassigned" : ""
                      }`}
                    >
                      <header className="reports-project-card__header">
                        <div>
                          <span className="reports-project-card__eyebrow">
                            {projectGroup.projectId === UNASSIGNED_PROJECT_ID ? "Taslaklar" : "Proje"}
                          </span>
                          <h3>{projectGroup.projectName}</h3>
                        </div>
                        <span className="reports-project-card__count">
                          {projectGroup.drafts.length} yarım test
                        </span>
                      </header>
                      <div className="report-list report-list--nested">
                        {projectGroup.drafts.map((draft) => (
                          <div key={draft.id} className="report-draft-row">
                            <div className="report-draft-row__main">
                              <Link
                                to={`/tests/new?draft=${draft.id}`}
                                className="report-list-item"
                              >
                                <div className="report-list-item__meta">
                                  <span className="report-list-item__title">
                                    {draft.payload.name?.trim() || "Yeni test taslağı"}
                                  </span>
                                  <span className="report-list-item__sub">
                                    {draft.current_step}. adımda bırakıldı · Son değişiklik{" "}
                                    {new Date(draft.updated_at).toLocaleString("tr-TR")}
                                  </span>
                                </div>
                                <span className="report-list-item__action">Devam et →</span>
                              </Link>
                              {draftToDelete !== draft.id && (
                                <button
                                  type="button"
                                  className="project-draft-row__delete"
                                  onClick={() => setDraftToDelete(draft.id)}
                                  aria-label={`${draft.payload.name?.trim() || "Yeni test taslağı"} taslağını sil`}
                                >
                                  Sil
                                </button>
                              )}
                            </div>
                            {draftToDelete === draft.id && (
                              <div className="inline-delete-confirm" role="alert">
                                <p>Bu yarım kalan test kalıcı olarak silinecek.</p>
                                <div>
                                  <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => setDraftToDelete(null)}
                                    disabled={isDeletingDraft}
                                  >
                                    Vazgeç
                                  </button>
                                  <button
                                    type="button"
                                    className="btn-danger"
                                    onClick={() => void handleDeleteDraft(draft.id)}
                                    disabled={isDeletingDraft}
                                  >
                                    {isDeletingDraft ? "Siliniyor…" : "Silme işlemini onayla"}
                                  </button>
                                </div>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <div className="empty-state reports-inline-empty">
                  <p>Yarım kalan test bulunmuyor.</p>
                </div>
              )}
            </section>
          )}
        </>
      )}
    </section>
  );
}
