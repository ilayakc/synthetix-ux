import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  type AnalysisModuleCatalogResponse,
  ApiError,
  getAnalysisModuleCatalog,
  getUsageSummary,
  type EntitlementStatus,
} from "../api/client";
import ModuleCatalogCard from "./wizard/ModuleCatalogCard";

export default function ModuleCatalog() {
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<AnalysisModuleCatalogResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [staged, setStaged] = useState<string[]>([]);
  const [entitlementStatuses, setEntitlementStatuses] = useState<Record<string, EntitlementStatus>>(
    {},
  );

  useEffect(() => {
    let cancelled = false;

    Promise.all([getAnalysisModuleCatalog(), getUsageSummary().catch(() => null)])
      .then(([data, usage]) => {
        if (!cancelled) {
          setCatalog(data);
          setEntitlementStatuses(
            Object.fromEntries(
              (usage?.entitlements ?? []).map((entitlement) => [
                entitlement.feature_key,
                entitlement.status,
              ]),
            ),
          );
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Modül kataloğu yüklenemedi.");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const toggleStaged = (key: string) => {
    setStaged((current) =>
      current.includes(key) ? current.filter((k) => k !== key) : [...current, key],
    );
  };

  const removeStaged = (key: string) => {
    setStaged((current) => current.filter((k) => k !== key));
  };

  const startNewTestWithSelection = () => {
    if (staged.length === 0) return;
    const params = new URLSearchParams({ modules: staged.join(",") });
    navigate(`/tests/new?${params.toString()}`);
  };

  const stagedModules = (catalog?.modules ?? []).filter((module) => staged.includes(module.key));

  return (
    <section aria-labelledby="module-catalog-heading">
      <h1 id="module-catalog-heading" className="page-heading">
        Analiz Modülleri Kataloğu
      </h1>
      <p className="page-placeholder">
        {catalog
          ? `Katalog sürümü: ${catalog.catalog_version}. Bir modülü yeni bir testte kullanmak için "Yeni testte kullan" düğmesine basın.`
          : "Kullanılabilir analiz modülleri ve ücretlendirmeleri aşağıda listelenir."}
      </p>

      {isLoading && <p className="page-placeholder">Yükleniyor…</p>}
      {error && <p className="auth-error">{error}</p>}

      {catalog && (
        <div className="module-card-grid">
          {catalog.modules.map((module) => (
            <ModuleCatalogCard
              key={module.key}
              module={module}
              queued={staged.includes(module.key)}
              onUseInTest={toggleStaged}
              entitlementStatus={
                module.free_entitlement_feature_key
                  ? entitlementStatuses[module.free_entitlement_feature_key]
                  : undefined
              }
            />
          ))}
        </div>
      )}

      {staged.length > 0 && (
        <div className="wizard-panel test-prep-panel" aria-labelledby="test-prep-heading">
          <h2 id="test-prep-heading" className="page-heading test-prep-heading">
            Test hazırlığı
          </h2>
          <p className="wizard-field-hint">
            Yeni bir test oluşturduğunuzda bu modüller sihirbazın 4. adımında önceden seçili gelir.
          </p>
          <ul className="wizard-summary-list">
            {stagedModules.map((module) => (
              <li key={module.key}>
                <span>{module.name}</span>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => removeStaged(module.key)}
                >
                  Kaldır
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="auth-submit" onClick={startNewTestWithSelection}>
            Seçtiklerimle yeni test oluştur
          </button>
        </div>
      )}
    </section>
  );
}
