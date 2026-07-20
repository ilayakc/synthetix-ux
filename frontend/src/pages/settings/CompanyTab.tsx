import { Link } from "react-router-dom";
import type { OrganizationSettingsResponse } from "../../api/client";
import { CURRENCY_LABELS, type OrgDraft } from "./types";

interface CompanyTabProps {
  snapshot: OrganizationSettingsResponse;
  draft: OrgDraft;
  onChange: <K extends keyof OrgDraft>(field: K, value: OrgDraft[K]) => void;
  savingDisabled: boolean;
}

export default function CompanyTab({ snapshot, draft, onChange, savingDisabled }: CompanyTabProps) {
  const canEdit = snapshot.can_edit_company;
  const fieldsDisabled = savingDisabled || !canEdit;

  return (
    <div>
      <div className="wizard-field">
        <label htmlFor="settings-org-name">Şirket adı</label>
        <input
          id="settings-org-name"
          value={draft.name}
          onChange={(event) => onChange("name", event.target.value)}
          maxLength={255}
          disabled={fieldsDisabled}
        />
        {!canEdit && (
          <p className="wizard-field-hint">
            Bu alanı yalnızca şirket sahibi veya yöneticisi düzenleyebilir.
          </p>
        )}
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-org-slug">Şirket kimliği (slug)</label>
        <input id="settings-org-slug" value={snapshot.slug} disabled readOnly />
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-org-role">Şirketteki rolünüz</label>
        <input id="settings-org-role" value={snapshot.role} disabled readOnly />
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-org-created">Şirket oluşturulma tarihi</label>
        <input
          id="settings-org-created"
          value={new Date(snapshot.created_at).toLocaleDateString("tr-TR")}
          disabled
          readOnly
        />
      </div>

      <div className="wizard-field">
        <label htmlFor="settings-org-currency">Varsayılan para birimi</label>
        <select
          id="settings-org-currency"
          value={draft.currency}
          onChange={(event) => onChange("currency", event.target.value)}
          disabled={fieldsDisabled}
        >
          {Object.entries(CURRENCY_LABELS).map(([code, label]) => (
            <option key={code} value={code}>
              {label}
            </option>
          ))}
        </select>
        <p className="wizard-field-hint">
          Bu ayar yalnızca görüntüleme amaçlıdır; gerçek bir ödeme veya kur çevrimi başlatmaz.
        </p>
        {!canEdit && (
          <p className="wizard-field-hint">
            Bu alanı yalnızca şirket sahibi veya yöneticisi düzenleyebilir.
          </p>
        )}
      </div>

      <p className="wizard-field-hint">
        Chip bakiyeniz ve kullanım hakkı durumunuz için <Link to="/kullanim-ve-chip">Kullanım ve Chip</Link>{" "}
        sayfasına bakın.
      </p>
    </div>
  );
}
