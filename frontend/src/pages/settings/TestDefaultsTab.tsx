import type {
  AnalysisModuleDefinition,
  OrganizationSettingsResponse,
  PersonaPresetResponse,
} from "../../api/client";
import { DEVICE_PROFILE_LABELS, type OrgDraft } from "./types";

interface TestDefaultsTabProps {
  snapshot: OrganizationSettingsResponse;
  draft: OrgDraft;
  onChange: <K extends keyof OrgDraft>(field: K, value: OrgDraft[K]) => void;
  savingDisabled: boolean;
  personaPresets: PersonaPresetResponse[];
  moduleCatalog: AnalysisModuleDefinition[];
}

export default function TestDefaultsTab({
  snapshot,
  draft,
  onChange,
  savingDisabled,
  personaPresets,
  moduleCatalog,
}: TestDefaultsTabProps) {
  const canEdit = snapshot.can_edit_defaults;
  const fieldsDisabled = savingDisabled || !canEdit;
  const selectableModules = moduleCatalog.filter((module) => module.selectable_in_wizard);
  const selectedModules = new Set(draft.default_modules);

  const toggleModule = (key: string) => {
    const next = new Set(selectedModules);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    onChange("default_modules", Array.from(next));
  };

  return (
    <div>
      {!canEdit && (
        <p className="wizard-field-hint">
          Bu sekmeyi yalnızca test oluşturma yetkisi olan kullanıcılar (analist, yönetici, şirket
          sahibi) düzenleyebilir.
        </p>
      )}

      {snapshot.warnings.map((warning) => (
        <p key={warning} className="auth-error" role="alert">
          {warning}
        </p>
      ))}

      <div className="wizard-field">
        <label htmlFor="settings-persona-count">Varsayılan persona sayısı</label>
        <input
          id="settings-persona-count"
          type="number"
          min={100}
          max={50000}
          value={draft.default_persona_count}
          onChange={(event) => onChange("default_persona_count", Number(event.target.value))}
          disabled={fieldsDisabled}
        />
        <p className="wizard-field-hint">100 ile 50.000 arasında bir değer olmalıdır.</p>
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-persona-preset">Varsayılan persona preseti</label>
        <select
          id="settings-persona-preset"
          value={draft.default_persona_preset_id ?? ""}
          onChange={(event) => onChange("default_persona_preset_id", event.target.value || null)}
          disabled={fieldsDisabled}
        >
          <option value="">Yok</option>
          {personaPresets.map((preset) => (
            <option key={preset.id} value={preset.id}>
              {preset.name}
            </option>
          ))}
        </select>
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-device-profile">Varsayılan cihaz profili</label>
        <select
          id="settings-device-profile"
          value={draft.default_device_profile ?? ""}
          onChange={(event) => onChange("default_device_profile", event.target.value || null)}
          disabled={fieldsDisabled}
        >
          <option value="">Yok</option>
          {Object.entries(DEVICE_PROFILE_LABELS).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </div>

      <div className="wizard-field">
        <label>Varsayılan analiz modülleri</label>
        <div className="module-card-grid">
          {selectableModules.map((module) => (
            <label key={module.key} className="wizard-radio-option">
              <input
                type="checkbox"
                checked={selectedModules.has(module.key)}
                onChange={() => toggleModule(module.key)}
                disabled={fieldsDisabled}
              />{" "}
              {module.name}
            </label>
          ))}
        </div>
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-target-audience">Varsayılan hedef kitle açıklaması</label>
        <textarea
          id="settings-target-audience"
          value={draft.default_target_audience ?? ""}
          onChange={(event) => onChange("default_target_audience", event.target.value)}
          maxLength={2000}
          disabled={fieldsDisabled}
        />
      </div>

      <p className="not-real-data-tag">
        Bilimsel dürüstlük uyarısı tüm sonuç ekranlarında otomatik olarak gösterilir.
      </p>
      <p className="methodology-note">
        Bu varsayılanlar yalnızca yeni oluşturulacak test sihirbazı taslaklarının başlangıç
        değerleridir; mevcut taslakları, çalıştırmaları veya rapor anlık görüntülerini etkilemez.
        Kullanıcı, sihirbaz içinde bu değerleri her zaman değiştirebilir.
      </p>
    </div>
  );
}
