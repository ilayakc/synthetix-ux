import type { WizardDraftPayload } from "../../api/client";

/** Backend'deki `app.services.pricing.AI_REPORT_MODULE_KEY` ile birebir ayni
 * deger. Modul anahtari tek bir yerde tutulur, baska yerde hardcode edilmez. */
export const AI_REPORT_MODULE_KEY = "ai_report";

/** `ai_report` secili AMA hicbir persona secimi yokken kullaniciya gosterilen
 * (ve onu 3. persona adimina yonlendiren) tek kaynak mesaj. Ayni kural
 * backend'de de BAGIMSIZ olarak zorlanir (bkz. app.services.test_wizard.
 * validate_ai_report_persona_requirement / AI_REPORT_PERSONA_REQUIRED_MESSAGE);
 * bu frontend metni yalnizca erken, aciklayici bir UX katmanidir - guvenlik
 * siniri degildir. */
export const AI_REPORT_PERSONA_REQUIRED_MESSAGE =
  "AI raporu modülü temsili personalar üzerinde çalışır. Devam etmeden önce 3. adımda bir " +
  "persona preset'i seçin ya da özel bir persona dağılımı tanımlayın.";

/** Sihirbaz persona adiminda gecerli bir persona secimi (preset VEYA BOS
 * OLMAYAN ozel dagilim) yapilmis mi? Backend'deki `_payload_has_persona_
 * selection` ile AYNI truthiness kuralini uygular: bos bir
 * `persona_distribution = {}` (custom mod secilip hicbir boyut girilmemis) da
 * orada oldugu gibi burada da "secim yok" sayilir. */
export function hasPersonaSelection(payload: WizardDraftPayload): boolean {
  if (payload.persona_preset_id) return true;
  const distribution = payload.persona_distribution;
  return distribution !== undefined && Object.keys(distribution).length > 0;
}

/** `ai_report` secili AMA hicbir persona secimi yoksa `true` doner. Bu durumda
 * launch backend tarafindan reddedilir; frontend bunu ONCEDEN yakalayip
 * kullaniciyi persona adimina yonlendirir (bkz. TestWizard, Step4Modules). */
export function aiReportRequiresPersonaSelection(payload: WizardDraftPayload): boolean {
  const modules = payload.modules ?? [];
  return modules.includes(AI_REPORT_MODULE_KEY) && !hasPersonaSelection(payload);
}
