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

  // Bu dört alan yalnizca gorsel bir on-izlemedir: hicbir backend cagrisina
  // eklenmez, hicbir yere gonderilmez veya kaydedilmez (bkz. handleSubmit -
  // yalnizca `selectedPackage` gonderilir). Gercek bir odeme saglayicisi
  // baglandiginda bu alanlarin yerini saglayicinin kendi guvenli
  // formu/SDK'si alacaktir.
  const [cardNumber, setCardNumber] = useState("");
  const [cardName, setCardName] = useState("");
  const [cardExpiry, setCardExpiry] = useState("");
  const [cardCvv, setCardCvv] = useState("");

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
        "Yükleme talebiniz kaydedildi. Chip bakiyeniz henüz değişmedi; gerçek ödeme sağlayıcısı " +
          "entegrasyonu yakında aktif olacak ve talebiniz o zaman işleme alınacaktır.",
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
        Bir Chip paketi seçip yükleme talebi gönderin. Aşağıdaki kart bilgileri alanı yalnızca bir
        önizlemedir: girilen değerler hiçbir sunucuya gönderilmez veya kaydedilmez; gerçek ödeme
        sağlayıcısı entegrasyonu henüz aktif değildir.
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

          <div className="wizard-field" style={{ marginTop: 16 }}>
            <label>Kart bilgileri</label>
            <p className="wizard-field-hint">
              Bu alanlar bir önizlemedir; girdiğiniz bilgiler hiçbir sunucuya gönderilmez veya
              kaydedilmez. Gerçek ödeme sağlayıcısı entegrasyonu aktif olduğunda kart bilgileriniz
              yalnızca o sağlayıcının güvenli formu üzerinden alınacaktır.
            </p>
            <div className="wizard-field" style={{ marginTop: 8 }}>
              <label htmlFor="chip-topup-card-number">Kart numarası</label>
              <input
                id="chip-topup-card-number"
                type="text"
                inputMode="numeric"
                autoComplete="off"
                placeholder="0000 0000 0000 0000"
                maxLength={19}
                value={cardNumber}
                onChange={(event) => setCardNumber(event.target.value)}
              />
            </div>
            <div className="wizard-field" style={{ marginTop: 8 }}>
              <label htmlFor="chip-topup-card-name">Kart üzerindeki isim</label>
              <input
                id="chip-topup-card-name"
                type="text"
                autoComplete="off"
                placeholder="Ad Soyad"
                value={cardName}
                onChange={(event) => setCardName(event.target.value)}
              />
            </div>
            <div style={{ display: "flex", gap: 12, marginTop: 8 }}>
              <div className="wizard-field" style={{ flex: 1 }}>
                <label htmlFor="chip-topup-card-expiry">Son kullanma tarihi</label>
                <input
                  id="chip-topup-card-expiry"
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder="AA/YY"
                  maxLength={5}
                  value={cardExpiry}
                  onChange={(event) => setCardExpiry(event.target.value)}
                />
              </div>
              <div className="wizard-field" style={{ flex: 1 }}>
                <label htmlFor="chip-topup-card-cvv">Güvenlik kodu</label>
                <input
                  id="chip-topup-card-cvv"
                  type="text"
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder="CVV"
                  maxLength={4}
                  value={cardCvv}
                  onChange={(event) => setCardCvv(event.target.value)}
                />
              </div>
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
                    {request.chip_amount.toLocaleString("tr-TR")} Chip ({request.package_key})
                  </span>
                  <span>{STATUS_LABELS[request.status]}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
