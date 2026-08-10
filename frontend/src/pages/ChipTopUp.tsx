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

const REQUEST_STATUS_ORDER: TopUpRequestResponse["status"][] = [
  "pending",
  "approved",
  "rejected",
];

const formatTry = (amount: number) => `${amount.toLocaleString("tr-TR")} TL`;

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
    <section aria-labelledby="chip-wallet-heading">
      <h1 id="chip-wallet-heading" className="page-heading">
        Chip Cüzdanı
      </h1>
      <p className="page-placeholder">
        Bakiyenizi görüntüleyin ve ihtiyacınıza uygun Chip paketini seçin. Çevrimiçi ödeme
        henüz açık değildir; talebiniz yönetici onayından sonra bakiyenize eklenir.
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

          <div className="wizard-field chip-package-section" style={{ marginTop: 24 }}>
            <h2 className="page-heading" style={{ fontSize: "1.125rem", marginBottom: 4 }}>
              Chip Paketleri
            </h2>
            <p className="page-placeholder">
              Tanıtım fiyatlarıdır. Bu ekranda kart veya ödeme bilgisi alınmaz.
            </p>
            <div className="wizard-radio-group chip-package-grid">
              {packages.map((pkg) => (
                <label key={pkg.key} className="wizard-radio-option chip-package-option">
                  <input
                    type="radio"
                    name="chip-package"
                    checked={selectedPackage === pkg.key}
                    onChange={() => setSelectedPackage(pkg.key)}
                  />
                  <span className="chip-package-option__content">
                    <span className="chip-package-option__heading">
                      <strong>{pkg.name}</strong>
                      {pkg.price_try !== null && (
                        <strong className="chip-package-option__price">
                          {formatTry(pkg.price_try)}
                        </strong>
                      )}
                    </span>
                    <span className="chip-package-option__amount">
                      {pkg.chip_amount.toLocaleString("tr-TR")} Chip
                    </span>
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
            <div className="topup-request-groups">
              {REQUEST_STATUS_ORDER.map((status) => {
                const statusRequests = requests.filter((request) => request.status === status);
                const headingId = `topup-status-${status}`;
                return (
                  <section
                    key={status}
                    className={`topup-request-group topup-request-group--${status}`}
                    aria-labelledby={headingId}
                  >
                    <div className="topup-request-group__heading">
                      <h3 id={headingId}>{STATUS_LABELS[status]}</h3>
                      <span className="chip-pill">{statusRequests.length}</span>
                    </div>
                    {statusRequests.length === 0 ? (
                      <p className="page-placeholder">Bu durumda talep yok.</p>
                    ) : (
                      <ul className="wizard-summary-list">
                        {statusRequests.map((request) => (
                          <li key={request.id}>
                            <span>
                              {request.chip_amount.toLocaleString("tr-TR")} Chip ·{" "}
                              {packageNames.get(request.package_key) ??
                                "Paket bilgisi bulunamadı"}
                            </span>
                            <span>{new Date(request.created_at).toLocaleString("tr-TR")}</span>
                            {request.review_note && (
                              <span>Yönetici notu: {request.review_note}</span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                );
              })}
            </div>
          )}
        </>
      )}
    </section>
  );
}
