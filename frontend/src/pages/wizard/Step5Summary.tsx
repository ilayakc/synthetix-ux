import { useEffect, useState } from "react";
import {
  ApiError,
  type AnalysisModuleDefinition,
  type DesignAssetResponse,
  type QuoteResponse,
  getDesignAsset,
  getDesignAssetPreviewUrl,
} from "../../api/client";
import { TEST_TYPE_LABELS, type StepProps } from "./types";

const CONTENT_TYPE_LABELS: Record<string, string> = {
  "image/png": "PNG",
  "image/jpeg": "JPEG",
  "image/webp": "WebP",
};

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

  const isAbComparison = payload.test_type === "ab_comparison";
  const isScreenshotSource = payload.current_source_type === "screenshot";
  const isNewAiGenerated = isAbComparison && payload.new_source_type === "ai_generated";
  const isNewScreenshotSource =
    isAbComparison && (payload.new_source_type === "screenshot" || isNewAiGenerated);
  const hasVisualSource = isScreenshotSource || isNewScreenshotSource;
  // Onay metni kaynak turune gore degisir - "Tasarim A"/"Tasarim B" ikisi de
  // screenshot/AI ise "Bu URL'leri..." yazmak yanlis/yanıltıcı olur (bkz.
  // Paket 4 Final sonrasi bulunan gercek kullanici hatasi raporu).
  const hasUrlSource = !isScreenshotSource || (isAbComparison && !isNewScreenshotSource);
  const consentLabel =
    hasVisualSource && !hasUrlSource
      ? "Bu tasarım görsellerini analiz etme ve kullanma yetkisine sahip olduğumu onaylıyorum."
      : hasVisualSource && hasUrlSource
        ? "Bu URL ve tasarım görsellerini analiz etme yetkisine sahip olduğumu onaylıyorum."
        : "Bu URL'leri analiz etme yetkisine sahip olduğumu onaylıyorum.";

  const [assetMeta, setAssetMeta] = useState<DesignAssetResponse | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [newAssetMeta, setNewAssetMeta] = useState<DesignAssetResponse | null>(null);
  const [newAssetError, setNewAssetError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!isScreenshotSource || !payload.current_design_asset_id) {
      setAssetMeta(null);
      setAssetError(null);
      return;
    }
    getDesignAsset(payload.current_design_asset_id)
      .then((meta) => {
        if (!cancelled) {
          setAssetMeta(meta);
          setAssetError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setAssetMeta(null);
          setAssetError(
            err instanceof ApiError
              ? "Görsel artık kullanılamıyor (silinmiş veya süresi dolmuş olabilir)."
              : "Görsel bilgisi yüklenemedi.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isScreenshotSource, payload.current_design_asset_id]);

  useEffect(() => {
    let cancelled = false;
    if (!isNewScreenshotSource || !payload.new_design_asset_id) {
      setNewAssetMeta(null);
      setNewAssetError(null);
      return;
    }
    getDesignAsset(payload.new_design_asset_id)
      .then((meta) => {
        if (!cancelled) {
          setNewAssetMeta(meta);
          setNewAssetError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setNewAssetMeta(null);
          setNewAssetError(
            err instanceof ApiError
              ? "Görsel artık kullanılamıyor (silinmiş veya süresi dolmuş olabilir)."
              : "Görsel bilgisi yüklenemedi.",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isNewScreenshotSource, payload.new_design_asset_id]);

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
          <span>{isAbComparison ? "Tasarım A kaynağı" : "Kaynak türü"}</span>
          <span>{isScreenshotSource ? "Ekran görüntüsü" : "URL"}</span>
        </li>
        {isScreenshotSource ? (
          <li>
            <span>{isAbComparison ? "Tasarım A ekran görüntüsü" : "Ekran görüntüsü"}</span>
            <span>
              {assetError ? (
                assetError
              ) : assetMeta ? (
                <span className="wizard-summary-screenshot">
                  <img
                    src={getDesignAssetPreviewUrl(payload.current_design_asset_id as string)}
                    alt="Yüklenen tasarım ekran görüntüsü önizlemesi"
                    className="wizard-summary-screenshot-image"
                  />
                  <span>
                    {CONTENT_TYPE_LABELS[assetMeta.content_type] ?? assetMeta.content_type},{" "}
                    {assetMeta.width}×{assetMeta.height}px
                  </span>
                </span>
              ) : (
                "—"
              )}
            </span>
          </li>
        ) : (
          <li>
            <span>{isAbComparison ? "Tasarım A URL" : "URL"}</span>
            <span>{payload.current_url || "—"}</span>
          </li>
        )}
        {isAbComparison && (
          <>
            <li>
              <span>Tasarım B kaynağı</span>
              <span>
                {isNewAiGenerated ? "AI ile üretildi" : isNewScreenshotSource ? "Ekran görüntüsü" : "URL"}
              </span>
            </li>
            {isNewScreenshotSource ? (
              <li>
                <span>{isNewAiGenerated ? "AI tasarım taslağı" : "Tasarım B ekran görüntüsü"}</span>
                <span>
                  {newAssetError ? (
                    newAssetError
                  ) : newAssetMeta ? (
                    <span className="wizard-summary-screenshot">
                      <img
                        src={getDesignAssetPreviewUrl(payload.new_design_asset_id as string)}
                        alt={
                          isNewAiGenerated
                            ? "AI ile üretilen tasarım taslağı önizlemesi"
                            : "Yüklenen alternatif tasarım ekran görüntüsü önizlemesi"
                        }
                        className="wizard-summary-screenshot-image"
                      />
                      <span>
                        {isNewAiGenerated && (
                          <span className="auth-notice" style={{ display: "block" }}>
                            AI tarafından üretilmiş tasarım taslağı
                          </span>
                        )}
                        {CONTENT_TYPE_LABELS[newAssetMeta.content_type] ?? newAssetMeta.content_type},{" "}
                        {newAssetMeta.width}×{newAssetMeta.height}px
                      </span>
                    </span>
                  ) : (
                    "—"
                  )}
                </span>
              </li>
            ) : (
              <li>
                <span>Tasarım B URL</span>
                <span>{payload.new_url || "—"}</span>
              </li>
            )}
          </>
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
          <span>Ek analiz modülleri</span>
          <span>
            {modules.length > 0
              ? modules.map((m) => moduleNamesByKey.get(m) ?? m).join(", ")
              : "Seçilmedi (yalnızca Temel UX testi çalıştırılacak)"}
          </span>
        </li>
      </ul>

      {hasVisualSource && (
        <p className="auth-notice" role="status">
          {isAbComparison
            ? "Görsel tabanlı A/B kaynaklarınız kaydedildi. Analiz, sayfa görselinin piksel özelliklerinden (OpenCV tabanlı) yürütülür - gerçek DOM verisi değildir."
            : "Ekran görüntünüz kaydedildi. Analiz, görselin piksel özelliklerinden (OpenCV tabanlı) yürütülür - gerçek DOM verisi değildir."}
        </p>
      )}
      {payload.current_cta_annotation && (
        <p className="wizard-field-hint" role="status">
          {isAbComparison ? "Tasarım A" : "Tasarım"} için bir CTA alanı onayladınız - analiz bu alanı
          öncelikli CTA olarak kullanacak.
        </p>
      )}
      {isAbComparison && payload.new_cta_annotation && (
        <p className="wizard-field-hint" role="status">
          Tasarım B için bir CTA alanı onayladınız - analiz bu alanı öncelikli CTA olarak kullanacak.
        </p>
      )}

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
          {consentLabel} Sonuçlar sentetik ve kalibre edilmemiş tahminlerdir, gerçek kullanıcı
          davranışı olarak sunulamaz.
        </span>
      </label>
      {fieldErrors.authorization_confirmed && (
        <p className="auth-error">{fieldErrors.authorization_confirmed}</p>
      )}
    </div>
  );
}
