import DesignSourcePicker from "./DesignSourcePicker";
import ScreenshotCtaSelector from "./ScreenshotCtaSelector";
import type { WizardCurrentSourceType, WizardNewSourceType } from "../../api/client";
import type { StepProps } from "./types";

const LEGACY_AI_DRAFT_WARNING =
  "Bu taslakta artık desteklenmeyen bir AI tasarım denemesi bulunuyor. Devam etmek için URL veya ekran görüntüsü seçin.";

export default function Step2Urls({ payload, fieldErrors, onChange, draftId }: StepProps) {
  const isAbComparison = payload.test_type === "ab_comparison";
  const isAccessibilityPrecheck = payload.test_type === "accessibility_precheck";

  // Eski taslaklarda "Tasarim B" kaynagi artik kaldirilmis "AI ile oluştur"
  // secenegiyle ("ai_generated") kaydedilmis olabilir. Kabul edilmis (bir
  // DesignAsset'e donusmus) sonuclar sessizce normal bir ekran goruntusu
  // kaynagi gibi gosterilir - asset SILINMEZ/degistirilmez, yalnizca kaynak
  // turu etiketi yeniden yorumlanir. Henuz kabul edilmemis/yarim kalmis bir
  // AI denemesi icin ise kullanici acikca yeni bir kaynak (URL/ekran
  // goruntusu) secene kadar hicbir kaynak onceden secili gosterilmez (bkz.
  // asagidaki `legacyUnacceptedAiDraft`).
  const newSourceIsLegacyAi = payload.new_source_type === "ai_generated";
  const legacyUnacceptedAiDraft = newSourceIsLegacyAi && !payload.new_design_asset_id;
  const effectiveNewSourceType: WizardNewSourceType | undefined = legacyUnacceptedAiDraft
    ? undefined
    : newSourceIsLegacyAi
      ? "screenshot"
      : payload.new_source_type;

  // A/B karşılaştırması: hem "Tasarım A" (current_*) hem "Tasarım B" (new_*)
  // tarafı bağımsız olarak URL veya ekran görüntüsü kaynağı seçebilir (bkz.
  // docs/product-rules.md). Görsel kaynaklarla test yine de BAŞLATILAMAZ
  // (bkz. Step5Summary ve backend launch_draft) - bu adımda yalnızca
  // kaydedilir.
  if (isAbComparison) {
    return (
      <div>
        <DesignSourcePicker
          label="Tasarım A — Orijinal tasarım"
          sourceType={payload.current_source_type}
          url={payload.current_url}
          assetId={payload.current_design_asset_id}
          urlLabel="Mevcut tasarım URL'si"
          urlFieldId="wizard-current-url"
          urlError={fieldErrors.current_url}
          assetError={fieldErrors.current_design_asset_id}
          onSourceTypeChange={(sourceType) =>
            onChange("current_source_type", sourceType as WizardCurrentSourceType)
          }
          onUrlChange={(value) => onChange("current_url", value)}
          onAssetChange={(assetId) => onChange("current_design_asset_id", assetId ?? undefined)}
        />
        {payload.current_source_type === "screenshot" && (
          <ScreenshotCtaSelector
            slot="current"
            label="Tasarım A"
            draftId={draftId ?? null}
            assetId={payload.current_design_asset_id}
            annotation={payload.current_cta_annotation}
            onAnnotationChange={(value) => onChange("current_cta_annotation", value)}
          />
        )}

        {legacyUnacceptedAiDraft && (
          <p className="auth-notice" role="status">
            {LEGACY_AI_DRAFT_WARNING}
          </p>
        )}
        <DesignSourcePicker
          label="Tasarım B — Alternatif tasarım"
          sourceType={effectiveNewSourceType}
          url={payload.new_url}
          assetId={legacyUnacceptedAiDraft ? undefined : payload.new_design_asset_id}
          urlLabel="Yeni tasarım URL'si"
          urlFieldId="wizard-new-url"
          urlError={fieldErrors.new_url}
          assetError={fieldErrors.new_design_asset_id}
          onSourceTypeChange={(sourceType) =>
            onChange("new_source_type", sourceType as WizardNewSourceType)
          }
          onUrlChange={(value) => onChange("new_url", value)}
          onAssetChange={(assetId) => onChange("new_design_asset_id", assetId ?? undefined)}
        />
        {effectiveNewSourceType === "screenshot" && (
          <ScreenshotCtaSelector
            slot="new"
            label="Tasarım B"
            draftId={draftId ?? null}
            assetId={payload.new_design_asset_id}
            annotation={payload.new_cta_annotation}
            onAnnotationChange={(value) => onChange("new_cta_annotation", value)}
          />
        )}
      </div>
    );
  }

  return (
    <div>
      <DesignSourcePicker
        label="Tasarım kaynağı"
        sourceType={payload.current_source_type}
        url={payload.current_url}
        assetId={payload.current_design_asset_id}
        urlLabel="Test edilecek URL"
        urlFieldId="wizard-current-url"
        urlError={fieldErrors.current_url}
        assetError={fieldErrors.current_design_asset_id}
        onSourceTypeChange={(sourceType) =>
          onChange("current_source_type", sourceType as WizardCurrentSourceType)
        }
        onUrlChange={(value) => onChange("current_url", value)}
        onAssetChange={(assetId) => onChange("current_design_asset_id", assetId ?? undefined)}
        screenshotDisabledReason={
          isAccessibilityPrecheck
            ? "Erişilebilirlik ön kontrolü DOM ve sayfa yapısı gerektirdiği için URL ile çalışır."
            : undefined
        }
      />
      {payload.current_source_type === "screenshot" && (
        <ScreenshotCtaSelector
          slot="current"
          label="Tasarım kaynağı"
          draftId={draftId ?? null}
          assetId={payload.current_design_asset_id}
          annotation={payload.current_cta_annotation}
          onAnnotationChange={(value) => onChange("current_cta_annotation", value)}
        />
      )}
    </div>
  );
}
