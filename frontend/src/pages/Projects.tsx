import { type FormEvent, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, type ProjectResponse, createProject, listProjects } from "../api/client";
import { useOptionalAuth } from "../auth/AuthContext";

const STATUS_LABELS: Record<ProjectResponse["status"], string> = {
  active: "Aktif",
  archived: "Arşivlendi",
};

function StatusBadge({ status }: { status: ProjectResponse["status"] }) {
  return <span className={`status-badge status-badge--${status}`}>{STATUS_LABELS[status]}</span>;
}

function ProjectCard({ project }: { project: ProjectResponse }) {
  return (
    <Link to={`/projeler/${project.id}`} className="project-card">
      <div className="project-card__header">
        <h3>{project.name}</h3>
        <StatusBadge status={project.status} />
      </div>
      <p className="project-card__description">
        {project.description?.trim() ? project.description : "Açıklama eklenmedi."}
      </p>
      <div className="project-card__footer">
        <span className="chip-pill">{project.test_count} başlatılmış test</span>
      </div>
    </Link>
  );
}

function NewProjectCard({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="project-card project-card--new" onClick={onClick}>
      <span className="project-card--new__icon" aria-hidden="true">
        +
      </span>
      <span>Yeni Proje</span>
    </button>
  );
}

interface CreateProjectModalProps {
  onClose: () => void;
  onCreated: (project: ProjectResponse) => void;
}

function CreateProjectModal({ onClose, onCreated }: CreateProjectModalProps) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const project = await createProject({
        name: name.trim(),
        description: description.trim() ? description.trim() : null,
      });
      onCreated(project);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Proje oluşturulamadı.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-overlay" role="presentation" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-heading"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="create-project-heading">Yeni Proje</h2>
        <form className="auth-form" onSubmit={handleSubmit}>
          <label htmlFor="project-name">Proje adı</label>
          <input
            id="project-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={255}
          />

          <label htmlFor="project-description">Kısa açıklama</label>
          <input
            id="project-description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            maxLength={2000}
          />

          {error && <p className="auth-error">{error}</p>}

          <div className="modal__actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Vazgeç
            </button>
            <button type="submit" className="auth-submit" disabled={isSubmitting || !name.trim()}>
              {isSubmitting ? "Oluşturuluyor…" : "Oluştur"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function Projects() {
  const isDemo = Boolean(useOptionalAuth()?.session?.is_demo);
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const load = () => {
    setError(null);
    listProjects()
      .then(setProjects)
      .catch(() => setError("Projeler yüklenemedi."));
  };

  useEffect(load, []);

  return (
    <section aria-labelledby="projects-heading">
      <h1 id="projects-heading" className="page-heading">
        Projeler
      </h1>
      <p className="page-placeholder">
        Test projelerinizi buradan yönetin. Arşivlenen projeler kalıcı olarak silinmez.
      </p>

      {error && <p className="page-placeholder">{error}</p>}

      {projects && projects.length === 0 && (
        <div className="empty-state">
          <p>Henüz bir projeniz yok.</p>
          {!isDemo && (
            <button type="button" className="auth-submit" onClick={() => setIsModalOpen(true)}>
              İlk projenizi oluşturun
            </button>
          )}
        </div>
      )}

      {projects && projects.length > 0 && (
        <div className="project-grid">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
          {!isDemo && <NewProjectCard onClick={() => setIsModalOpen(true)} />}
        </div>
      )}

      {isModalOpen && !isDemo && (
        <CreateProjectModal
          onClose={() => setIsModalOpen(false)}
          onCreated={(project) => {
            setIsModalOpen(false);
            setProjects((current) => [project, ...(current ?? [])]);
          }}
        />
      )}
    </section>
  );
}
