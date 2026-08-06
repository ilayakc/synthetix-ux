import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { type EntitlementSummary, type UsageSummaryResponse, getUsageSummary } from "../api/client";

const FEATURE_LABELS: Record<string, string> = {
  basic_ux_test: "Ücretsiz Temel UX Testi",
  accessibility_precheck: "Ücretsiz Erişilebilirlik Ön Kontrolü",
};

const STATUS_LABELS: Record<EntitlementSummary["status"], string> = {
  available: "Kullanılabilir",
  reserved: "Rezerve edildi",
  consumed: "Kullanıldı",
};

function EntitlementCard({ entitlement }: { entitlement: EntitlementSummary }) {
  const used = entitlement.status === "consumed" ? entitlement.quantity : 0;
  return (
    <div className="dashboard-card">
      <h3>{FEATURE_LABELS[entitlement.feature_key] ?? entitlement.feature_key}</h3>
      <p>
        {used}/{entitlement.quantity}
      </p>
      <p className="page-placeholder">{STATUS_LABELS[entitlement.status]}</p>
    </div>
  );
}

export default function KullanimVeChip() {
  const [summary, setSummary] = useState<UsageSummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    getUsageSummary()
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch(() => {
        if (!cancelled) setError("Kullanım özeti yüklenemedi.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section aria-labelledby="kullanim-ve-chip-heading">
      <div className="usage-overview__header">
        <div>
          <h1 id="kullanim-ve-chip-heading" className="page-heading">
            Kullanım ve Chip
          </h1>
          <p className="page-placeholder">
            Bakiyenizi ve ücretsiz test haklarınızı tek yerden takip edin.
          </p>
        </div>
        <Link to="/chip-yukle" className="auth-submit usage-overview__topup-button">
          Chip Yükle
        </Link>
      </div>

      {isLoading && <p className="page-placeholder">Yükleniyor…</p>}
      {error && <p className="page-placeholder">{error}</p>}

      {summary && (
        <div className="dashboard-grid">
          <div className="dashboard-card">
            <h3>Chip Bakiyesi</h3>
            <p>{summary.chip_balance}</p>
          </div>
          {summary.entitlements.map((entitlement) => (
            <EntitlementCard key={entitlement.feature_key} entitlement={entitlement} />
          ))}
        </div>
      )}
    </section>
  );
}
