import DesignSourcePicker from "./DesignSourcePicker";
import AiDesignGenerator from "./AiDesignGenerator";
import type { WizardNewSourceType } from "../../api/client";

export interface DesignBSourcePickerProps {
  label: string;
  sourceType: WizardNewSourceType | undefined;
  url: string | undefined;
  assetId: string | undefined;
  onSourceTypeChange: (sourceType: WizardNewSourceType) => void;
  onUrlChange: (url: string) => void;
  onAssetChange: (assetId: string | null) => void;
  onAiGenerationAccept: (jobId: string, assetId: string) => void;
  urlLabel?: string;
  urlFieldId?: string;
  urlError?: string;
  assetError?: string;
  /** Tasarım A'nın (current_*) etkin kaynak türü ve - ekran görüntüsüyse -
   * asset kimliği. AI varyant üretimi bir referans görsel gerektirir (bkz.
   * proje talimatı "Referans görsel"); Tasarım A henüz bir URL'yse bu paket
   * kapsamında güvenilir bir referans screenshot henüz hazır değildir. */
  referenceSourceType: "url" | "screenshot";
  referenceAssetId: string | undefined;
}

const REFERENCE_URL_UNAVAILABLE_MESSAGE =
  "AI varyant oluşturmak için önce Tasarım A'nın ekran görüntüsünü yükleyin.";

export default function DesignBSourcePicker({
  label,
  sourceType,
  url,
  assetId,
  onSourceTypeChange,
  onUrlChange,
  onAssetChange,
  onAiGenerationAccept,
  urlLabel,
  urlFieldId,
  urlError,
  assetError,
  referenceSourceType,
  referenceAssetId,
}: DesignBSourcePickerProps) {
  const referenceUnavailableReason =
    referenceSourceType !== "screenshot" || !referenceAssetId
      ? REFERENCE_URL_UNAVAILABLE_MESSAGE
      : undefined;

  return (
    <DesignSourcePicker
      label={label}
      sourceType={sourceType}
      url={url}
      assetId={assetId}
      onSourceTypeChange={(value) => onSourceTypeChange(value as WizardNewSourceType)}
      onUrlChange={onUrlChange}
      onAssetChange={onAssetChange}
      urlLabel={urlLabel}
      urlFieldId={urlFieldId}
      urlError={urlError}
      assetError={assetError}
      extraOption={{
        value: "ai_generated",
        label: "AI ile oluştur",
        disabledReason: referenceUnavailableReason,
        renderPanel: () => (
          <AiDesignGenerator
            referenceAssetId={referenceAssetId}
            referenceUnavailableReason={referenceUnavailableReason}
            onAccept={onAiGenerationAccept}
            onUseUpload={() => onSourceTypeChange("screenshot")}
            onUseUrl={() => onSourceTypeChange("url")}
          />
        ),
      }}
    />
  );
}
