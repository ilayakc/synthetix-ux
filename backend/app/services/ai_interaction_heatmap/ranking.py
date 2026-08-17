"""Deterministik, gorev-odakli etkilesim siralayicisi.

Bu modul, model gorsel girdi DESTEKLEMEDIGINDE (varsayilan durum, bkz.
openai_provider - yalnizca metin) kullanilan "DOM ve gorsel ozellik destekli
AI siralamasi" yontemidir. Tamamen deterministiktir (ayni girdi -> ayni cikti),
DB/ag kullanmaz ve gorev talimatinin cekirdek kurallarini kodlar:

- SONUC YALNIZCA GORSEL BUYUKLUGE gore olmaz: skorun agirlikli cogunlugu
  (`_TASK_WEIGHT`) hedef gorev-aday eslesmesinden gelir; boyut/one-cikma
  katkisi (`_PROMINENCE_WEIGHT`) yapisal olarak bir "none" adayi bir "direct"
  adayin ustune CIKARAMAZ (bkz. test 3).
- HEDEF GOREVE DUYARLILIK: ayni sayfa, farkli hedef gorevlerde farkli hotspot
  siralamasi verir (bkz. test 1 vs test 2) - cunku eslesme hedef gorevin niyet
  gruplariyla hesaplanir.
- KULLANICI CTA'SI GUCLU KANITTIR: onaylanmis aday bonus alir ama digger
  adaylar degerlendirilmeye devam eder.
- GUCLU ESLESME YOKSA kirmizi alan UYDURULMAZ: gorevle ilgili hicbir aday
  yoksa hotspot listesi bos birakilir ve `unmatched_task_warning` doldurulur.
"""

from __future__ import annotations

import math
import re

from app.services.ai_interaction_heatmap.schemas import (
    MAX_HOTSPOTS,
    NO_STRONG_MATCH_WARNING,
    AIHotspotSelection,
    AIInteractionOutput,
    Confidence,
    InteractionCandidate,
    TaskRelevance,
)

# --- Skor agirliklari (toplam gorev-disi <= 0.25 << gorev 0.70) --------------
_TASK_WEIGHT = 0.70
_KIND_WEIGHT = 0.15
_PROMINENCE_WEIGHT = 0.10
_CONFIRMED_BONUS = 0.25

# task_match esikleri -> gorev-ilgisi etiketi.
_DIRECT_THRESHOLD = 0.75
_RELATED_THRESHOLD = 0.35

# Etkilesim turu -> [0,1] onem agirligi (eylem turleri daha yuksek).
_KIND_WEIGHTS: dict[str, float] = {
    "user_confirmed_cta": 1.0,
    "form_action": 1.0,
    "button": 0.85,
    "cta_link": 0.85,
    # Gorsel (ekran goruntusu) adaylari icin sekil-tabanli turler (bkz.
    # candidates._visual_kind) - eylem gorunumlu buton adaylari daha yuksek.
    "visual_button": 0.85,
    "visual_card": 0.5,
    "visual_region": 0.4,
    "content_link": 0.5,
    "image_link": 0.45,
    "navigation_action": 0.4,
    "navigation_link": 0.3,
    "pagination_control": 0.1,
    "container_link": 0.0,
}

