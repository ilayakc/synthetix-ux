import type { StepProps } from "./types";

export default function Step2Urls({ payload, fieldErrors, onChange }: StepProps) {
  const isAbComparison = payload.test_type === "ab_comparison";

  return (
    <div>
      <div className="wizard-field">
        <label htmlFor="wizard-current-url">
          {isAbComparison ? "Mevcut tasarım URL'si" : "Test edilecek URL"}
        </label>
        <input
          id="wizard-current-url"
          type="url"
          value={payload.current_url ?? ""}
          onChange={(event) => onChange("current_url", event.target.value)}
          placeholder="https://example.com"
        />
        <p className="wizard-field-hint">
          URL yalnızca biçim olarak doğrulanır; bu aşamada ziyaret edilmez veya taranmaz.
        </p>
        {fieldErrors.current_url && <p className="auth-error">{fieldErrors.current_url}</p>}
      </div>

      {isAbComparison && (
        <div className="wizard-field">
          <label htmlFor="wizard-new-url">Yeni tasarım URL'si</label>
          <input
            id="wizard-new-url"
            type="url"
            value={payload.new_url ?? ""}
            onChange={(event) => onChange("new_url", event.target.value)}
            placeholder="https://staging.example.com"
          />
          {fieldErrors.new_url && <p className="auth-error">{fieldErrors.new_url}</p>}
        </div>
      )}
    </div>
  );
}
