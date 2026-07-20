import type { ThemePreference } from "../../api/client";
import { useTheme } from "../../theme/ThemeContext";
import type { MeDraft } from "./types";

interface AppearanceTabProps {
  draft: MeDraft;
  onChange: <K extends keyof MeDraft>(field: K, value: MeDraft[K]) => void;
  disabled: boolean;
}

const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "Sistem teması" },
  { value: "light", label: "Açık tema" },
  { value: "dark", label: "Koyu tema" },
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
      <div className="wizard-field">
        <label>Tema</label>
        <div className="wizard-radio-group">
          {THEME_OPTIONS.map((option) => (
            <label key={option.value} className="wizard-radio-option">
              <input
                type="radio"
                name="settings-theme"
                value={option.value}
                checked={draft.theme === option.value}
                onChange={() => handleThemeChange(option.value)}
                disabled={disabled}
              />{" "}
              {option.label}
            </label>
          ))}
        </div>
        <p className="wizard-field-hint">
          "Sistem teması" işletim sisteminizin açık/koyu tercihini otomatik olarak izler.
        </p>
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
          Bu tercihler yalnızca uygulama içi bildirim davranışını kontrol eder; e-posta sağlayıcısı henüz
          bağlı olmadığı için e-posta gönderimi yapılmaz.
        </p>
      </div>
    </div>
  );
}
