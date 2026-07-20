import type { MeSettingsResponse } from "../../api/client";
import type { MeDraft } from "./types";

interface ProfileTabProps {
  snapshot: MeSettingsResponse;
  draft: MeDraft;
  onChange: <K extends keyof MeDraft>(field: K, value: MeDraft[K]) => void;
  disabled: boolean;
}

export default function ProfileTab({ snapshot, draft, onChange, disabled }: ProfileTabProps) {
  return (
    <div>
      <div className="wizard-field">
        <label htmlFor="settings-display-name">Görünen ad</label>
        <input
          id="settings-display-name"
          value={draft.display_name ?? ""}
          onChange={(event) => onChange("display_name", event.target.value)}
          maxLength={255}
          disabled={disabled}
        />
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-email">E-posta adresi</label>
        <input id="settings-email" value={snapshot.email} disabled readOnly />
        <p className="wizard-field-hint">
          E-posta değiştirme özelliği güvenli doğrulama akışıyla daha sonra eklenecek.
        </p>
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-language">Dil</label>
        <select
          id="settings-language"
          value={draft.language}
          onChange={(event) => onChange("language", event.target.value)}
          disabled={disabled}
        >
          <option value="tr">Türkçe</option>
        </select>
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-timezone">Saat dilimi</label>
        <input
          id="settings-timezone"
          value={draft.timezone}
          onChange={(event) => onChange("timezone", event.target.value)}
          placeholder="Europe/Istanbul"
          disabled={disabled}
        />
        <p className="wizard-field-hint">IANA saat dilimi biçimi, örn. "Europe/Istanbul".</p>
      </div>
    </div>
  );
}
