import { describe, expect, it } from "vitest";
import {
  TARGET_TASK_EMPTY_MESSAGE,
  TARGET_TASK_MEANINGLESS_MESSAGE,
  TARGET_TASK_SYMBOLS_ONLY_MESSAGE,
  isValidTargetTask,
  targetTaskRejectionReason,
} from "./targetTaskValidation";

// Bu kural seti backend'deki app/services/target_task.py ile bire bir aynidir;
// asagidaki ornekler test_target_task_validation.py ile eslesir.

describe("targetTaskRejectionReason", () => {
  it.each([
    [undefined, TARGET_TASK_EMPTY_MESSAGE],
    [null, TARGET_TASK_EMPTY_MESSAGE],
    ["", TARGET_TASK_EMPTY_MESSAGE],
    ["   ", TARGET_TASK_EMPTY_MESSAGE],
    [".", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["...", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["!?", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["---", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["_", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["😀", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["12345", TARGET_TASK_SYMBOLS_ONLY_MESSAGE],
    ["aaa", TARGET_TASK_MEANINGLESS_MESSAGE],
    ["test", TARGET_TASK_MEANINGLESS_MESSAGE],
    ["asdf", TARGET_TASK_MEANINGLESS_MESSAGE],
    ["deneme", TARGET_TASK_MEANINGLESS_MESSAGE],
  ])("reddeder: %j", (value, expected) => {
    expect(targetTaskRejectionReason(value as string | null | undefined)).toBe(expected);
    expect(isValidTargetTask(value as string | null | undefined)).toBe(false);
  });

  it.each([
    "Ürünü bul",
    "Giriş yap",
    "Kırmızı spor ayakkabıyı sepete ekle",
    "İletişim formunu doldur",
    "Fiyatlandırma sayfasına git",
    "Şifreyi güncelle",
  ])("kabul eder: %s", (value) => {
    expect(targetTaskRejectionReason(value)).toBeNull();
    expect(isValidTargetTask(value)).toBe(true);
  });
});
