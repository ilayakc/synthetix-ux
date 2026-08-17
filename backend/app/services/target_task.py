"""Hedef gorev (`target_task`) icin ANLAMSAL dogrulama - tek dogruluk kaynagi.

Sihirbazin "Hedef görev" alani, AI tiklama tahmini (etkilesim isi haritasi) ve
AI raporu pipeline'i icin kullanicinin NIYETINI tasir: model, bu gorev icin
sentetik bir kullanicinin hangi ogeyle etkilesecegini secer (bkz. app.services.
ai_interaction_heatmap.openai_selector). Bu yuzden alanin yalnizca "bos degil"
olmasi YETMEZ - `.`, `...`, `!?`, tek bir tekrarlanan karakter veya `test`/`asdf`
gibi anlamsiz bir placeholder, gecerli bir kullanici niyeti ICERMEZ ve pipeline'a
ulastiginda modul belirsiz bir cikti (bos/eslesmeyen) uretir.

Bu modul, hem PATCH (taslak kaydetme) hem launch (test baslatma) hem de worker
(savunma amacli) yollarinda AYNEN kullanilan tek kural setidir; ayni kurallar
frontend'de (frontend/src/pages/wizard/targetTaskValidation.ts) UX icin bire bir
yansitilir. Backend kesin ve otoriter olandir.

Dogrulama gereksiz derecede KATI degildir: `Ürünü bul` / `Giriş yap` gibi kisa
ama anlamli Turkce gorevler kabul edilir; Turkce harfler (ç, ğ, ı, İ, ö, ş, ü)
`str.isalpha()` uzerinden dogal olarak gecerli harf sayilir.
"""

from __future__ import annotations

import unicodedata
from typing import Final

# --- Hata sozlesmesi (bkz. app.routers.test_wizard - 422 yaniti) -------------
INVALID_TARGET_TASK_CODE: Final = "INVALID_TARGET_TASK"
TARGET_TASK_FIELD: Final = "target_task"

# --- Kullaniciya gosterilecek, alanin ALTINDA inline gosterilecek mesajlar ---
# (Turkce karakterli; frontend'de normalizeTurkishErrorCopy tarafindan
# bozulmadan aynen gosterilir.)
TARGET_TASK_EMPTY_MESSAGE: Final = "Hedef görev boş bırakılamaz."
TARGET_TASK_SYMBOLS_ONLY_MESSAGE: Final = (
    "Hedef görev yalnızca noktalama işaretlerinden veya sembollerden oluşamaz."
)
TARGET_TASK_MEANINGLESS_MESSAGE: Final = (
    "Hedef görevi kullanıcının ne yapacağını açıklayacak şekilde yazın."
)
TARGET_TASK_EXAMPLE_HINT: Final = "Örnek: Kırmızı spor ayakkabıyı bul ve sepete ekle."

# Anlamli sayilmasi icin en az bu kadar harf ve toplam uzunluk gerekir. Urun
# yapisina uygun, dusuk tutulmus makul bir esik: `Ürünü bul` (8 harf) gibi kisa
# gecerli gorevleri REDDETMEZ, ama `.` / `a` gibi girdileri gecirmez.
MIN_LETTERS: Final = 2
MIN_MEANINGFUL_LENGTH: Final = 3

# Anlamsiz placeholder / klavye-ezmesi degerleri (normalize: kucuk harf,
# bosluksuz). Gercek bir kullanici niyeti belirtmezler.
_PLACEHOLDER_VALUES: Final = frozenset(
    {
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
    }
)


class InvalidTargetTaskError(ValueError):
    """`target_task` anlamsal olarak gecersiz oldugunda firlatilir.

    `DraftValidationError`den (400) AYRI tutulur: router bunu, urun API
    standardina uygun, alan bazli bir 422 yanitina (`code`/`detail`/`field`)
    cevirir - boylece frontend hatayi ilgili alanin altinda gosterebilir,
    genel "bir hata olustu" mesajina indirgemez.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = INVALID_TARGET_TASK_CODE
        self.field = TARGET_TASK_FIELD


def _normalize(value: str) -> str:
    """Unicode NFC normalize + ardisik bosluklari tek boslukla birlestirip strip."""

    return " ".join(unicodedata.normalize("NFC", value).split())


def target_task_rejection_reason(value: object) -> str | None:
    """Gecersizse kullaniciya gosterilecek mesaji, gecerliyse `None` dondurur.

    Ayni siniflandirma frontend'de (targetTaskValidation.ts) bire bir yansitilir;
    bu iki uygulama asla ayrisamamalidir (tek dogruluk kaynagi bu fonksiyondur).
    """

    if not isinstance(value, str):
        return TARGET_TASK_EMPTY_MESSAGE

    text = _normalize(value)
    if not text:
        return TARGET_TASK_EMPTY_MESSAGE

    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        # Hic harf yok: yalnizca noktalama/sembol/emoji/rakam (`.`, `...`, `!?`,
        # `---`, `_`, `😀`, `123` ...).
        return TARGET_TASK_SYMBOLS_ONLY_MESSAGE

    if len(letters) < MIN_LETTERS or len(text) < MIN_MEANINGFUL_LENGTH:
        return TARGET_TASK_MEANINGLESS_MESSAGE

    compact = "".join(text.split()).casefold()

    # Tek bir tekrarlanan alfasayisal karakter (`aaa`, `IIII` ...): anlamsiz.
    distinct_alnum = {ch for ch in compact if ch.isalnum()}
    if len(distinct_alnum) <= 1:
        return TARGET_TASK_MEANINGLESS_MESSAGE

    if compact in _PLACEHOLDER_VALUES:
        return TARGET_TASK_MEANINGLESS_MESSAGE

    return None


def is_valid_target_task(value: object) -> bool:
    return target_task_rejection_reason(value) is None


def validate_target_task(value: object) -> None:
    """Gecersizse `InvalidTargetTaskError` firlatir (aksi halde sessizce doner)."""

    reason = target_task_rejection_reason(value)
    if reason is not None:
        raise InvalidTargetTaskError(reason)


__all__ = [
    "INVALID_TARGET_TASK_CODE",
    "TARGET_TASK_FIELD",
    "TARGET_TASK_EMPTY_MESSAGE",
    "TARGET_TASK_SYMBOLS_ONLY_MESSAGE",
    "TARGET_TASK_MEANINGLESS_MESSAGE",
    "TARGET_TASK_EXAMPLE_HINT",
    "MIN_LETTERS",
    "MIN_MEANINGFUL_LENGTH",
    "InvalidTargetTaskError",
    "target_task_rejection_reason",
    "is_valid_target_task",
    "validate_target_task",
]
