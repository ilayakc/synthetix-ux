import type { AnalysisModuleDefinition, QuoteResponse } from "../../api/client";
import ModuleCatalogCard from "./ModuleCatalogCard";
import type { StepProps } from "./types";

interface Step4Props extends StepProps {
  quote: QuoteResponse | null;
  quoteLoading: boolean;
  quoteError: string | null;
  chipBalance: number | null;
  moduleCatalog: AnalysisModuleDefinition[];
}

export default function Step4Modules({
  payload,
  onChange,
  quote,
  quoteLoading,
  quoteError,
  chipBalance,
  moduleCatalog,
}: Step4Props) {
  const selectableModules = moduleCatalog.filter((module) => module.selectable_in_wizard);
  const selected = new Set(payload.modules ?? []);

  const toggleModule = (moduleKey: string) => {
    const next = new Set(selected);
    if (next.has(moduleKey)) {
      next.delete(moduleKey);
    } else {
      next.add(moduleKey);
    }
    onChange("modules", Array.from(next));
  };

  const insufficientBalance =
    quote !== null &&
    !quote.free_entitlement_applicable &&
    chipBalance !== null &&
    chipBalance < quote.required_chips;

  const selectedModuleNames = selectableModules
    .filter((module) => selected.has(module.key))
    .map((module) => module.name);

  return (
    <div>
      <div className="wizard-field">
        <label>Analiz modülleri (isteğe bağlı)</label>
        <div className="module-card-grid">
          {selectableModules.map((module) => (
            <ModuleCatalogCard
              key={module.key}
              module={module}
              selected={selected.has(module.key)}
              onToggle={toggleModule}
            />
          ))}
        </div>
        <p className="wizard-field-hint">
          Temel ücretsiz haklar dışında kalan gelişmiş modüller Chip harcaması gerektirir.
        </p>
      </div>

      {quoteLoading && <p className="page-placeholder">Fiyat teklifi hesaplanıyor…</p>}
      {quoteError && <p className="auth-error">{quoteError}</p>}

      {quote && (
        <ul className="wizard-summary-list">
          <li>
            <span>Persona sayısı</span>
            <span>{quote.persona_count.toLocaleString("tr-TR")}</span>
          </li>
          <li>
            <span>Seçili modüller</span>
            <span>{selectedModuleNames.length > 0 ? selectedModuleNames.join(", ") : "Yok"}</span>
          </li>
          <li>
            <span>Kullanılacak ücretsiz hak</span>
            <span>{quote.free_entitlement_applicable ? "Evet" : "Yok"}</span>
          </li>
          <li>
            <span>Toplam Chip</span>
            <span>{quote.required_chips.toLocaleString("tr-TR")}</span>
          </li>
          <li>
            <span>Mevcut bakiye</span>
            <span>{chipBalance !== null ? chipBalance.toLocaleString("tr-TR") : "—"}</span>
          </li>
          <li>
            <span>Fiyatlandırma sürümü</span>
            <span>{quote.pricing_version}</span>
          </li>
        </ul>
      )}

      {quote && (
        <div className="wizard-quote-total">
          <span>Gerekli Chip</span>
          <span>{quote.required_chips.toLocaleString("tr-TR")}</span>
        </div>
      )}

      {insufficientBalance && (
        <p className="auth-error">
          Chip bakiyeniz bu testi başlatmak için yeterli değil. Lütfen persona sayısını azaltın veya
          Chip bakiyenizi artırın.
        </p>
      )}
    </div>
  );
}
