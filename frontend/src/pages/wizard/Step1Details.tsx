import { useEffect, useState } from "react";
import { type ProjectResponse, listProjects } from "../../api/client";
import { TEST_TYPE_DESCRIPTIONS, TEST_TYPE_LABELS, type StepProps } from "./types";

const TEST_TYPES = Object.keys(TEST_TYPE_LABELS);

export default function Step1Details({ payload, fieldErrors, onChange }: StepProps) {
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  return (
    <div>
      <div className="wizard-field">
        <label htmlFor="wizard-project">Proje</label>
        <select
          id="wizard-project"
          value={payload.project_id ?? ""}
          onChange={(event) => onChange("project_id", event.target.value)}
        >
          <option value="" disabled>
            Proje seçin
          </option>
          {(projects ?? []).map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
            </option>
          ))}
        </select>
        {projects && projects.length === 0 && (
          <p className="wizard-field-hint">
            Henüz bir projeniz yok; devam etmeden önce Projeler sayfasından bir proje oluşturun.
          </p>
        )}
        {fieldErrors.project_id && <p className="auth-error">{fieldErrors.project_id}</p>}
      </div>

      <div className="wizard-field">
        <label htmlFor="wizard-name">Test adı</label>
        <input
          id="wizard-name"
          value={payload.name ?? ""}
          onChange={(event) => onChange("name", event.target.value)}
          maxLength={255}
        />
        {fieldErrors.name && <p className="auth-error">{fieldErrors.name}</p>}
      </div>

      <div className="wizard-field">
        <label htmlFor="wizard-target-task">Hedef görev</label>
        <textarea
          id="wizard-target-task"
          rows={3}
          value={payload.target_task ?? ""}
          onChange={(event) => onChange("target_task", event.target.value)}
          placeholder="Örn. Kullanıcının sepete ürün ekleyip ödemeyi tamamlaması"
        />
        {fieldErrors.target_task && <p className="auth-error">{fieldErrors.target_task}</p>}
      </div>

      <div className="wizard-field">
        <label>Ana test türü</label>
        <p className="wizard-field-hint">
          Burada testin temel amacını seçersiniz. Bu ana analiz 4. adımda yeniden seçilmez.
        </p>
        <div className="wizard-radio-group" role="radiogroup" aria-label="Ana test türü">
          {TEST_TYPES.map((type) => (
            <label key={type} className="wizard-radio-option">
              <input
                type="radio"
                name="test_type"
                value={type}
                checked={payload.test_type === type}
                onChange={() => onChange("test_type", type as StepProps["payload"]["test_type"])}
              />
              <span className="wizard-test-type-option__content">
                <span className="wizard-test-type-option__title">{TEST_TYPE_LABELS[type]}</span>
                <span className="wizard-test-type-option__description">
                  {TEST_TYPE_DESCRIPTIONS[type]}
                </span>
              </span>
            </label>
          ))}
        </div>
        {fieldErrors.test_type && <p className="auth-error">{fieldErrors.test_type}</p>}
      </div>
    </div>
  );
}