# Turkce/ingilizce, aksan-normalize edilmis niyet gruplari. Bir aday VE hedef
# gorev ayni gruba dusuyorsa "direct" eslesme kabul edilir. Yeni bir dil/niyet
# eklemek icin buraya satir eklenir (tek kaynak).
_INTENT_PHRASES: dict[str, tuple[str, ...]] = {
    "account_creation": (
        "uye ol",
        "uye olun",
        "kayit ol",
        "kayit olun",
        "kaydol",
        "kaydolun",
        "hesap olustur",
        "hesap olusturun",
        "hesap ac",
        "hesap acin",
        "ucretsiz hesap",
        "yeni hesap",
        "hesabinizi olusturun",
        "register",
        "sign up",
        "signup",
        "create account",
        "join",
    ),
    "login": ("giris yap", "oturum ac", "login", "sign in", "signin"),
    "bestsellers": (
        "cok satan",
        "cok satanlar",
        "en cok satan",
        "best seller",
        "bestseller",
        "populer urun",
        "populer urunler",
        "one cikan",
    ),
    "browse_products": (
        "urunleri incele",
        "urunleri gor",
        "urunleri kesfet",
        "tum urunler",
        "tumunu gor",
        "urun katalogu",
        "koleksiyon",
        "kesfet",
        "alisverise basla",
        "alisveris yap",
    ),
    "add_to_cart": ("sepete ekle", "satin al", "hemen al", "siparis ver", "siparis olustur"),
    # Sepeti GORUNTULEME / sepete gitme niyeti (ekleme'den ayri). "sepet" ve
    # "cart"/"basket" alt-dizileri; "sepete eklenen urunu goruntule" gorevi VE
    # ikon-only sepet kontrolunun ("Sepet"/"Sepetim" etiketi, bkz. candidates.
    # _SEMANTIC_LABELS) ORTAK niyete dusmesini saglar - boylece gecerli bir sepet
    # gorevi artik "no strong match" uretmez.
    "view_cart": (
        "sepet",
        "sepetim",
        "sepeti goruntule",
        "sepete git",
        "sepeti gor",
        "sepeti ac",
        "sepete bak",
        "alisveris sepeti",
        "alisveris cantasi",
        "cart",
        "basket",
        "view cart",
        "go to cart",
        "my cart",
        "view basket",
    ),
    "contact": ("iletisim", "bize ulasin", "destek al", "contact"),
    "subscribe": ("abone ol", "bulten", "newsletter", "subscribe"),
}

# Tek-kelimelik ("token") niyet ipuclari - `_INTENT_PHRASES`i tamamlar.
_INTENT_TOKENS: dict[str, tuple[str, ...]] = {
    "account_creation": ("kayit", "kaydol", "register", "signup"),
    "bestsellers": ("bestseller",),
    "browse_products": ("urunler", "katalog", "koleksiyon", "kesfet"),
}

# Eslesmede bilgi tasimayan yaygin kelimeler (token-ortusme paydasindan cikarilir).
_STOPWORDS = {
    "ve", "ile", "bir", "bu", "da", "de", "icin", "the", "a", "an", "to",
    "your", "you", "of", "en", "veya", "or",
}

_TR_MAP = str.maketrans(
    {
        "ç": "c", "Ç": "c", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
        "ı": "i", "İ": "i", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "I": "i",
    }
)


def _normalize(text: str) -> str:
    return text.translate(_TR_MAP).lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalize(text))


def _content_tokens(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in _STOPWORDS and len(t) >= 2}


def _detect_intents(text: str) -> set[str]:
    normalized = " ".join(_tokens(text))
    token_set = set(normalized.split())
    intents: set[str] = set()
    for intent, phrases in _INTENT_PHRASES.items():
        if any(phrase in normalized for phrase in phrases):
            intents.add(intent)
    for intent, single in _INTENT_TOKENS.items():
        if token_set & set(single):
            intents.add(intent)
    return intents


def _task_match(task_text: str, task_intents: set[str], task_tokens: set[str], label: str) -> float:
    """[0,1] gorev-aday eslesme skoru. Ortak niyet -> 1.0 (direct); aksi halde
    kelime ortusme oranina duser."""

    label_intents = _detect_intents(label)
    if task_intents & label_intents:
        return 1.0
    if not task_tokens:
        return 0.0
    label_tokens = _content_tokens(label)
    if not label_tokens:
        return 0.0
    overlap = len(task_tokens & label_tokens) / len(task_tokens)
    return max(0.0, min(1.0, overlap))


def _relevance(task_match: float) -> TaskRelevance:
    if task_match >= _DIRECT_THRESHOLD:
        return "direct"
    if task_match >= _RELATED_THRESHOLD:
        return "related"
    if task_match > 0.0:
        return "weak"
    return "none"


