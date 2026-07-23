import type { AnalysisModuleDefinition } from "../../api/client";

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
}

interface ModuleCatalogCardCatalogProps {
  module: AnalysisModuleDefinition;
  selected?: undefined;
  onToggle?: undefined;
  /** Katalog sayfasindaki "test hazirligi" tepsisine eklenmis mi. */
  queued: boolean;
  onUseInTest: (key: string) => void;
}

type ModuleCatalogCardProps = ModuleCatalogCardWizardProps | ModuleCatalogCardCatalogProps;

function ModuleCatalogCardBody({ module }: { module: AnalysisModuleDefinition }) {
  const costLabel =
    module.chip_cost === 0
      ? module.free_entitlement_feature_key
        ? "Ücretsiz hak uygunluğu var"
        : "Ücretsiz"
      : `${module.chip_cost.toLocaleString("tr-TR")} Chip`;

  return (
    <>
      <p className="page-placeholder">{module.description}</p>
      <ul className="module-card__outputs">
        {module.outputs.map((output) => (
          <li key={output}>{output}</li>
        ))}
      </ul>
      <div className="module-card__meta">
        <span className="status-badge status-badge--active">
          {MEASUREMENT_TYPE_LABELS[module.measurement_type]}
        </span>
        <span>{costLabel}</span>
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
    const { selected, onToggle } = props;
    return (
      <label className={`module-card${selected ? " module-card--selected" : ""}`}>
        <div className="module-card__header">
          <input type="checkbox" checked={selected} onChange={() => onToggle(module.key)} />
          <h3>{module.name}</h3>
        </div>
        <ModuleCatalogCardBody module={module} />
      </label>
    );
  }

  const { queued, onUseInTest } = props;
  return (
    <div className={`module-card module-card--static${queued ? " module-card--selected" : ""}`}>
      <div className="module-card__header">
        <h3>{module.name}</h3>
      </div>
      <ModuleCatalogCardBody module={module} />
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
