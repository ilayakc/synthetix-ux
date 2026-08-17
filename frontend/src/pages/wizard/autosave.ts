import { type WizardDraftPayload } from "../../api/client";
import { targetTaskRejectionReason } from "./targetTaskValidation";

export function isValidHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

// Autosave (debounce'lu PATCH) icin taslagi TEMIZLER: yerel olarak GECERSIZ
// alanlari (bos/gecersiz hedef gorev, gecersiz/eksik URL) GONDERMEZ. Boylece
// kullanici alani yazarken backend her tuş vuruşunda beklenen bir 422
// (INVALID_TARGET_TASK) / 400 ("... URL olmalidir") dondurup konsolu ve banner'i
// kirletmez - bu alanlar yalnizca YEREL olarak (inline alan hatasi) dogrulanir ve
// yalnizca GECERLI olduklarinda kalici olarak kaydedilir. Diger tum alanlar
// (isim, persona, moduller, kaynak turu vb.) normal sekilde kaydedilmeye devam eder.
export function sanitizeDraftForAutosave(payload: WizardDraftPayload): WizardDraftPayload {
  const next: WizardDraftPayload = { ...payload };
  if (targetTaskRejectionReason(next.target_task)) {
    delete next.target_task;
  }
  if (
    next.current_url !== undefined &&
    (!next.current_url.trim() || !isValidHttpUrl(next.current_url))
  ) {
    delete next.current_url;
  }
  if (next.new_url !== undefined && (!next.new_url.trim() || !isValidHttpUrl(next.new_url))) {
    delete next.new_url;
  }
  return next;
}
