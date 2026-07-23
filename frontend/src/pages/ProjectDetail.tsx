import { type FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  type ProjectResponse,
  archiveProject,
  getProject,
  updateProject,
} from "../api/client";

const STATUS_LABELS: Record<ProjectResponse["status"], string> = {
  active: "Aktif",
  archived: "Arşivlendi",
};

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const [project, setProject] = useState<ProjectResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isArchiving, setIsArchiving] = useState(false);
  const [isConfirmingArchive, setIsConfirmingArchive] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    getProject(projectId)
      .then((data) => {
        setProject(data);
        setName(data.name);
        setDescription(data.description ?? "");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setError("Proje yüklenemedi.");
        }
      });
  }, [projectId]);

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
    return <p className="page-placeholder">{error}</p>;
  }

  if (!project) {
    return <p className="page-placeholder">Yükleniyor…</p>;
  }

  const isArchived = project.status === "archived";

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

      <div className="dashboard-grid">
        <div className="dashboard-card">
          <h3>Test Sayısı</h3>
          <p>{project.test_count}</p>
        </div>
      </div>

      <form className="auth-form project-detail__form" onSubmit={handleSave}>
        <label htmlFor="project-detail-name">Proje adı</label>
        <input
          id="project-detail-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          disabled={isArchived}
          required
          maxLength={255}
        />

        <label htmlFor="project-detail-description">Kısa açıklama</label>
        <input
          id="project-detail-description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          disabled={isArchived}
          maxLength={2000}
        />

        {saveError && <p className="auth-error">{saveError}</p>}
        {isArchived && <p className="page-placeholder">Arşivlenmiş projeler düzenlenemez.</p>}

        <div className="modal__actions">
          {!isArchived && (
            <button type="submit" className="auth-submit" disabled={isSaving || !name.trim()}>
              {isSaving ? "Kaydediliyor…" : "Kaydet"}
            </button>
          )}
          <button
            type="button"
            onClick={() => navigate(`/tests/new?project=${project.id}`)}
            className="btn-secondary"
            disabled={isArchived}
          >
            Bu proje için yeni test başlat
          </button>
        </div>
      </form>

      {!isArchived && (
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
    </section>
  );
}
