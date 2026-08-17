import { useEffect, useRef, useState } from "react";
import { type EntitlementStatus, type ProjectResponse, listProjects } from "../../api/client";
import { TARGET_TASK_EXAMPLE_HINT } from "./targetTaskValidation";
import { TEST_TYPE_DESCRIPTIONS, TEST_TYPE_LABELS, type StepProps } from "./types";

const TEST_TYPES = Object.keys(TEST_TYPE_LABELS);

interface Step1DetailsProps extends StepProps {
  entitlementStatuses: Record<string, EntitlementStatus>;
}

function testTypePriceStatus(
  testType: string,
  entitlementStatuses: Record<string, EntitlementStatus>,
): {
  label: string;
  detail: string | null;
  tone: "free" | "paid" | "reserved" | "unknown";
} {
  const featureKey =
    testType === "accessibility_precheck" ? "accessibility_precheck" : "basic_ux_test";
  const status = entitlementStatuses[featureKey];
  const paidCost = testType === "accessibility_precheck" ? "30 Chip" : "Persona başına 1 Chip";

  if (status === "available") {
    return {
      label:
        testType === "ab_comparison"
          ? "Temel UX ücretsiz hakkıyla kullanılabilir"
          : "1 ücretsiz kullanım hakkı mevcut",
      detail: `Sonraki kullanımlar: ${paidCost}`,
      tone: "free",
    };
  }
  if (status === "consumed") {
    return { label: paidCost, detail: null, tone: "paid" };
  }
  if (status === "reserved") {
    return {
      label: paidCost,
      detail: "Ücretsiz hak devam eden testte ayrıldı",
      tone: "reserved",
    };
  }
  return {
    label:
      testType === "ab_comparison"
        ? "Temel UX ücretsiz hakkını paylaşır"
        : "Tek kullanımlık ücretsiz hakla kullanılabilir",
    detail: `Sonraki kullanımlar: ${paidCost}`,
    tone: "unknown",
  };
}

export default function Step1Details({
  payload,
  fieldErrors,
  onChange,
  entitlementStatuses,
}: Step1DetailsProps) {
  const [projects, setProjects] = useState<ProjectResponse[] | null>(null);
  const targetTaskRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    listProjects()
      .then(setProjects)
      .catch(() => setProjects([]));
  }, []);

  // Hedef gorev hatasi olustugunda (İleri'ye basildiginda veya launch 422'si
  // alanla eslendiginde) odagi hatali alana tasi - kullanici hatayi hemen
  // gorur ve duzeltebilir.
  const targetTaskError = fieldErrors.target_task;
  useEffect(() => {
    if (targetTaskError) targetTaskRef.current?.focus();
  }, [targetTaskError]);

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
          ref={targetTaskRef}
          rows={3}
          value={payload.target_task ?? ""}
          onChange={(event) => onChange("target_task", event.target.value)}
          placeholder="Örn. Kullanıcının sepete ürün ekleyip ödemeyi tamamlaması"
          aria-invalid={targetTaskError ? true : undefined}
          aria-describedby={
            targetTaskError ? "wizard-target-task-error" : "wizard-target-task-hint"
          }
        />
        {targetTaskError ? (
          <p id="wizard-target-task-error" className="auth-error">
            {targetTaskError}
          </p>
        ) : (
          <p id="wizard-target-task-hint" className="wizard-field-hint">
            {TARGET_TASK_EXAMPLE_HINT}
          </p>
        )}
      </div>

      <div className="wizard-field">
        <label>Ana test türü</label>
        <p className="wizard-field-hint">
          Burada testin temel amacını seçersiniz. Bu ana analiz 4. adımda yeniden seçilmez.
        </p>
        <div className="wizard-radio-group" role="radiogroup" aria-label="Ana test türü">
          {TEST_TYPES.map((type) => {
            const priceStatus = testTypePriceStatus(type, entitlementStatuses);
            return (
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
                  <span
                    className={`wizard-test-type-option__pricing wizard-test-type-option__pricing--${priceStatus.tone}`}
                  >
                    {priceStatus.label}
                  </span>
                  {priceStatus.detail && (
                    <span className="wizard-test-type-option__pricing-detail">
                      {priceStatus.detail}
                    </span>
                  )}
                </span>
              </label>
            );
          })}
        </div>
        {fieldErrors.test_type && <p className="auth-error">{fieldErrors.test_type}</p>}
      </div>
    </div>
  );
}
