"""Analyzer verisinden GERCEK etkilesim adaylarini cikarir.

`build_interaction_candidates`, `app.routers.reports._build_cta_overlay` ile
AYNI kaynak veriyi (DOM `element_boxes` / gorsel `visual_cta_candidates`) ve
AYNI koordinat-guvenligi ilkesini kullanir - fark, buradaki her adaya kararli
bir `candidate_id` atanmasi ve AI'in YALNIZCA bu id'ler arasindan
secebilmesidir. Bu fonksiyon SAFTIR: DB'ye erismez, ag cagrisi yapmaz; girdi
olarak yalnizca (zaten yuklenmis) `features` sozlugu, goruntu boyutlari,
kaynak turu ve kullanicinin onayladigi CTA'yi alir.

Rastgele/tahmini koordinat URETILMEZ: gecerli bir kutusu olmayan aday atlanir.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.models.page_analysis import PageAnalysisSourceKind
from app.services.ai_interaction_heatmap.schemas import InteractionCandidate

# Ilk ekran ("above the fold") esigi: viewport yuksekligi rapor katmaninda
# kesin bilinmedigi icin, standart bir masaustu en-boy oranindan (16:9)
# turetilen deterministik bir yaklasim kullanilir - image_width * 9/16 px'lik
# ust bant "ilk ekran" sayilir. Gorsel/fraksiyon kaynaklarda dogrudan y
# fraksiyonu bu orana gore degerlendirilir.
_FOLD_ASPECT = 9.0 / 16.0

# `container_link` (dev sarmalayici baglantilar) haritada anlamli bir tiki
# temsil etmez - `_build_cta_overlay` ile ayni sekilde disarida birakilir.
_EXCLUDED_KINDS = {"container_link"}


def _fold_ratio(image_width: int | None, image_height: int | None) -> float:
    if not image_width or not image_height or image_height <= 0:
        return 1.0
    fold_px = float(image_width) * _FOLD_ASPECT
    return max(0.0, min(1.0, fold_px / float(image_height)))


def _infer_dom_interaction_kind(element: Mapping, w: float, h: float) -> str:
    interaction_kind = element.get("interaction_kind")
    if isinstance(interaction_kind, str) and interaction_kind:
        return interaction_kind
    aspect = w / h if h > 0 else float("inf")
    if w > 0.55 or aspect > 16:
        return "container_link"
    if w <= 0.028 and h <= 0.07:
        return "pagination_control"
    if element.get("role") == "button":
        return "button"
    if float(element.get("y_frac", 1.0)) < 0.14:
        return "navigation_link"
    return "content_link"


def _safe_label(raw: object, fallback: str) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:120]
    return fallback


# --- Gorsel (ekran goruntusu) adaylarina AYIRT EDILEBILIR, guvenli kimlik ----
#
# Ekran goruntusu kaynaklarinda OCR metni YOKTUR; bu yuzden gercek buton metni
# UYDURULMAZ (gorev talimati "gercek metin uydurma"). Bunun yerine adaya, gorsel
# analizden ZATEN cikarilmis GUVENLI ozelliklerden (baskin renk, kutu sekli/
# boyutu, yaklasik konum ve sira numarasi) ayirt edilebilir bir kimlik uretilir.
# Boylece her aday "Gorsel CTA adayi" gibi ayni/anlamsiz etiketle degil; ornegin
# "Mor buton adayi - alt orta (aday 3)" gibi tekil bir etiketle gonderilir ve
# OpenAI numarali overlay + bu etiket ile dogru `candidate_id`'yi eslestirebilir.

_VERTICAL_LABELS = ("ust", "orta", "alt")
_HORIZONTAL_LABELS = ("sol", "orta", "sag")


def _color_name(color: Mapping | None) -> str | None:
    """Baskin RGB rengini kaba, insan-okur bir Turkce renk adina cevirir. Renk
    bilgisi yoksa/gecersizse `None` (etikette renk atlanir - uydurulmaz)."""

    if not isinstance(color, Mapping):
        return None
    try:
        r = float(color["r"])
        g = float(color["g"])
        b = float(color["b"])
    except (KeyError, TypeError, ValueError):
        return None
    mx, mn = max(r, g, b), min(r, g, b)
    delta = mx - mn
    if delta < 28:  # doygunluk dusuk -> gri tonu
        if mx >= 210:
            return "beyaz"
        if mx <= 60:
            return "siyah"
        return "gri"
    if mx == r:
        hue = 60.0 * (((g - b) / delta) % 6.0)
    elif mx == g:
        hue = 60.0 * (((b - r) / delta) + 2.0)
    else:
        hue = 60.0 * (((r - g) / delta) + 4.0)
    if hue < 0:
        hue += 360.0
    if hue < 15 or hue >= 345:
        return "kirmizi"
    if hue < 45:
        return "turuncu"
    if hue < 70:
        return "sari"
    if hue < 165:
        return "yesil"
    if hue < 200:
        return "camgobegi"
    if hue < 255:
        return "mavi"
    if hue < 290:
        return "mor"
    return "pembe"


def _visual_kind(w: float, h: float) -> tuple[str, str]:
    """Kutu sekil/boyutundan (interaction_kind, insan-okur tur) uretir.

    interaction_kind, ranking._KIND_WEIGHTS ile eslesen KONTROLLU bir degerdir;
    ikinci deger etikette gosterilen serbest metindir."""

    area = max(0.0, w) * max(0.0, h)
    aspect = (w / h) if h > 0 else float("inf")
    if area >= 0.055 and 0.5 <= aspect <= 2.4:
        return "visual_card", "kart"
    if aspect >= 2.4:
        return "visual_button", "buton"
    if aspect <= 0.5:
        return "visual_region", "menu veya kenar alani"
    return "visual_button", "buton"


def _position_phrase(x: float, y: float, w: float, h: float) -> str:
    cx = x + w / 2.0
    cy = y + h / 2.0
    vertical = _VERTICAL_LABELS[0] if cy < 0.34 else (_VERTICAL_LABELS[1] if cy < 0.67 else _VERTICAL_LABELS[2])
    horizontal = (
        _HORIZONTAL_LABELS[0] if cx < 0.34 else (_HORIZONTAL_LABELS[1] if cx < 0.67 else _HORIZONTAL_LABELS[2])
    )
    if horizontal == "orta":
        return f"{vertical} orta"
    return f"{horizontal} {vertical}"


def marker_number(candidate_id: str) -> int:
    """`candidate-N` icindeki sira numarasini (N) dondurur - numarali overlay
    isareti ve etiketteki "(aday N)" bununla eslesir. Parse edilemezse 0."""

    try:
        return int(candidate_id.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        return 0


def _marker_number(candidate_id: str) -> int:
    return marker_number(candidate_id)


def _describe_visual_candidate(cand: Mapping, marker: int, w: float, h: float) -> tuple[str, str]:
    """Bir gorsel CTA adayina ayirt edilebilir, guvenli etiket + interaction_kind
    uretir. Ornek: ("Mor buton adayi - alt orta (aday 3)", "visual_button").

    OCR metni bulunmadigi icin gercek buton metni IDDIA EDILMEZ; etiket yalnizca
    renk + tur + konum + sira numarasindan olusur. `marker`, `candidate_id`'nin
    sira numarasidir (numarali overlay isaretiyle eslesir)."""

    interaction_kind, human_type = _visual_kind(w, h)
    color = _color_name(cand.get("dominant_color"))
    box = cand.get("box") or {}
    try:
        x = float(box["x"])
        y = float(box["y"])
    except (KeyError, TypeError, ValueError):
        x = y = 0.0
    position = _position_phrase(x, y, w, h)
    prefix = f"{color.capitalize()} {human_type}" if color else human_type.capitalize()
    return f"{prefix} adayi - {position} (aday {marker})", interaction_kind


def build_interaction_candidates(
    *,
    features: Mapping | None,
    image_width: int | None,
    image_height: int | None,
    source_kind: PageAnalysisSourceKind,
    user_confirmed_cta: Mapping | None = None,
) -> tuple[list[InteractionCandidate], str | None]:
    """Gercek etkilesim adaylarini + (varsa) kullanicinin onayladigi CTA'nin
    `candidate_id`'sini dondurur.

    Donen `confirmed_candidate_id`, deterministik siralayiciya "guclu kanit"
    olarak gecirilir (bkz. ranking); fakat digger adaylar da degerlendirilmeye
    devam eder (gorev talimati "HEDEF GOREV ONCELIGI").
    """

    features = features or {}
    fold_ratio = _fold_ratio(image_width, image_height)
    candidates: list[InteractionCandidate] = []
    confirmed_candidate_id: str | None = None
    counter = 0

    def _next_id() -> str:
        nonlocal counter
        counter += 1
        return f"candidate-{counter}"

    # 1) Kullanicinin elle sectigi/onayladigi CTA (varsa) - her zaman ilk aday.
    confirmed_source_index: int | None = None
    if isinstance(user_confirmed_cta, Mapping):
        box = user_confirmed_cta.get("box") or {}
        try:
            x = float(box["x"])
            y = float(box["y"])
            w = float(box["w"])
            h = float(box["h"])
        except (KeyError, TypeError, ValueError):
            x = y = w = h = 0.0
        if w > 0 and h > 0:
            confirmed_candidate_id = _next_id()
            candidates.append(
                InteractionCandidate(
                    candidate_id=confirmed_candidate_id,
                    label=_safe_label(user_confirmed_cta.get("label"), "Secilen CTA"),
                    interaction_kind="user_confirmed_cta",
                    role="user_confirmed",
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    above_fold=(y + h / 2) <= fold_ratio,
                )
            )
            if user_confirmed_cta.get("selection_source") == "candidate_confirmation":
                idx = user_confirmed_cta.get("source_candidate_index")
                if isinstance(idx, int):
                    confirmed_source_index = idx

    # 2) Gorsel (DesignAsset) kaynak: fraksiyon kutular zaten [0,1].
    if source_kind == PageAnalysisSourceKind.DESIGN_ASSET:
        raw = features.get("visual_cta_candidates") or []
        if isinstance(raw, list):
            for index, cand in enumerate(raw):
                if confirmed_source_index is not None and index == confirmed_source_index:
                    continue
                if not isinstance(cand, Mapping):
                    continue
                box = cand.get("box") or {}
                try:
                    x = float(box["x"])
                    y = float(box["y"])
                    w = float(box["w"])
                    h = float(box["h"])
                except (KeyError, TypeError, ValueError):
                    continue
                if w <= 0 or h <= 0:
                    continue
                # OCR metni yoksa gercek metin uydurulmaz: renk + tur + konum +
                # sira numarasindan ayirt edilebilir, guvenli bir kimlik uretilir
                # (aksi halde tum adaylar "Gorsel CTA adayi" ile gonderiliyordu).
                # Etiketteki "(aday N)", `candidate_id`'nin sira numarasiyla (ve
                # dolayisiyla numarali overlay isaretiyle) BIREBIR eslesir.
                cid = _next_id()
                marker = _marker_number(cid)
                described_label, visual_kind = _describe_visual_candidate(cand, marker, w, h)
                candidates.append(
                    InteractionCandidate(
                        candidate_id=cid,
                        label=described_label,
                        interaction_kind=visual_kind,
                        role="link",
                        x=x,
                        y=y,
                        w=w,
                        h=h,
                        above_fold=(y + h / 2) <= fold_ratio,
                    )
                )
        return candidates, confirmed_candidate_id

    # 3) URL/DOM kaynak: `element_boxes` CSS pikselini fraksiyona normalize et.
    element_boxes = features.get("element_boxes") or []
    width = image_width
    height = image_height
    if isinstance(element_boxes, list) and width and height:
        for element in element_boxes:
            if not isinstance(element, Mapping) or element.get("role") not in ("button", "link"):
                continue
            try:
                x = float(element["x"]) / width
                y = float(element["y"]) / height
                w = float(element["width"]) / width
                h = float(element["height"]) / height
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            if w <= 0 or h <= 0:
                continue
            enriched = {**element, "y_frac": y}
            interaction_kind = _infer_dom_interaction_kind(enriched, w, h)
            if interaction_kind in _EXCLUDED_KINDS:
                continue
            candidates.append(
                InteractionCandidate(
                    candidate_id=_next_id(),
                    label=_safe_label(element.get("label"), "Etkilesim alani"),
                    interaction_kind=interaction_kind,
                    role=str(element.get("role")),
                    x=x,
                    y=y,
                    w=w,
                    h=h,
                    above_fold=(y + h / 2) <= fold_ratio,
                )
            )

    return candidates, confirmed_candidate_id


__all__ = ["build_interaction_candidates", "marker_number"]
