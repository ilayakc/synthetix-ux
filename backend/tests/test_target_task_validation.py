"""`app.services.target_task` anlamsal hedef gorev dogrulamasi icin birim testler.

Bu kural seti, frontend'deki targetTaskValidation.ts ile bire bir aynidir (tek
dogruluk kaynagi backend'dir); asagidaki gecerli/gecersiz ornekler gorev
talimatindaki listelerden turetilmistir.
"""

import pytest

from app.services import target_task
from app.services.target_task import (
    TARGET_TASK_EMPTY_MESSAGE,
    TARGET_TASK_MEANINGLESS_MESSAGE,
    TARGET_TASK_SYMBOLS_ONLY_MESSAGE,
    InvalidTargetTaskError,
)


@pytest.mark.parametrize(
    "value,expected_message",
    [
        (None, TARGET_TASK_EMPTY_MESSAGE),
        ("", TARGET_TASK_EMPTY_MESSAGE),
        ("   ", TARGET_TASK_EMPTY_MESSAGE),
        ("\t\n ", TARGET_TASK_EMPTY_MESSAGE),
        (".", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("...", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("!", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("???", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("!?", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("---", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("_", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("-", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("😀", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("🚀🔥", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("12345", TARGET_TASK_SYMBOLS_ONLY_MESSAGE),
        ("aaa", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("AAAA", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("a", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("test", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("TEST", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("asdf", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("qwerty", TARGET_TASK_MEANINGLESS_MESSAGE),
        ("deneme", TARGET_TASK_MEANINGLESS_MESSAGE),
    ],
)
def test_invalid_target_task_values(value, expected_message):
    assert target_task.target_task_rejection_reason(value) == expected_message
    assert not target_task.is_valid_target_task(value)
    with pytest.raises(InvalidTargetTaskError) as excinfo:
        target_task.validate_target_task(value)
    assert excinfo.value.message == expected_message
    assert excinfo.value.code == "INVALID_TARGET_TASK"
    assert excinfo.value.field == "target_task"


@pytest.mark.parametrize(
    "value",
    [
        "Ürünü bul",
        "Giriş yap",
        "Kırmızı spor ayakkabıyı sepete ekle",
        "İletişim formunu doldur",
        "Fiyatlandırma sayfasına git",
        "Kullanicinin sepete urun eklemesini gozlemle",
        "Çıkış yap",  # tum harfler Turkce; kisa ama anlamli
    ],
)
def test_valid_target_task_values(value):
    assert target_task.target_task_rejection_reason(value) is None
    assert target_task.is_valid_target_task(value)
    target_task.validate_target_task(value)  # firlatmamali


def test_turkish_letters_count_as_letters():
    # Yalnizca Turkce harflerden olusan gecerli kisa gorev reddedilmemeli.
    assert target_task.is_valid_target_task("Şifreyi güncelle")


def test_short_meaningful_task_not_rejected():
    # "Ürünü bul" gibi 2 kelimelik kisa ama anlamli gorev kabul edilir.
    assert target_task.is_valid_target_task("Ürünü bul")