def _confidence(task_relevance: TaskRelevance, score: float) -> Confidence:
    if task_relevance == "direct" and score >= 0.7:
        return "high"
    if task_relevance in ("direct", "related"):
        return "medium"
    return "low"


def _prominence(candidate: InteractionCandidate) -> float:
    area = max(0.0, candidate.w) * max(0.0, candidate.h)
    size = min(1.0, math.sqrt(area) / 0.35) if area > 0 else 0.0
    fold = 0.15 if candidate.above_fold else 0.0
    return min(1.0, 0.85 * size + fold)


def _reason(task_relevance: TaskRelevance, label: str, task_text: str) -> str:
    if task_relevance == "direct":
        return (
            f"Hedef gorev ('{task_text}') ile '{label}' dogrudan ayni eylemi ifade ediyor."
        )
    if task_relevance == "related":
        return f"'{label}', hedef gorevle kismen iliskili bir etkilesim alani."
    return f"'{label}' hedef gorevle yalnizca zayif iliskili; dusuk guvenle listelendi."


def rank_interaction_hotspots(
    candidates: list[InteractionCandidate],
    *,
    target_task: str,
    confirmed_candidate_id: str | None = None,
) -> AIInteractionOutput:
    """Adaylari gorev-odakli skorlar ve en fazla `MAX_HOTSPOTS` hotspot dondurur.

    Gorevle ilgili (task_relevance != "none") ya da kullanici tarafindan
    onaylanmis aday YOKSA hotspot listesi BOS birakilir ve
    `unmatched_task_warning` doldurulur - kirmizi alan uydurulmaz.
    """

    task_intents = _detect_intents(target_task)
    task_tokens = _content_tokens(target_task)

    scored: list[AIHotspotSelection] = []
    for candidate in candidates:
        is_confirmed = candidate.candidate_id == confirmed_candidate_id
        task_match = _task_match(target_task, task_intents, task_tokens, candidate.label)
        task_relevance = _relevance(task_match)
        kind = _KIND_WEIGHTS.get(candidate.interaction_kind, 0.5)
        score = (
            _TASK_WEIGHT * task_match
            + _KIND_WEIGHT * kind
            + _PROMINENCE_WEIGHT * _prominence(candidate)
            + (_CONFIRMED_BONUS if is_confirmed else 0.0)
        )
        score = max(0.0, min(1.0, score))
        if task_relevance == "none" and not is_confirmed:
            continue
        scored.append(
            AIHotspotSelection(
                candidate_id=candidate.candidate_id,
                score=round(score, 4),
                confidence=_confidence(task_relevance, score),
                reason=_reason(task_relevance, candidate.label, target_task),
                task_relevance=task_relevance,
            )
        )

    # Skora gore azalan; esitlikte kararli sira (candidate_id) ile deterministik.
    scored.sort(key=lambda h: (-h.score, h.candidate_id))
    hotspots = scored[:MAX_HOTSPOTS]

    if not hotspots:
        return AIInteractionOutput(
            summary=(
                f"Hedef gorev ('{target_task}') icin gorevle guclu eslesen bir "
                "etkilesim alani bulunamadi."
            ),
            hotspots=[],
            unmatched_task_warning=NO_STRONG_MATCH_WARNING,
        )

    top = hotspots[0]
    top_label = next(c.label for c in candidates if c.candidate_id == top.candidate_id)
    warning = None if any(h.task_relevance in ("direct", "related") for h in hotspots) else NO_STRONG_MATCH_WARNING
    summary = (
        f"Hedef gorev ('{target_task}') icin en olası etkilesim alani '{top_label}'. "
        f"{len(hotspots)} aday gorev baglaminda onceliklendirildi."
    )
    return AIInteractionOutput(summary=summary, hotspots=hotspots, unmatched_task_warning=warning)


__all__ = ["rank_interaction_hotspots"]
