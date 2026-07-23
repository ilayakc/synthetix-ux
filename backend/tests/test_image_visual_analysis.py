"""`app.services.image_visual_analysis.analyze_screenshot` icin birim testleri.

Bu servis, DesignAsset ekran goruntuleri icin TEK yerel/deterministik gorsel
analiz noktasidir (bkz. app.services.page_analysis._process_design_asset_source).
Testler; determinizm, kontrast farkina gore skor degisimi, buyuk gorsel
kucultme + normalize koordinat dogrulugu, kaynak siniri ve "hic aday yok"
durumunun gecerli bir sonuc oldugunu kanitlar.
"""

from __future__ import annotations

import io
import math

import pytest
from PIL import Image

from app.services.image_visual_analysis import (
    MAX_WORKING_DIMENSION,
    VisualAnalysisError,
    analyze_screenshot,
)

pytestmark = pytest.mark.unit


def _png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _button_image(
    *,
    size: tuple[int, int] = (800, 600),
    button_box: tuple[int, int, int, int] = (300, 100, 500, 150),
    button_color: tuple[int, int, int] = (10, 10, 10),
    bg_color: tuple[int, int, int] = (245, 245, 245),
) -> bytes:
    image = Image.new("RGB", size, color=bg_color)
    x0, y0, x1, y1 = button_box
    for y in range(y0, y1):
        for x in range(x0, x1):
            image.putpixel((x, y), button_color)
    return _png_bytes(image)


def _flat_image(size: tuple[int, int] = (400, 300), color: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    return _png_bytes(Image.new("RGB", size, color=color))


def _assert_all_finite(value: object) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _assert_all_finite(v)
    elif isinstance(value, list):
        for v in value:
            _assert_all_finite(v)
    elif isinstance(value, float):
        assert math.isfinite(value), f"sonlu olmayan deger bulundu: {value!r}"


def test_deterministic_equivalence():
    """Ayni gorsel bytes'i iki kez analiz edilince BIREBIR ayni JSON uretilmeli."""

    raw = _button_image()
    result_a = analyze_screenshot(raw)
    result_b = analyze_screenshot(raw)
    assert result_a == result_b


def test_button_like_region_produces_candidate():
    raw = _button_image()
    result = analyze_screenshot(raw)
    assert len(result["visual_cta_candidates"]) >= 1
    candidate = result["visual_cta_candidates"][0]
    assert candidate["kind"] == "visual_cta_candidate"
    assert 0.0 <= candidate["box"]["x"] <= 1.0
    assert 0.0 <= candidate["box"]["y"] <= 1.0
    assert 0.0 < candidate["box"]["w"] <= 1.0
    assert 0.0 < candidate["box"]["h"] <= 1.0
    assert "edge_contour" in candidate["evidence"]


def test_flat_image_produces_no_fabricated_candidates():
    """Tek renkli, kenari olmayan bir gorselde sahte aday uretilmemeli - bos liste gecerli sonuctur."""

    raw = _flat_image()
    result = analyze_screenshot(raw)
    assert result["visual_cta_candidates"] == []
    assert result["synthetic_attention_estimate"]["cells"] != []


def test_higher_contrast_button_scores_higher():
    """Ayni boyut/konumda, cevresine gore daha yuksek kontrastli buton daha yuksek skor almali."""

    low_contrast = _button_image(button_color=(210, 210, 210), bg_color=(245, 245, 245))
    high_contrast = _button_image(button_color=(5, 5, 5), bg_color=(245, 245, 245))

    low_result = analyze_screenshot(low_contrast)
    high_result = analyze_screenshot(high_contrast)

    assert low_result["visual_cta_candidates"], "dusuk kontrastli senaryoda da aday bulunmali"
    assert high_result["visual_cta_candidates"], "yuksek kontrastli senaryoda aday bulunmali"

    low_score = low_result["visual_cta_candidates"][0]["heuristic_score"]
    high_score = high_result["visual_cta_candidates"][0]["heuristic_score"]
    assert high_score > low_score


def test_large_image_is_resized_and_normalized_correctly():
    """MAX_WORKING_DIMENSION uzerindeki bir gorsel kucultulur; normalize kutu
    oranlari orijinal gorseldeki gercek buton konumuyla tutarli kalmalidir."""

    large_size = (MAX_WORKING_DIMENSION + 800, round((MAX_WORKING_DIMENSION + 800) * 0.75))
    width, height = large_size
    # Butonu gorselin yatayda ortasina, dikeyde ust yarisina yerlestir.
    button_box = (round(width * 0.375), round(height * 0.15), round(width * 0.625), round(height * 0.35))
    raw = _button_image(size=large_size, button_box=button_box)

    result = analyze_screenshot(raw)
    assert result["visual_cta_candidates"], "buyuk gorselde de aday bulunmali"
    candidate = result["visual_cta_candidates"][0]
    # Normalize x merkezi ~0.5, y merkezi ~0.25 civarinda olmali (orijinal oranlarla tutarli).
    center_x = candidate["box"]["x"] + candidate["box"]["w"] / 2
    center_y = candidate["box"]["y"] + candidate["box"]["h"] / 2
    assert 0.3 < center_x < 0.7
    assert 0.1 < center_y < 0.4


def test_resource_bound_working_resolution_is_capped():
    """Cok buyuk bir gorsel bile MAX_WORKING_DIMENSION sinirinda islenir (CPU/bellek koruması)."""

    large_size = (MAX_WORKING_DIMENSION * 2, round(MAX_WORKING_DIMENSION * 2 * 0.6))
    raw = _flat_image(size=large_size)
    # Patlamadan/asiri surede takilmadan tamamlanmali.
    result = analyze_screenshot(raw)
    assert result["algorithm_version"]


def test_all_numeric_outputs_are_finite_no_nan_or_infinity():
    raw = _button_image()
    result = analyze_screenshot(raw)
    _assert_all_finite(result)


def test_invalid_bytes_raise_visual_analysis_error():
    with pytest.raises(VisualAnalysisError):
        analyze_screenshot(b"not-a-real-image-payload")


def test_result_shape_has_required_top_level_fields():
    raw = _button_image()
    result = analyze_screenshot(raw)
    assert result["feature_source"] == "visual_heuristic"
    assert result["algorithm_version"]
    assert "limitations" in result and result["limitations"]
    assert result["synthetic_attention_estimate"]["disclaimer"]
    assert result["synthetic_attention_estimate"]["feature_source"] == "visual_heuristic"
