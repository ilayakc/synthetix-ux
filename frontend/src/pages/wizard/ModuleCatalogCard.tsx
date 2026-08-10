import type { AnalysisModuleDefinition, EntitlementStatus } from "../../api/client";
import { normalizeTurkishSystemCopy } from "../../lib/turkishCopy";

const MEASUREMENT_TYPE_LABELS: Record<AnalysisModuleDefinition["measurement_type"], string> = {
  technical_measurement: "Teknik ölçüm",
  synthetic_estimate: "Sentetik tahmin",
};

interface ModuleCatalogCardWizardProps {
  module: AnalysisModuleDefinition;
  selected: boolean;
  onToggle: (key: string) => void;
  onUseInTest?: undefined;
  queued?: undefined;
  /** Modul, taslagin GUNCEL tasarim kaynagi turleriyle (bkz.
   * moduleCompatibility.ts) uyumsuz oldugu icin secilemez. */
  disabled?: boolean;
  /** `disabled` true iken gosterilen, erisilebilir (screen-reader'in da
   * okuyabilecegi) kisa aciklama. */
  disabledReason?: string;
}

interface ModuleCatalogCardCatalogProps {
  module: AnalysisModuleDefinition;
  selected?: undefined;
  onToggle?: undefined;
  /** Katalog sayfasindaki "test hazirligi" tepsisine eklenmis mi. */
  queued: boolean;
  onUseInTest: (key: string) => void;
  entitlementStatus?: EntitlementStatus;
}

type ModuleCatalogCardProps = ModuleCatalogCardWizardProps | ModuleCatalogCardCatalogProps;

interface ModulePriceDisplay {
  primary: string;
  secondary: string | null;
}

function freeEntitlementPrice(
  module: AnalysisModuleDefinition,
  status?: EntitlementStatus,
): ModulePriceDisplay {
  const paidLabel = module.post_entitlement_cost_label ?? "Chip ile kullanılabilir";
  if (status === "consumed") return { primary: paidLabel, secondary: null };
  if (status === "reserved") {
    return {
      primary: paidLabel,
      secondary: "Ücretsiz hak devam eden testte ayrıldı",
    };
  }
  if (status === "available") {
    return {
      primary:
        module.key === "ab_comparison"
          ? "Temel UX ücretsiz hakkıyla kullanılabilir"
          : "1 ücretsiz kullanım hakkı mevcut",
      secondary: `Sonraki kullanımlar: ${paidLabel}`,
    };
  }
  return {
    primary:
      module.key === "ab_comparison"
        ? "Temel UX ücretsiz hakkını paylaşır"
        : "Tek kullanımlık ücretsiz hakla kullanılabilir",
    secondary: `Sonraki kullanımlar: ${paidLabel}`,
  };
}

function ModuleCatalogCardBody({
  module,
  entitlementStatus,
}: {
  module: AnalysisModuleDefinition;
  entitlementStatus?: EntitlementStatus;
}) {
  const priceDisplay: ModulePriceDisplay = module.free_entitlement_feature_key
    ? freeEntitlementPrice(module, entitlementStatus)
    : {
        primary:
          module.chip_cost === 0 ? "Ücretsiz" : `${module.chip_cost.toLocaleString("tr-TR")} Chip`,
        secondary: null,
      };

  return (
    <>
      <p className="page-placeholder">{normalizeTurkishSystemCopy(module.description)}</p>
      <ul className="module-card__outputs">
        {module.outputs.map((output) => (
          <li key={output}>{normalizeTurkishSystemCopy(output)}</li>
        ))}
      </ul>
      <div className="module-card__meta">
        <span className="status-badge status-badge--active">
          {MEASUREMENT_TYPE_LABELS[module.measurement_type]}
        </span>
        <span className="module-card__pricing">
          <span>{priceDisplay.primary}</span>
          {priceDisplay.secondary && <small>{priceDisplay.secondary}</small>}
        </span>
        <span>~{module.estimated_duration_minutes} dk</span>
      </div>
    </>
  );
}

/**
 * Sihirbazin 4. adiminda (Step4Modules) gercek bir secim yapan checkbox karti
 * ile Analiz Modulleri kataloginda yalnizca bilgilendirme ve "yeni testte
 * kullan" eylemi sunan salt-okunur kart, ayni bilesimden ama iki farkli
 * moddan render edilir. Katalog modunda sahte/etkisiz bir checkbox gosterilmez.
 */
export default function ModuleCatalogCard(props: ModuleCatalogCardProps) {
  const { module } = props;

  if (props.onToggle) {
    const { selected, onToggle, disabled, disabledReason } = props;
    return (
      <label
        className={`module-card${selected ? " module-card--selected" : ""}${disabled ? " module-card--disabled" : ""}`}
        aria-disabled={disabled || undefined}
      >
        <div className="module-card__header">
          <input
            type="checkbox"
            checked={selected}
            disabled={disabled}
            onChange={() => onToggle(module.key)}
          />
          <h3>{normalizeTurkishSystemCopy(module.name)}</h3>
        </div>
        <ModuleCatalogCardBody module={module} />
        {disabled && disabledReason && (
          <p className="wizard-field-hint" role="status">
            {disabledReason}
          </p>
        )}
      </label>
    );
  }

  const { queued, onUseInTest, entitlementStatus } = props;
  return (
    <div className={`module-card module-card--static${queued ? " module-card--selected" : ""}`}>
      <div className="module-card__header">
        <h3>{normalizeTurkishSystemCopy(module.name)}</h3>
      </div>
      <ModuleCatalogCardBody module={module} entitlementStatus={entitlementStatus} />
      <div className="module-card__actions">
        {module.selectable_in_wizard ? (
          <button type="button" className="btn-secondary" onClick={() => onUseInTest(module.key)}>
            {queued ? "Test hazırlığından çıkar" : "Yeni testte kullan"}
          </button>
        ) : (
          <p className="wizard-field-hint">Test türüne göre otomatik eklenir.</p>
        )}
      </div>
    </div>
  );
}
