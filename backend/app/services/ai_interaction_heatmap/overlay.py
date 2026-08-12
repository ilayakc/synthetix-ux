"""Numarali aday overlay goruntusu uretici (yalnizca worker/provider tarafi).

Amac (gorev talimati "EKRAN GORUNTUSU ADAYLARINI DUZELT"): OpenAI'in gorselde
gordugu ogeyi GUVENLI bicimde gercek bir `candidate_id` ile eslestirebilmesi
icin, temiz ekran goruntusunun bir KOPYASI uzerine her dogrulanmis adayin
etrafina belirgin bir kutu + yalnizca adayin SIRA NUMARASI cizilir. Bu numara
`candidate_id`'nin sira numarasiyla (bkz. candidates.marker_number) BIREBIR
eslesir. Modele hem temiz goruntu hem bu numarali goruntu gonderilir; modelin
urettigi koordinat DEGIL, yalnizca sectigi `candidate_id` kullanilir - haritadaki
gercek koordinatlar yine analyzer adaylarindan (dogrulanmis) gelir.

Bu modul SAF ve YAN ETKISIZDIR (ag/DB yok). Basarisiz olursa `None` doner ve
cagiran taraf numarali overlay OLMADAN devam eder (temiz goruntu yine gonderilir).
"""

from __future__ import annotations

import io
from collections.abc import Sequence

from app.services.ai_interaction_heatmap.candidates import marker_number
from app.services.ai_interaction_heatmap.schemas import InteractionCandidate

# Numarali overlay islenirken kullanilan azami calisma boyutu (asiri buyuk
# gorsellerde CPU/bellek tuketimini sinirlamak icin en-boy orani korunarak
# kucultulur - image_visual_analysis.MAX_WORKING_DIMENSION ile ayni ruh).
_MAX_WORKING_DIMENSION = 1600
# Yuksek kontrastli, cift katmanli kutu (disi koyu, ici parlak) - hem acik hem
# koyu sayfalarda gorunur kalir.
_OUTER_COLOR = (0, 0, 0, 235)
_INNER_COLOR = (0, 229, 255, 255)  # parlak camgobegi
_OUTER_WIDTH = 5
_INNER_WIDTH = 2
_BADGE_BG = (17, 24, 39, 240)
_BADGE_FG = (255, 255, 255, 255)


def build_numbered_candidate_overlay(
    screenshot_data: bytes,
    candidates: Sequence[InteractionCandidate],
) -> bytes | None:
    """Temiz ekran goruntusunun uzerine numarali aday kutulari cizilmis PNG
    bytes dondurur. Cizilen numara `candidate_id`'nin sira numarasidir.

    Herhangi bir adimda hata olursa `None` doner (numarali overlay opsiyoneldir;
    yoksa yalnizca temiz goruntu gonderilir)."""

    if not screenshot_data or not candidates:
        return None
    try:
        from PIL import Image, ImageColor, ImageDraw, ImageFont

        with Image.open(io.BytesIO(screenshot_data)) as opened:
            base = opened.convert("RGBA")
        width, height = base.size
        if width <= 0 or height <= 0:
            return None

        scale = min(1.0, _MAX_WORKING_DIMENSION / float(max(width, height)))
        if scale < 1.0:
            width = max(1, round(width * scale))
            height = max(1, round(height * scale))
            base = base.resize((width, height), Image.Resampling.LANCZOS)

        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        badge_font = _load_font(ImageFont, max(13, round(min(width, height) / 34)))

        for candidate in candidates:
            marker = marker_number(candidate.candidate_id)
            if marker <= 0:
                continue
            left = int(round(candidate.x * width))
            top = int(round(candidate.y * height))
            right = int(round((candidate.x + candidate.w) * width))
            bottom = int(round((candidate.y + candidate.h) * height))
            left = max(0, min(width - 1, left))
            top = max(0, min(height - 1, top))
            right = max(left + 1, min(width, right))
            bottom = max(top + 1, min(height, bottom))

            # Cift katmanli sinir: disi koyu golge, ici parlak camgobegi.
            draw.rectangle((left, top, right, bottom), outline=_OUTER_COLOR, width=_OUTER_WIDTH)
            draw.rectangle((left, top, right, bottom), outline=_INNER_COLOR, width=_INNER_WIDTH)

            _draw_badge(draw, ImageColor, badge_font, str(marker), left, top)

        composed = Image.alpha_composite(base, overlay).convert("RGB")
        buffer = io.BytesIO()
        composed.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        # Overlay opsiyoneldir; herhangi bir cizim/decode hatasinda sessizce
        # None don (temiz goruntu yine gonderilir).
        return None


def _load_font(image_font_module, size: int):  # noqa: ANN001, ANN202
    try:
        return image_font_module.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        try:
            return image_font_module.truetype("arialbd.ttf", size)
        except Exception:
            return image_font_module.load_default()


def _draw_badge(draw, image_color_module, font, text: str, left: int, top: int) -> None:  # noqa: ANN001
    """Kutunun sol ustune, opak arka planli okunabilir bir numara rozeti cizer."""

    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        text_w, text_h = 10 * len(text), 14
    pad = 4
    badge_w = text_w + pad * 2
    badge_h = text_h + pad * 2
    bx0 = left
    by0 = max(0, top - badge_h)
    draw.rectangle((bx0, by0, bx0 + badge_w, by0 + badge_h), fill=_BADGE_BG)
    draw.text((bx0 + pad, by0 + pad - 1), text, fill=_BADGE_FG, font=font)


__all__ = ["build_numbered_candidate_overlay"]
