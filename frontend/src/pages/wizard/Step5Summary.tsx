import type { AnalysisModuleDefinition, QuoteResponse } from "../../api/client";
import { TEST_TYPE_LABELS, type StepProps } from "./types";

interface Step5Props extends StepProps {
  quote: QuoteResponse | null;
  quoteLoading: boolean;
  quoteError: string | null;
  chipBalance: number | null;
  moduleCatalog: AnalysisModuleDefinition[];
}

export default function Step5Summary({
  payload,
  fieldErrors,
  onChange,
  quote,
  quoteLoading,
  quoteError,
  chipBalance,
  moduleCatalog,
}: Step5Props) {
  const moduleNamesByKey = new Map(moduleCatalog.map((module) => [module.key, module.name]));
  const modules = payload.modules ?? [];
  const insufficientBalance =
    quote !== null &&
    !quote.free_entitlement_applicable &&
    chipBalance !== null &&
    chipBalance < quote.required_chips;

  return (
    <div>
      <ul className="wizard-summary-list">
        <li>
          <span>Test türü</span>
          <span>{payload.test_type ? TEST_TYPE_LABELS[payload.test_type] : "—"}</span>
        </li>
        <li>
          <span>Test adı</span>
          <span>{payload.name || "—"}</span>
        </li>
        <li>
          <span>Hedef görev</span>
          <span>{payload.target_task || "—"}</span>
        </li>
        <li>
          <span>URL</span>
          <span>{payload.current_url || "—"}</span>
        </li>
        {payload.test_type === "ab_comparison" && (
          <li>
            <span>Yeni tasarım URL</span>
            <span>{payload.new_url || "—"}</span>
          </li>
        )}
        <li>
          <span>Persona sayısı</span>
          <span>{payload.persona_count?.toLocaleString("tr-TR") ?? "—"}</span>
        </li>
        <li>
          <span>Hedef kitle</span>
          <span>{payload.target_audience || "—"}</span>
        </li>
        <li>
          <span>Analiz modülleri</span>
          <span>
            {modules.length > 0
              ? modules.map((m) => moduleNamesByKey.get(m) ?? m).join(", ")
              : "Yok"}
          </span>
        </li>
      </ul>

      {quoteLoading && <p className="page-placeholder">Fiyat teklifi hesaplanıyor…</p>}
      {quoteError && <p className="auth-error">{quoteError}</p>}

      {quote && (
        <>
          <ul className="wizard-summary-list">
            {quote.line_items.map((item) => (
              <li key={item.key}>
                <span>{item.label}</span>
                <span>{item.chip_cost === 0 ? "Ücretsiz" : `${item.chip_cost} Chip`}</span>
              </li>
            ))}
          </ul>
          <div className="wizard-quote-total">
            <span>Toplam</span>
            <span>
              {quote.free_entitlement_applicable
                ? "Ücretsiz hak"
                : `${quote.required_chips.toLocaleString("tr-TR")} Chip`}
            </span>
          </div>
          {chipBalance !== null && !quote.free_entitlement_applicable && (
            <p className="wizard-field-hint">
              Mevcut Chip bakiyeniz: {chipBalance.toLocaleString("tr-TR")}
            </p>
          )}
          {insufficientBalance && (
            <p className="auth-error">
              Chip bakiyeniz bu testi başlatmak için yeterli değil. Lütfen persona sayısını azaltın
              veya Chip bakiyenizi artırın; sahte/başlatılmamış bir işlem oluşturulmaz.
            </p>
          )}
        </>
      )}

      <label className="wizard-radio-option" style={{ marginTop: 16 }}>
        <input
          type="checkbox"
          checked={payload.authorization_confirmed === true}
          onChange={(event) => onChange("authorization_confirmed", event.target.checked)}
        />
        <span>
          Bu URL'leri test etme yetkisine sahip olduğumu onaylıyorum. Sonuçlar sentetik ve kalibre
          edilmemiş tahminlerdir, gerçek kullanıcı davranışı olarak sunulamaz.
        </span>
      </label>
      {fieldErrors.authorization_confirmed && (
        <p className="auth-error">{fieldErrors.authorization_confirmed}</p>
      )}
    </div>
  );
}
