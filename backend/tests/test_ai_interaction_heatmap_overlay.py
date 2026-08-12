"""Numarali aday overlay (candidates.marker_number + overlay.build_numbered_
candidate_overlay) testleri (gorev talimati test 3 ve 7)."""

from __future__ import annotations

import io

from PIL import Image

from app.services.ai_interaction_heatmap.candidates import marker_number
from app.services.ai_interaction_heatmap.overlay import build_numbered_candidate_overlay
from app.services.ai_interaction_heatmap.schemas import InteractionCandidate


def _clean_png(width: int = 400, height: int = 800) -> bytes:
    img = Image.new("RGB", (width, height), (240, 240, 240))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _cand(candidate_id: str, x: float, y: float, w: float, h: float) -> InteractionCandidate:
    return InteractionCandidate(
        candidate_id=candidate_id,
        label="aday",
        interaction_kind="visual_button",
        role="link",
        x=x,
        y=y,
        w=w,
        h=h,
        above_fold=y < 0.5,
    )


def _region_changed(base: Image.Image, over: Image.Image, cx: int, cy: int, radius: int = 12) -> bool:
    for px in range(max(0, cx - radius), min(base.width, cx + radius)):
        for py in range(max(0, cy - radius), min(base.height, cy + radius)):
            if base.getpixel((px, py)) != over.getpixel((px, py)):
                return True
    return False


def test_marker_number_matches_candidate_id_ordinal() -> None:
    # Test 3: overlay'de cizilecek numara `candidate_id`'nin sira numarasidir.
    assert marker_number("candidate-1") == 1
    assert marker_number("candidate-7") == 7
    assert marker_number("bozuk-id") == 0


def test_overlay_draws_a_numbered_box_per_candidate() -> None:
    """Test 3/7: her dogrulanmis adayin koordinatinda bir kutu + numara cizilir;
    ekran goruntusu adaylari ayni genel etikete sahip olsa bile numarali gorselle
    (koordinat uzerinden) ayrilabilir."""

    clean = _clean_png()
    cands = [
        _cand("candidate-1", 0.1, 0.1, 0.25, 0.06),
        _cand("candidate-2", 0.55, 0.6, 0.2, 0.05),
    ]
    png = build_numbered_candidate_overlay(clean, cands)
    assert png is not None
    base = Image.open(io.BytesIO(clean)).convert("RGB")
    over = Image.open(io.BytesIO(png)).convert("RGB")
    assert over.size == base.size
    # Her adayin kutu/etiket bolgesinde piksel degisti (kutu + numara cizildi).
    for cand in cands:
        cx = int((cand.x + cand.w / 2) * base.width)
        top_y = int(cand.y * base.height)
        assert _region_changed(base, over, cx, top_y), f"{cand.candidate_id} icin kutu cizilmedi"


def test_overlay_returns_none_on_invalid_image_or_no_candidates() -> None:
    assert build_numbered_candidate_overlay(b"not-a-png", [_cand("candidate-1", 0.1, 0.1, 0.2, 0.05)]) is None
    assert build_numbered_candidate_overlay(_clean_png(), []) is None
