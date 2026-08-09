import { useEffect, useState } from "react";
import {
  ApiError,
  type ChipPackage,
  type TopUpRequestResponse,
  createTopUpRequest,
  getChipPackages,
  getUsageSummary,
  listTopUpRequests,
} from "../api/client";

const STATUS_LABELS: Record<TopUpRequestResponse["status"], string> = {
  pending: "Beklemede",
  approved: "Onaylandı",
  rejected: "Reddedildi",
};

export default function ChipTopUp() {
  const [chipBalance, setChipBalance] = useState<number | null>(null);
  const [packages, setPackages] = useState<ChipPackage[]>([]);
  const [requests, setRequests] = useState<TopUpRequestResponse[]>([]);
  const [selectedPackage, setSelectedPackage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const packageNames = new Map(packages.map((pkg) => [pkg.key, pkg.name]));

  const loadRequests = () => {
    listTopUpRequests()
      .then(setRequests)
      .catch(() => setRequests([]));
  };

  useEffect(() => {
    let cancelled = false;

    Promise.all([getUsageSummary(), getChipPackages(), listTopUpRequests()])
      .then(([summary, packageList, topUpRequests]) => {
        if (cancelled) return;
        setChipBalance(summary.chip_balance);
        setPackages(packageList.packages);
        setSelectedPackage(packageList.packages[0]?.key ?? null);
        setRequests(topUpRequests);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : "Chip yükleme bilgileri yüklenemedi.");
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async () => {
    if (!selectedPackage) return;
    setIsSubmitting(true);
    setError(null);
    setSuccessMessage(null);
    try {
      await createTopUpRequest(selectedPackage);
      setSuccessMessage(
        "Yükleme talebiniz yönetime iletildi. Talep onaylandığında Chip bakiyeniz otomatik güncellenecektir.",
      );
      loadRequests();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Yükleme talebi gönderilemedi.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <section aria-labelledby="chip-topup-heading">
      <h1 id="chip-topup-heading" className="page-heading">
        Chip Yükle
      </h1>
      <p className="page-placeholder">
        Bir Chip paketi seçip yönetime yükleme talebi gönderin. Talep oluşturmak bakiyenizi hemen
        değiştirmez; yönetici onayından sonra Chip hesabınıza eklenir.
      </p>

      {isLoading && <p className="page-placeholder">Yükleniyor…</p>}
      {error && <p className="auth-error">{error}</p>}
      {successMessage && <p className="auth-notice">{successMessage}</p>}

      {!isLoading && (
        <>
          <div className="dashboard-grid">
            <div className="dashboard-card">
              <h3>Mevcut Chip Bakiyesi</h3>
              <p>{chipBalance ?? "—"}</p>
            </div>
          </div>

          <div className="wizard-field" style={{ marginTop: 16 }}>
            <label>Chip paketi seçin</label>
            <div className="wizard-radio-group">
              {packages.map((pkg) => (
                <label key={pkg.key} className="wizard-radio-option">
                  <input
                    type="radio"
                    name="chip-package"
                    checked={selectedPackage === pkg.key}
                    onChange={() => setSelectedPackage(pkg.key)}
                  />
                  <span>
                    {pkg.name} — {pkg.chip_amount.toLocaleString("tr-TR")} Chip
                  </span>
                </label>
              ))}
            </div>
          </div>

          <div className="wizard-actions">
            <button
              type="button"
              className="auth-submit"
              onClick={handleSubmit}
              disabled={isSubmitting || !selectedPackage}
            >
              {isSubmitting ? "Gönderiliyor…" : "Yükleme talebi gönder"}
            </button>
          </div>

          <h2 className="page-heading" style={{ marginTop: 32, fontSize: "1.125rem" }}>
            Geçmiş talepler
          </h2>
          {requests.length === 0 ? (
            <p className="page-placeholder">Henüz bir yükleme talebiniz yok.</p>
          ) : (
            <ul className="wizard-summary-list">
              {requests.map((request) => (
                <li key={request.id}>
                  <span>
                    {request.chip_amount.toLocaleString("tr-TR")} Chip ·{" "}
                    {packageNames.get(request.package_key) ?? "Paket bilgisi bulunamadı"}
                  </span>
                  <span>{STATUS_LABELS[request.status]}</span>
                  {request.review_note && <span>Yönetici notu: {request.review_note}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
