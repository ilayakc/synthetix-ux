"""DesignAsset ekran goruntuleri icin tamamen yerel/deterministik gorsel analiz.

Paket 4C kapsami: bu modul, `app.services.page_analysis._process_design_asset_source`
tarafindan cagirilir - bir DesignAsset ekran goruntusunun `PageAnalysis.features`
alanini doldurmak icin TEK yerdir. Harici bir vision API'sine hicbir cagri
yapilmaz, gorsel hicbir zaman ucuncu bir tarafa gonderilmez; tum islem OpenCV
(opencv-python-headless) + numpy ile bellek icinde yapilir.

Ureten sonuclar:
- `visual_cta_candidates`: kenar/kontur, boyut ve konum sezgilerinden turetilen
  aday dikdortgenler. `heuristic_score` GERCEK bir tiklama olasiligi veya
  istatistiksel guven degildir - yalnizca aday SIRALAMA skorudur (bkz.
  docs/methodology.md). Hic aday bulunamamasi gecerli, sahte aday uretilmez.
- `synthetic_attention_estimate`: luminance/kontrast + kenar yogunlugu +
  boyut/konum sezgilerinden turetilen izgara tabanli, normalize edilmis bir
  "sentetik dikkat" tahmini - GERCEK goz takibi/tiklama verisi degildir.

Guvenlik/kaynak sinirlari: bu fonksiyon yalnizca `app.services.image_safety`
ile ONCEDEN decode/format/boyut/piksel dogrulamasindan gecmis bytes uzerinde
calisir (yeniden decode/format kontrolu yapmaz - cagiran taraf sorumludur).
Yine de calisma cozunurlugu burada BAGIMSIZ olarak ust sinirlanir (asiri
buyuk gorsellerde CPU/bellek tuketimini sinirlamak icin), NaN/Infinity
degerler reddedilir, gecici dosya kullanilmaz (bellek ici cv2.imdecode),
binary/piksel verisi hicbir yerde loglanmaz.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

ALGORITHM_VERSION = "visual-analysis-2"

# Guvenlik: gorsel zaten image_safety tarafindan dogrulanmis olsa da, OpenCV
# islemesi CPU/bellek acisindan pahali olabilir - calisma cozunurlugu burada
# BAGIMSIZ olarak ust sinirlanir (aspect ratio korunarak kucultulur).
MAX_WORKING_DIMENSION = 1600

_MAX_CANDIDATES = 8
_MIN_CANDIDATE_AREA_FRACTION = 0.0008
_MAX_CANDIDATE_AREA_FRACTION = 0.35
_MIN_ASPECT_RATIO = 0.12
_MAX_ASPECT_RATIO = 8.0
_CONTRAST_MARGIN_PX = 6

_ATTENTION_GRID_COLS = 12
_ATTENTION_GRID_ROWS = 8

_LIMITATIONS = [
    "Bu sonuclar gercek kullanici tiklamasi veya goz takibi verisi degildir.",
    "Bu gorsel icin DOM bilgisi mevcut degildir (yalnizca piksel tabanli sezgiler kullanilir).",
]

_ATTENTION_DISCLAIMER = (
    "Sentetik dikkat tahmini: bu sonuc, sayfa gorselinin piksel ozelliklerinden "
    "(parlaklik/renk kontrasti, kenar yogunlugu, boyut/konum sezgileri) turetilen "
    "model/algoritma tabanli bir tahmindir. Kullanicilarin gercekten baktigi yer, "
    "en cok tiklanan alan veya gercek goz takibi/kullanici davranisi verisi DEGILDIR."
)

_CANDIDATE_DISCLAIMER = (
    "Bu alanlar gorsel ozelliklere gore CTA adayi olarak onerilir. Gercek kullanici "
    "tiklamasi veya goz takibi verisi degildir; yalnizca aday siralama skorudur."
)


class VisualAnalysisError(ValueError):
    """Gorsel OpenCV ile islenemedigi veya sonuc sonlu/gecerli olmadiginda firlatilir."""


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG bagil luminance formulu - analyzer/app/browser.py::relativeLuminance
    ile BIREBIR AYNI sabitlerle (0.2126/0.7152/0.0722, gamma 2.4) yeniden yazilir."""

    def channel(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(rgb_a: tuple[float, float, float], rgb_b: tuple[float, float, float]) -> float:
    l1 = _relative_luminance(rgb_a) + 0.05
    l2 = _relative_luminance(rgb_b) + 0.05
    return l1 / l2 if l1 > l2 else l2 / l1


def _decode_bgr(image_bytes: bytes) -> np.ndarray:
    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None or image.size == 0:
        raise VisualAnalysisError("Gorsel OpenCV ile decode edilemedi")
    return image


def _resize_for_processing(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        raise VisualAnalysisError("Gorsel boyutlari gecersiz")
    scale = min(1.0, MAX_WORKING_DIMENSION / max(height, width))
    if scale >= 1.0:
        return image
    new_width = max(1, round(width * scale))
    new_height = max(1, round(height * scale))
    return cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)


def _mean_bgr(image: np.ndarray, x: int, y: int, w: int, h: int) -> tuple[float, float, float] | None:
    region = image[y : y + h, x : x + w]
    if region.size == 0:
        return None
    mean = region.reshape(-1, 3).mean(axis=0)
    b, g, r = (float(mean[0]), float(mean[1]), float(mean[2]))
    if not all(math.isfinite(v) for v in (b, g, r)):
        return None
    return (r, g, b)


def _surrounding_mean_bgr(
    image: np.ndarray, x: int, y: int, w: int, h: int, margin: int
) -> tuple[float, float, float] | None:
    height, width = image.shape[:2]
    outer_x0, outer_y0 = max(0, x - margin), max(0, y - margin)
    outer_x1, outer_y1 = min(width, x + w + margin), min(height, y + h + margin)
    outer = image[outer_y0:outer_y1, outer_x0:outer_x1].copy()
    if outer.size == 0:
        return None
    # Ic bolgeyi cikar (yalnizca cevre halkasinin ortalamasi alinsin).
    inner_x0, inner_y0 = x - outer_x0, y - outer_y0
    inner_x1, inner_y1 = inner_x0 + w, inner_y0 + h
    mask = np.ones(outer.shape[:2], dtype=bool)
    mask[max(0, inner_y0) : max(0, inner_y1), max(0, inner_x0) : max(0, inner_x1)] = False
    pixels = outer[mask]
    if pixels.size == 0:
        return None
    mean = pixels.reshape(-1, 3).mean(axis=0)
    b, g, r = (float(mean[0]), float(mean[1]), float(mean[2]))
    if not all(math.isfinite(v) for v in (b, g, r)):
        return None
    return (r, g, b)


def _find_candidates(bgr: np.ndarray, gray: np.ndarray, edges: np.ndarray) -> list[dict]:
    work_h, work_w = gray.shape[:2]
    total_area = float(work_h * work_w)
    if total_area <= 0:
        return []

    kernel = np.ones((3, 3), np.uint8)
    dilated_edges = cv2.dilate(edges, kernel, iterations=1)
    contours, _ = cv2.findContours(dilated_edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    raw_candidates: list[dict] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            continue
        area_fraction = (w * h) / total_area
        if area_fraction < _MIN_CANDIDATE_AREA_FRACTION or area_fraction > _MAX_CANDIDATE_AREA_FRACTION:
            continue
        aspect_ratio = w / h
        if aspect_ratio < _MIN_ASPECT_RATIO or aspect_ratio > _MAX_ASPECT_RATIO:
            continue

        inner_mean = _mean_bgr(bgr, x, y, w, h)
        outer_mean = _surrounding_mean_bgr(bgr, x, y, w, h, _CONTRAST_MARGIN_PX)
        if inner_mean is None or outer_mean is None:
            continue

        contrast_ratio = _contrast_ratio(inner_mean, outer_mean)
        if not math.isfinite(contrast_ratio):
            continue
        # 1.0 (fark yok) .. 21.0 (maksimum WCAG karsitligi) araligini 0-1'e sikistir.
        contrast_component = min(max((contrast_ratio - 1.0) / 20.0, 0.0), 1.0)

        size_component = min(area_fraction / 0.05, 1.0)

        center_y = y + h / 2.0
        position_component = max(0.0, 1.0 - (center_y / work_h))

        heuristic_score = round(0.4 * size_component + 0.3 * position_component + 0.3 * contrast_component, 4)
        if not math.isfinite(heuristic_score):
            continue

        evidence = ["edge_contour", "size_heuristic", "position_heuristic"]
        if contrast_component > 0.15:
            evidence.append("local_contrast_heuristic")

        raw_candidates.append(
            {
                "kind": "visual_cta_candidate",
                "box": {
                    "x": round(x / work_w, 6),
                    "y": round(y / work_h, 6),
                    "w": round(w / work_w, 6),
                    "h": round(h / work_h, 6),
                },
                "heuristic_score": heuristic_score,
                "dominant_color": {
                    "r": round(inner_mean[0]),
                    "g": round(inner_mean[1]),
                    "b": round(inner_mean[2]),
                },
                "regional_visual_contrast_estimate": round(contrast_ratio, 2),
                "size_component": round(size_component, 4),
                "position_component": round(position_component, 4),
                "contrast_component": round(contrast_component, 4),
                "evidence": evidence,
                "algorithm_version": ALGORITHM_VERSION,
            }
        )

    raw_candidates.sort(key=lambda c: c["heuristic_score"], reverse=True)
    return raw_candidates[:_MAX_CANDIDATES]


def _robust_unit_map(values: np.ndarray) -> np.ndarray:
    """Aykiri tek bir pikselin tum haritayi bastirmadigi 0..1 olcekleme."""

    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return np.zeros_like(values, dtype=np.float32)
    low = float(np.percentile(finite, 20))
    high = float(np.percentile(finite, 95))
    if not math.isfinite(low) or not math.isfinite(high) or high <= low + 1e-9:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip((values - low) / (high - low), 0.0, 1.0).astype(np.float32)


def _synthetic_attention_estimate(
    bgr: np.ndarray,
    gray: np.ndarray,
    edges: np.ndarray,
) -> dict:
    work_h, work_w = gray.shape[:2]
    cell_h = max(1, work_h // _ATTENTION_GRID_ROWS)
    cell_w = max(1, work_w // _ATTENTION_GRID_COLS)

    # Tek bir "ustte olma" katsayisi eski algoritmada navigasyonu yapay olarak
    # sicak gosteriyordu. Yeni surum, yerel ve genis olcekli renk/kontrast farkini,
    # yumusatilan kenar yogunlugunu ve doygunlugu birlestirir. Merkez onceligi
    # yalnizca zayif bir okunabilirlik onceligidir; sonucu tek basina belirlemez.
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    short_edge = max(1, min(work_h, work_w))
    local_sigma = max(3.0, short_edge / 55.0)
    broad_sigma = max(9.0, short_edge / 14.0)
    local_mean = cv2.GaussianBlur(lab, (0, 0), sigmaX=local_sigma, sigmaY=local_sigma)
    broad_mean = cv2.GaussianBlur(lab, (0, 0), sigmaX=broad_sigma, sigmaY=broad_sigma)
    local_contrast = np.linalg.norm(lab - local_mean, axis=2)
    broad_contrast = np.linalg.norm(lab - broad_mean, axis=2)
    edge_map = cv2.GaussianBlur(
        (edges > 0).astype(np.float32),
        (0, 0),
        sigmaX=max(2.0, short_edge / 180.0),
        sigmaY=max(2.0, short_edge / 180.0),
    )
    saturation = hsv[:, :, 1] / 255.0

    saliency = (
        0.34 * _robust_unit_map(local_contrast)
        + 0.30 * _robust_unit_map(broad_contrast)
        + 0.24 * _robust_unit_map(edge_map)
        + 0.12 * np.clip(saturation, 0.0, 1.0)
    )
    yy, xx = np.mgrid[0:work_h, 0:work_w]
    x_norm = (xx + 0.5) / work_w
    y_norm = (yy + 0.5) / work_h
    center_prior = np.exp(
        -0.5 * (((x_norm - 0.5) / 0.48) ** 2 + ((y_norm - 0.48) / 0.52) ** 2)
    )
    saliency *= (0.86 + 0.14 * center_prior).astype(np.float32)
    # Ince kampanya/duyuru seridi, metin ve kenar yogunlugu nedeniyle tum sayfayi
    # bastirmasin. Arama ve ana navigasyon bandi korunur; yalnizca ilk %7 zayiflatilir.
    saliency[y_norm < 0.07] *= 0.55

    raw_cells: list[dict] = []
    for row in range(_ATTENTION_GRID_ROWS):
        for col in range(_ATTENTION_GRID_COLS):
            y0, y1 = row * cell_h, min(work_h, (row + 1) * cell_h)
            x0, x1 = col * cell_w, min(work_w, (col + 1) * cell_w)
            if y1 <= y0 or x1 <= x0:
                continue

            saliency_cell = saliency[y0:y1, x0:x1]
            if saliency_cell.size == 0:
                continue
            mean_score = float(saliency_cell.mean())
            peak_score = float(np.percentile(saliency_cell, 82))
            raw_score = 0.62 * mean_score + 0.38 * peak_score
            if not math.isfinite(raw_score):
                continue

            raw_cells.append(
                {
                    "x": round(x0 / work_w, 6),
                    "y": round(y0 / work_h, 6),
                    "w": round((x1 - x0) / work_w, 6),
                    "h": round((y1 - y0) / work_h, 6),
                    "_raw_score": raw_score,
                }
            )

    raw_scores = np.asarray([cell["_raw_score"] for cell in raw_cells], dtype=np.float32)
    normalized_scores = _robust_unit_map(raw_scores)
    cells = []
    for cell, normalized in zip(raw_cells, normalized_scores, strict=True):
        intensity = round(float(normalized), 4)
        if not math.isfinite(intensity):
            intensity = 0.0
        cells.append(
            {
                "x": cell["x"],
                "y": cell["y"],
                "w": cell["w"],
                "h": cell["h"],
                "intensity": intensity,
            }
        )

    return {
        "cells": cells,
        "feature_source": "visual_heuristic",
        "algorithm_version": ALGORITHM_VERSION,
        "disclaimer": _ATTENTION_DISCLAIMER,
    }


def analyze_screenshot(image_bytes: bytes) -> dict:
    """Zaten `image_safety.decode_and_validate_image` ile dogrulanmis bir
    ekran goruntusu bytes'indan deterministik CTA adaylari + sentetik dikkat
    tahmini uretir. Bozuk/beklenmeyen veri durumunda `VisualAnalysisError`
    firlatilir - cagiran taraf (bkz. app.services.page_analysis) bunu isi
    FAILED yapmak icin kullanir, hicbir zaman kismi sonuc yazilmaz."""

    try:
        bgr = _decode_bgr(image_bytes)
        bgr = _resize_for_processing(bgr)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 50, 150)

        candidates = _find_candidates(bgr, gray, edges)
        attention = _synthetic_attention_estimate(bgr, gray, edges)
    except VisualAnalysisError:
        raise
    except Exception as exc:  # OpenCV/numpy beklenmeyen hatasi - guvenli FAILED'a cevrilir
        raise VisualAnalysisError("Gorsel analiz beklenmeyen bir hatayla karsilasti") from exc

    return {
        "feature_source": "visual_heuristic",
        "algorithm_version": ALGORITHM_VERSION,
        "visual_cta_candidates": candidates,
        "candidate_disclaimer": _CANDIDATE_DISCLAIMER,
        "synthetic_attention_estimate": attention,
        "limitations": list(_LIMITATIONS),
    }


__all__ = [
    "ALGORITHM_VERSION",
    "MAX_WORKING_DIMENSION",
    "VisualAnalysisError",
    "analyze_screenshot",
]
