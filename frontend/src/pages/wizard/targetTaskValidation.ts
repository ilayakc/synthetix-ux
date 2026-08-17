// Hedef gorev (`target_task`) icin anlamsal dogrulama - backend'deki
// app/services/target_task.py kural setinin UX icin bire bir yansimasi.
//
// Backend KESIN ve otoriterdir (gecersiz bir deger sunucuya ulasirsa 422
// INVALID_TARGET_TASK doner); buradaki dogrulama yalnizca kullaniciya aninda,
// alan altinda inline geri bildirim saglar. Iki uygulama asla ayrisamamalidir -
// mesajlar ve esikler aynen kopyalanmistir.

export const TARGET_TASK_EMPTY_MESSAGE = "Hedef görev boş bırakılamaz.";
export const TARGET_TASK_SYMBOLS_ONLY_MESSAGE =
  "Hedef görev yalnızca noktalama işaretlerinden veya sembollerden oluşamaz.";
export const TARGET_TASK_MEANINGLESS_MESSAGE =
  "Hedef görevi kullanıcının ne yapacağını açıklayacak şekilde yazın.";
export const TARGET_TASK_EXAMPLE_HINT = "Örnek: Kırmızı spor ayakkabıyı bul ve sepete ekle.";

const MIN_LETTERS = 2;
const MIN_MEANINGFUL_LENGTH = 3;

// Anlamsiz placeholder / klavye-ezmesi degerleri (normalize: kucuk harf,
// bosluksuz). Backend `_PLACEHOLDER_VALUES` ile ayni liste.
const PLACEHOLDER_VALUES = new Set([
  "test",
  "testtest",
  "deneme",
  "denemetest",
  "asdf",
  "asdfasdf",
  "asd",
  "sdf",
  "qwe",
  "qwer",
  "qwerty",
  "abc",
  "abcabc",
  "xyz",
  "xxx",
  "lorem",
  "loremipsum",
  "asdasd",
  "sdfsdf",
  "dfdf",
]);

function normalize(value: string): string {
  return value.normalize("NFC").split(/\s+/).filter(Boolean).join(" ");
}

/**
 * Gecersizse kullaniciya gosterilecek mesaji, gecerliyse `null` dondurur.
 * Backend `target_task_rejection_reason` ile ayni siniflandirma.
 */
export function targetTaskRejectionReason(value: string | null | undefined): string | null {
  if (typeof value !== "string") return TARGET_TASK_EMPTY_MESSAGE;

  const text = normalize(value);
  if (!text) return TARGET_TASK_EMPTY_MESSAGE;

  const chars = Array.from(text);
  const letters = chars.filter((ch) => /\p{L}/u.test(ch));
  if (letters.length === 0) {
    // Hic harf yok: yalnizca noktalama/sembol/emoji/rakam.
    return TARGET_TASK_SYMBOLS_ONLY_MESSAGE;
  }
  if (letters.length < MIN_LETTERS || text.length < MIN_MEANINGFUL_LENGTH) {
    return TARGET_TASK_MEANINGLESS_MESSAGE;
  }

  const compact = text.replace(/\s+/g, "").toLowerCase();

  // Tek bir tekrarlanan alfasayisal karakter (`aaa`, `IIII` ...): anlamsiz.
  const distinctAlnum = new Set(Array.from(compact).filter((ch) => /[\p{L}\p{N}]/u.test(ch)));
  if (distinctAlnum.size <= 1) return TARGET_TASK_MEANINGLESS_MESSAGE;

  if (PLACEHOLDER_VALUES.has(compact)) return TARGET_TASK_MEANINGLESS_MESSAGE;

  return null;
}

export function isValidTargetTask(value: string | null | undefined): boolean {
  return targetTaskRejectionReason(value) === null;
}
