import type { WizardDraftPayload } from "../../api/client";

export interface StepProps {
  payload: WizardDraftPayload;
  fieldErrors: Record<string, string>;
  onChange: <K extends keyof WizardDraftPayload>(field: K, value: WizardDraftPayload[K]) => void;
  /** Yalnızca Step2Urls (CTA seçimi, bkz. ScreenshotCtaSelector) tarafından
   * kullanılır - annotation PATCH'lerinin sunucu tarafında hemen çözümlenmiş
   * (`verified_content_sha256` dahil) yanıtını alabilmek için gereklidir.
   * Diğer adımlar bu alanı hiç kullanmadığı için opsiyoneldir. */
  draftId?: string | null;
}

export const TEST_TYPE_LABELS: Record<string, string> = {
  existing_site_basic_ux: "Mevcut site: temel UX testi",
  ab_comparison: "A/B testi: aynı sitenin orijinal ve değiştirilmiş tasarımı",
  accessibility_precheck: "Erişilebilirlik ön kontrolü",
};

export const TEST_TYPE_DESCRIPTIONS: Record<string, string> = {
  existing_site_basic_ux:
    "Tek bir tasarımda kullanıcıların hedef görevi ne kadar kolay tamamladığını ölçer. URL veya ekran görüntüsüyle çalışır.",
  ab_comparison:
    "Aynı sitenin orijinal tasarımı ile değiştirilmiş tasarımını karşılaştırır. İki farklı siteyi karşılaştırmak için kullanılmaz.",
  accessibility_precheck:
    "Kontrast, form alanları, başlık yapısı ve temel WCAG sorunlarını inceler. Sayfanın HTML yapısı gerektiği için URL ile çalışır.",
};
