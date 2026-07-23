import DesignSourcePicker from "./DesignSourcePicker";
import DesignBSourcePicker from "./DesignBSourcePicker";
import ScreenshotCtaSelector from "./ScreenshotCtaSelector";
import type { WizardCurrentSourceType, WizardNewSourceType } from "../../api/client";
import type { StepProps } from "./types";

export default function Step2Urls({ payload, fieldErrors, onChange, draftId }: StepProps) {
  const isAbComparison = payload.test_type === "ab_comparison";
  const isAccessibilityPrecheck = payload.test_type === "accessibility_precheck";

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

        <DesignBSourcePicker
          label="Tasarım B — Alternatif tasarım"
          sourceType={payload.new_source_type}
          url={payload.new_url}
          assetId={payload.new_design_asset_id}
          urlLabel="Yeni tasarım URL'si"
          urlFieldId="wizard-new-url"
          urlError={fieldErrors.new_url}
          assetError={fieldErrors.new_design_asset_id}
          onSourceTypeChange={(sourceType: WizardNewSourceType) => onChange("new_source_type", sourceType)}
          onUrlChange={(value) => onChange("new_url", value)}
          onAssetChange={(assetId) => onChange("new_design_asset_id", assetId ?? undefined)}
          onAiGenerationAccept={(jobId, assetId) => {
            onChange("new_source_type", "ai_generated");
            onChange("new_design_asset_id", assetId);
            onChange("new_ai_generation_id", jobId);
          }}
          referenceSourceType={payload.current_source_type === "screenshot" ? "screenshot" : "url"}
          referenceAssetId={
            payload.current_source_type === "screenshot" ? payload.current_design_asset_id : undefined
          }
        />
        {(payload.new_source_type === "screenshot" || payload.new_source_type === "ai_generated") && (
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
