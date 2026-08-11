import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  type ProjectResponse,
  type ReportListItemResponse,
  type WizardDraftResponse,
  archiveProject,
  deleteProject,
  deleteWizardDraft,
  getProject,
  listReports,
  listWizardDrafts,
  updateProject,
} from "../api/client";
import { useOptionalAuth } from "../auth/AuthContext";

const STATUS_LABELS: Record<ProjectResponse["status"], string> = {
  active: "Aktif",
  archived: "Arşivlendi",
};

export default function ProjectDetail() {
  const isDemo = Boolean(useOptionalAuth()?.session?.is_demo);
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [reports, setReports] = useState<ReportListItemResponse[]>([]);
  const [drafts, setDrafts] = useState<WizardDraftResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isArchiving, setIsArchiving] = useState(false);
  const [isConfirmingArchive, setIsConfirmingArchive] = useState(false);
  const [draftToDelete, setDraftToDelete] = useState<string | null>(null);
  const [isDeletingDraft, setIsDeletingDraft] = useState(false);
  const [isConfirmingDeleteProject, setIsConfirmingDeleteProject] = useState(false);
  const [isDeletingProject, setIsDeletingProject] = useState(false);

  const load = useCallback(() => {
    if (!projectId) return;
    setError(null);
    Promise.all([getProject(projectId), listReports({ projectId }), listWizardDrafts()])
      .then(([projectData, reportData, draftData]) => {
        setProject(projectData);
        setReports(reportData);
        setDrafts(draftData.filter((draft) => draft.payload.project_id === projectId));
        setName(projectData.name);
        setDescription(projectData.description ?? "");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError("Proje yüklenemedi.");
        }
      });
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const completedTests = useMemo(() => {
    const byDefinition = new Map<string, ReportListItemResponse>();
    for (const report of reports) {
      if (!byDefinition.has(report.test_definition_id)) {
        byDefinition.set(report.test_definition_id, report);
      }
    }
    return [...byDefinition.values()];
  }, [reports]);

  if (notFound) {
    return (
      <section aria-labelledby="project-detail-heading">
        <h1 id="project-detail-heading" className="page-heading">
          Proje bulunamadı
        </h1>
        <Link to="/projeler">Projeler listesine dön</Link>
      </section>
    );
  }

  if (error) {
    return (
      <div className="dashboard-error" role="alert">
        <p>{error}</p>
        <button type="button" className="btn-secondary" onClick={load}>
          Yeniden dene
        </button>
      </div>
    );
  }

  if (!project) {
    return <p className="page-placeholder">Yükleniyor…</p>;
  }

  const isArchived = project.status === "archived";
  const processingCount = Math.max(project.test_count - completedTests.length, 0);
  const totalWorkCount = project.test_count + drafts.length;

  const handleSave = async (event: FormEvent) => {
    event.preventDefault();
    setSaveError(null);
    setIsSaving(true);
    try {
      const updated = await updateProject(project.id, {
        name: name.trim(),
        description: description.trim() ? description.trim() : null,
      });
      setProject(updated);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Proje güncellenemedi.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleArchive = async () => {
    setIsArchiving(true);
    try {
      const updated = await archiveProject(project.id);
      setProject(updated);
      setIsConfirmingArchive(false);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Proje arşivlenemedi.");
    } finally {
      setIsArchiving(false);
    }
  };

  const handleDeleteDraft = async (draftId: string) => {
    setSaveError(null);
    setIsDeletingDraft(true);
    try {
      await deleteWizardDraft(draftId);
      setDrafts((current) => current.filter((draft) => draft.id !== draftId));
      setDraftToDelete(null);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Yarım kalan test silinemedi.");
    } finally {
      setIsDeletingDraft(false);
    }
  };

  const handleDeleteProject = async () => {
    setSaveError(null);
    setIsDeletingProject(true);
    try {
      await deleteProject(project.id);
      navigate("/projeler", { replace: true });
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Proje silinemedi.");
    } finally {
      setIsDeletingProject(false);
    }
  };

  return (
    <section aria-labelledby="project-detail-heading">
      <Link to="/projeler" className="project-detail__back">
        ← Projeler
      </Link>
      <div className="project-detail__header">
        <h1 id="project-detail-heading" className="page-heading">
          {project.name}
        </h1>
        <span className={`status-badge status-badge--${project.status}`}>
          {STATUS_LABELS[project.status]}
        </span>
      </div>

      <div className="dashboard-grid project-detail__summary">
        <div className="dashboard-card">
          <h3>Toplam Çalışma</h3>
          <p>{totalWorkCount}</p>
          <small>{project.test_count} başlatılmış test · {drafts.length} taslak</small>
        </div>
        <div className="dashboard-card">
          <h3>Tamamlanan Test</h3>
          <p>{completedTests.length}</p>
          <small>Raporu hazır olan benzersiz testler</small>
        </div>
        <div className="dashboard-card">
          <h3>Devam Eden</h3>
          <p>{processingCount + drafts.length}</p>
          <small>{processingCount} sonuç bekliyor · {drafts.length} yarım kaldı</small>
        </div>
      </div>

      <section className="project-detail__tests" aria-labelledby="project-tests-heading">
        <div className="dashboard-section__heading-row">
          <div>
            <h2 id="project-tests-heading">Proje Testleri</h2>
            <p>Tamamlanan testlere, yarım kalan taslaklara ve süren çalışmalara buradan ulaşın.</p>
          </div>
          {!isArchived && !isDemo && (
            <button
              type="button"
              onClick={() => navigate(`/tests/new?project=${project.id}`)}
              className="btn-primary"
            >
              Yeni test başlat
            </button>
          )}
        </div>

        {totalWorkCount === 0 && (
          <div className="empty-state">
            <p>Bu projede henüz test veya taslak bulunmuyor.</p>
          </div>
        )}

        {totalWorkCount > 0 && (
          <div className="project-test-group__content project-detail__test-list">
            {drafts.length > 0 && (
              <div className="project-test-subsection">
                <h3>Yarım kalan testler</h3>
                <ul>
                  {drafts.map((draft) => (
                    <li key={draft.id} className="project-draft-row">
                      <div className="project-draft-row__main">
                        <Link to={`/tests/new?draft=${draft.id}`}>
                          <span>
                            <strong>{draft.payload.name?.trim() || "Adsız test taslağı"}</strong>
                            <small>{draft.current_step}. adımda bırakıldı</small>
                          </span>
                          <span>Devam et →</span>
                        </Link>
                        {!isDemo && draftToDelete !== draft.id && (
                          <button
                            type="button"
                            className="project-draft-row__delete"
                            onClick={() => setDraftToDelete(draft.id)}
                            aria-label={`${draft.payload.name?.trim() || "Adsız test taslağı"} taslağını sil`}
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
                          <small>Rapor hazır · {new Date(report.created_at).toLocaleString("tr-TR")}</small>
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
                {processingCount} test çalışıyor, başarısız oldu veya sonuç bekliyor →
              </Link>
            )}
          </div>
        )}
      </section>

      <form className="auth-form project-detail__form" onSubmit={handleSave}>
        <label htmlFor="project-detail-name">Proje adı</label>
        <input
          id="project-detail-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={isArchived || isDemo}
          required
          maxLength={255}
        />

        <label htmlFor="project-detail-description">Kısa açıklama</label>
        <input
          id="project-detail-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          disabled={isArchived || isDemo}
          maxLength={2000}
        />

        {saveError && <p className="auth-error">{saveError}</p>}
        {isArchived && <p className="page-placeholder">Arşivlenmiş projeler düzenlenemez.</p>}

        <div className="modal__actions">
          {!isArchived && !isDemo && (
            <button type="submit" className="auth-submit" disabled={isSaving || !name.trim()}>
              {isSaving ? "Kaydediliyor…" : "Kaydet"}
            </button>
          )}
        </div>
      </form>

      {!isArchived && !isDemo && (
        <div className="project-detail__archive">
          {!isConfirmingArchive ? (
            <button
              type="button"
              className="project-detail__archive-trigger"
              onClick={() => setIsConfirmingArchive(true)}
            >
              Projeyi arşivle
            </button>
          ) : (
            <div className="auth-notice">
              <p>
                Proje arşivlendiğinde listede görünmeyecek ancak verileri (testler, sonuçlar) kalıcı
                olarak silinmeyecektir.
              </p>
              <div className="modal__actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setIsConfirmingArchive(false)}
                >
                  Vazgeç
                </button>
                <button
                  type="button"
                  className="auth-submit"
                  onClick={handleArchive}
                  disabled={isArchiving}
                >
                  {isArchiving ? "Arşivleniyor…" : "Arşivlemeyi onayla"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {!isArchived && !isDemo && (
        <section className="project-detail__danger-zone" aria-labelledby="delete-project-heading">
          <h2 id="delete-project-heading">Projeyi sil</h2>
          {completedTests.length > 0 ? (
            <p>
              Bu projede {completedTests.length} tamamlanmış ve Chip harcanmış test bulunuyor. Proje
              ve tamamlanan raporları silinemez; isterseniz projeyi arşivleyebilirsiniz.
            </p>
          ) : !isConfirmingDeleteProject ? (
            <>
              <p>
                Proje aktif listeden kaldırılır ve yarım kalan taslakları silinir. Tamamlanmış rapor
                bulunmadığı için bu işlem kullanılabilir.
              </p>
              <button
                type="button"
                className="btn-danger-outline"
                onClick={() => setIsConfirmingDeleteProject(true)}
              >
                Projeyi sil
              </button>
            </>
          ) : (
            <div className="inline-delete-confirm" role="alert">
              <p>“{project.name}” projesini silmek istediğinizden emin misiniz?</p>
              <div>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setIsConfirmingDeleteProject(false)}
                  disabled={isDeletingProject}
                >
                  Vazgeç
                </button>
                <button
                  type="button"
                  className="btn-danger"
                  onClick={() => void handleDeleteProject()}
                  disabled={isDeletingProject}
                >
                  {isDeletingProject ? "Siliniyor…" : "Projeyi silmeyi onayla"}
                </button>
              </div>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
