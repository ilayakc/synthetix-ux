import type { WizardDraftPayload } from "../../api/client";

export interface StepProps {
  payload: WizardDraftPayload;
  fieldErrors: Record<string, string>;
  onChange: <K extends keyof WizardDraftPayload>(field: K, value: WizardDraftPayload[K]) => void;
}

export const TEST_TYPE_LABELS: Record<string, string> = {
  existing_site_basic_ux: "Mevcut site: temel UX testi",
  ab_comparison: "A/B karşılaştırma: mevcut ve yeni tasarım",
  accessibility_precheck: "Erişilebilirlik ön kontrolü",
};
