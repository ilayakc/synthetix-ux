import type { ThemePreference } from "../../api/client";
import { useTheme } from "../../theme/ThemeContext";
import type { MeDraft } from "./types";

interface AppearanceTabProps {
  draft: MeDraft;
  onChange: <K extends keyof MeDraft>(field: K, value: MeDraft[K]) => void;
  disabled: boolean;
}

const THEME_OPTIONS: {
  value: ThemePreference;
  label: string;
  title: string;
  description: string;
}[] = [
  {
    value: "system",
    label: "Sistem teması",
    title: "Sistem",
    description: "Cihazınızın görünüm tercihini izler.",
  },
  {
    value: "light",
    label: "Açık tema",
    title: "Açık",
    description: "Aydınlık ve ferah bir görünüm kullanır.",
  },
  {
    value: "dark",
    label: "Koyu tema",
    title: "Koyu",
    description: "Düşük ışıkta daha rahat bir görünüm sunar.",
  },
];

export default function AppearanceTab({ draft, onChange, disabled }: AppearanceTabProps) {
  const { previewTheme } = useTheme();

  const handleThemeChange = (theme: ThemePreference) => {
    onChange("theme", theme);
    // "Tema seçimi anında arayüzde uygulansın": kayittan bagimsiz, anlik onizleme.
    previewTheme(theme);
  };

  return (
    <div>
      <div className="wizard-field settings-appearance-theme">
        <div className="settings-appearance-theme__header">
          <strong>Tema</strong>
          <p>Size en rahat gelen panel görünümünü seçin.</p>
        </div>
        <div className="admin-theme-options" role="radiogroup" aria-label="Panel görünümü">
          {THEME_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={`admin-theme-option${draft.theme === option.value ? " is-selected" : ""}${disabled ? " is-disabled" : ""}`}
            >
              <input
                className="visually-hidden"
                type="radio"
                name="settings-theme"
                value={option.value}
                aria-label={option.label}
                checked={draft.theme === option.value}
                onChange={() => handleThemeChange(option.value)}
                disabled={disabled}
              />
              <span className={`admin-theme-option__preview is-${option.value}`} aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span className="admin-theme-option__copy">
                <strong>{option.title}</strong>
                <small>{option.description}</small>
              </span>
              <span className="admin-theme-option__check" aria-hidden="true" />
            </label>
          ))}
        </div>
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-compact-view">
          <input
            id="settings-compact-view"
            type="checkbox"
            checked={draft.compact_view}
            onChange={(event) => onChange("compact_view", event.target.checked)}
            disabled={disabled}
          />{" "}
          Kompakt görünüm
        </label>
      </div>

      <div className="wizard-field">
        <label>Bildirimler</label>
        <label className="wizard-radio-option">
          <input
            type="checkbox"
            checked={draft.notify_simulation_completed}
            onChange={(event) => onChange("notify_simulation_completed", event.target.checked)}
            disabled={disabled}
          />{" "}
          Simülasyon tamamlandı
        </label>
        <label className="wizard-radio-option">
          <input
            type="checkbox"
            checked={draft.notify_simulation_failed}
            onChange={(event) => onChange("notify_simulation_failed", event.target.checked)}
            disabled={disabled}
          />{" "}
          Simülasyon başarısız oldu
        </label>
        <label className="wizard-radio-option">
          <input
            type="checkbox"
            checked={draft.notify_report_ready}
            onChange={(event) => onChange("notify_report_ready", event.target.checked)}
            disabled={disabled}
          />{" "}
          Rapor hazır
        </label>
        <label className="wizard-radio-option">
          <input
            type="checkbox"
            checked={draft.notify_low_chip_balance}
            onChange={(event) => onChange("notify_low_chip_balance", event.target.checked)}
            disabled={disabled}
          />{" "}
          Düşük Chip bakiyesi
        </label>

        {draft.notify_low_chip_balance && (
          <div className="wizard-field">
            <label htmlFor="settings-low-chip-threshold">Düşük Chip bakiyesi eşiği</label>
            <input
              id="settings-low-chip-threshold"
              type="number"
              min={0}
              value={draft.low_chip_balance_threshold ?? ""}
              onChange={(event) =>
                onChange(
                  "low_chip_balance_threshold",
                  event.target.value === "" ? null : Math.max(0, Number(event.target.value)),
                )
              }
              disabled={disabled}
            />
            <p className="wizard-field-hint">
              Bakiyeniz bu değerin altına düştüğünde uygulama içinde uyarı gösterilir.
            </p>
          </div>
        )}

        <p className="wizard-field-hint">
          Bu tercihler yalnızca uygulama içi bildirim davranışını kontrol eder; e-posta sağlayıcısı
          henüz bağlı olmadığı için e-posta gönderimi yapılmaz.
        </p>
      </div>
    </div>
  );
}
