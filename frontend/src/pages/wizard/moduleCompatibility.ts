import type { AnalysisModuleDefinition, ModuleSourceType, WizardDraftPayload } from "../../api/client";

export const NETWORK_DEVICE_TEST_DISABLED_REASON =
  "Bu modül gerçek sayfa yükleme ve ağ ölçümü yaptığı için yalnızca canlı URL kaynaklarıyla kullanılabilir.";

/** Taslagin (test_type'a gore tek veya A/B) SU AN etkin tum tasarim kaynagi
 * turlerini dondurur - backend'deki `effective_source_type`/
 * `effective_new_source_type` ile ayni geriye donuk uyumluluk kurali
 * gecerlidir (alan yoksa "url" varsayilir). */
export function activeSourceTypes(payload: WizardDraftPayload): ModuleSourceType[] {
  const types: ModuleSourceType[] = [(payload.current_source_type as ModuleSourceType) || "url"];
  if (payload.test_type === "ab_comparison") {
    types.push((payload.new_source_type as ModuleSourceType) || "url");
  }
  return types;
}

/** Bir modulun, taslagin TUM (tek veya A/B'nin iki tarafindaki) etkin
 * kaynak turleriyle uyumlu olup olmadigini modul katalogunun
 * `supported_source_types` capability alanina bakarak belirler - tek gercek
 * kaynak budur, baska bir yerde ayrica hardcode edilmez. */
export function isModuleCompatibleWithSources(
  module: AnalysisModuleDefinition,
  payload: WizardDraftPayload,
): boolean {
  return activeSourceTypes(payload).every((sourceType) =>
    module.supported_source_types.includes(sourceType),
  );
}

/** `payload.modules` icinde, GUNCEL kaynak turleriyle artik uyumsuz olan
 * modul anahtarlarini dondurur (kaynak sonradan screenshot'a cevrilmis
 * olabilir - bkz. TestWizard'daki otomatik temizleme). */
export function incompatibleSelectedModuleKeys(
  payload: WizardDraftPayload,
  moduleCatalog: AnalysisModuleDefinition[],
): string[] {
  const selected = payload.modules ?? [];
  const byKey = new Map(moduleCatalog.map((module) => [module.key, module]));
  return selected.filter((key) => {
    const module = byKey.get(key);
    if (!module) return false;
    return !isModuleCompatibleWithSources(module, payload);
  });
}
