import { setConsent, useConsent } from "../lib/analytics";

// Zorunlu olmayan analitik izni için hafif bir onay şeridi. Karar verilene kadar
// gösterilir; kullanıcı reddederse pazarlama analitiği üretilmez (bkz.
// frontend/src/lib/analytics.ts ve backend ANALYTICS_REQUIRE_CONSENT).
export default function ConsentBanner() {
  const consent = useConsent();
  if (consent !== null) return null;

  return (
    <div className="consent-banner" role="region" aria-label="Analitik ve gizlilik tercihi">
      <p className="consent-banner__text">
        Ürünü iyileştirmek için <strong>gizlilik dostu, anonim</strong> kullanım analitiği
        ölçüyoruz. IP adresiniz saklanmaz, parmak izi çıkarılmaz. Dilerseniz reddedebilirsiniz.
      </p>
      <div className="consent-banner__actions">
        <button
          type="button"
          className="consent-banner__button"
          onClick={() => setConsent("denied")}
        >
          Reddet
        </button>
        <button
          type="button"
          className="consent-banner__button consent-banner__button--accept"
          onClick={() => setConsent("granted")}
        >
          Kabul et
        </button>
      </div>
    </div>
  );
}
