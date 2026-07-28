"""Gercek `PageAnalysis.features` (DOM) verisini motorun beklegi girdiye cevirir.

Paket 4B kapsami: bu modul, analyzer'in urettigi gercek (Playwright tabanli)
`features` JSON'unu dogrudan `app.engine.baseline`/`app.engine.advanced_modules`'e
dagitmaz - kucuk, acikca test edilen bir adapter katmanidir:

- `features` semasini dogrular (eksik/yanlis tip/NaN/Infinity/asiri buyuk liste
  reddedilir) - gecersiz semada `PageAnalysisFeatureError` firlatilir, cagiran
  taraf (bkz. app.services.simulation_worker) bunu run'i FAILED yapmak icin
  kullanir; hicbir kosulda sessizce fixture'a duselmez.
- Gercek DOM alanlarindan (bkz. analyzer/app/schemas.py), motorun bugun
  bekledigi skaler `PageFeatureSnapshot` (app.engine.fixtures) sekliyle AYNI
  tipte, ama URL hash'i yerine gercek olculen/turetilen degerlerle dolu bir
  nesne uretir.
- CTA kanitini sinifllandirir: `element_boxes` icindeki `role in
  {button, link}` kutulari GERCEK DOM semantik kanitina (analyzer'in tag/
  selector eslesmesi) sahip oldugu icin `dom_interactive_candidate` (Paket
  4C duzeltmesi - eskiden `confirmed_cta` deniyordu; her etkilesimli DOM
  ogesi mutlaka bir pazarlama CTA'si degildir, bu yuzden "confirmed" ifadesi
  yaniltici bulunup kaldirildi). `layout_regions.birincil_cta` (sabit "ilk
  eslesen buton" heuristigi, JSON'da rol/tag/href kaniti tasimiyor) DOM
  degil, salt heuristik oldugu icin ayri ve DEGISTIRILMEDEN `cta_candidate`/
  `attention_region` olarak kalir - yeni `visual_cta_candidate` (bkz.
  app.services.image_visual_analysis) ile karistirilmamalidir: o, gercek
  ekran goruntusu piksellerinden turetilir, bu ise DOM layout heuristiginden.
  Kullanicinin secip onayladigi/cizdigi kesin CTA `user_confirmed_cta` olarak
  ayrica ve yalnizca sihirbaz taslak payload'inda tutulur (bkz.
  app.services.test_wizard).
- Provenance (feature_source/page_analysis_id/capture_content_sha256/
  analyzer_version/snapshot_version/goruntu boyutlari/koordinat sistemi/
  unmeasured_fields) uretir - bkz. `DomAdaptedInput.provenance`.

Bilinen limitasyonlar (kasitli, sonuc raporunda da belirtilir):
- `nav_depth`: tek sayfalik bir snapshot cok adimli gercek navigasyon
  derinligini olcemez; olculmedigi icin `baseline.py` formullerine SIFIR
  katki saglayan notr deger (0, rules_config.py'nin kendi dokumante ettigi
  "nav_depth=0 varsayimi" ile hizali) kullanilir - `provenance.
  unmeasured_fields.nav_depth="assumed"` ile acikca isaretlenir, kullaniciya
  olculmus gibi gosterilmez.
- `mobile_friendly`: bu analyzer yolu responsive/mobil viewport testi
  YAPMAZ (bu, ayri ve gercek `network_device_test` modulunun isidir); tespit
  edilmemis bir olumsuzlugu iddia etmemek icin notr varsayim (True, zaten
  `baseline.py` formulune sifir katki - ceza yalnizca `not mobile_friendly`
  iken uygulaniyor) kullanilir - ayni sekilde `provenance.unmeasured_fields.
  mobile_friendly="assumed"` ile isaretlenir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real

from app.engine.fixtures import WCAG_AA_CONTRAST_THRESHOLD, PageFeatureSnapshot
from app.models.page_analysis import PageAnalysis

DOM_ADAPTER_VERSION = "dom-adapter-1"
VISUAL_ADAPTER_VERSION = "visual-adapter-1"

# Savunma amacli yeniden sinirlama (analyzer zaten bu sinirlari uyguluyor ama
# `PageAnalysis.features` guvenilmez, kalici olarak saklanmis bir JSON'dur -
# bkz. modul dokstring'i).
_MAX_ELEMENT_BOXES = 20
_MAX_CONTRAST_CANDIDATES = 15
_MAX_TEXT_FIELD_LENGTH = 300
_MAX_PIXEL_COORDINATE = 20000.0
_CTA_ROLES = ("button", "link")

# `app.services.image_visual_analysis` sinirlariyla ayni buyuklukte, bagimsiz
# bir savunma sinirlamasi (bkz. yukaridaki DOM sinirlarinin ayni gerekcesi).
_MAX_VISUAL_CANDIDATES = 8


class PageAnalysisFeatureError(ValueError):
    """`PageAnalysis.features` beklenen semaya uymuyor/eksik/gecersiz oldugunda
    firlatilir. Fixture'a sessizce duselmez - cagiran run'i FAILED yapar."""


@dataclass(frozen=True)
class DomAdaptedInput:
    snapshot: PageFeatureSnapshot
    cta_evidence: list[dict]
    attention_region_candidate: dict | None
    provenance: dict


def _require_finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise PageAnalysisFeatureError(f"features.{field} sayisal degil")
    number = float(value)
    if not math.isfinite(number):
        raise PageAnalysisFeatureError(f"features.{field} sonlu bir sayi degil (NaN/Infinity)")
    return number


def _require_int(value: object, *, field: str, min_value: int = 0) -> int:
    number = _require_finite_number(value, field=field)
    if number != int(number) or number < min_value:
        raise PageAnalysisFeatureError(f"features.{field} gecerli bir tam sayi degil")
    return int(number)


def _require_dict(value: object, *, field: str) -> dict:
    if not isinstance(value, dict):
        raise PageAnalysisFeatureError(f"features.{field} bir sozluk olmalidir")
    return value


def _require_list(value: object, *, field: str, max_length: int) -> list:
    if not isinstance(value, list):
        raise PageAnalysisFeatureError(f"features.{field} bir liste olmalidir")
    return value[:max_length]


def _truncate_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise PageAnalysisFeatureError(f"features.{field} bir metin olmalidir")
    return value[:_MAX_TEXT_FIELD_LENGTH]


def _valid_pixel_box(box: object) -> dict | None:
    if not isinstance(box, dict):
        return None
    try:
        role = box.get("role")
        if not isinstance(role, str):
            return None
        x = _require_finite_number(box.get("x"), field="element_boxes[].x")
        y = _require_finite_number(box.get("y"), field="element_boxes[].y")
        width = _require_finite_number(box.get("width"), field="element_boxes[].width")
        height = _require_finite_number(box.get("height"), field="element_boxes[].height")
    except PageAnalysisFeatureError:
        return None
    if x < 0 or y < 0 or width < 0 or height < 0:
        return None
    if x > _MAX_PIXEL_COORDINATE or y > _MAX_PIXEL_COORDINATE:
        return None
    if width > _MAX_PIXEL_COORDINATE or height > _MAX_PIXEL_COORDINATE:
        return None
    return {"role": role, "x": x, "y": y, "width": width, "height": height}


def _valid_layout_region_box(box: object) -> dict | None:
    if box is None:
        return None
    if not isinstance(box, dict):
        return None
    try:
        x_pct = _require_finite_number(box.get("x_pct"), field="layout_regions.birincil_cta.x_pct")
        y_pct = _require_finite_number(box.get("y_pct"), field="layout_regions.birincil_cta.y_pct")
        width_pct = _require_finite_number(
            box.get("width_pct"), field="layout_regions.birincil_cta.width_pct"
        )
        height_pct = _require_finite_number(
            box.get("height_pct"), field="layout_regions.birincil_cta.height_pct"
        )
    except PageAnalysisFeatureError:
        return None
    # Kucuk bir tolerans disinda ([-1, 101]) taşan bolgeler bozuk/guvenilmez
    # kabul edilip reddedilir (sahte veri motora gecmez); tolerans icindeyse
    # gecerli [0, 100] araligina clamp edilir.
    for value in (x_pct, y_pct, width_pct, height_pct):
        if value < -1.0 or value > 101.0:
            return None
    return {
        "x_pct": min(max(x_pct, 0.0), 100.0),
        "y_pct": min(max(y_pct, 0.0), 100.0),
        "width_pct": min(max(width_pct, 0.0), 100.0),
        "height_pct": min(max(height_pct, 0.0), 100.0),
    }


def adapt_page_analysis(analysis: PageAnalysis, *, role: str) -> DomAdaptedInput:
    """SUCCEEDED, gecerli `features` tasiyan bir URL-kaynakli PageAnalysis'i
    motorun bekledigi normalize edilmis girdiye cevirir.

    Cagiran taraf (bkz. app.services.simulation_worker._load_dom_input) bu
    fonksiyonu cagirmadan once `analysis.status == SUCCEEDED` oldugunu zaten
    dogrulamis olmalidir; burada yalnizca `features` icerigi dogrulanir.
    """

    if analysis.features is None:
        raise PageAnalysisFeatureError("PageAnalysis.features bos (SUCCEEDED ama veri yok)")
    if not analysis.content_sha256:
        raise PageAnalysisFeatureError("PageAnalysis.content_sha256 eksik")
    if not analysis.url:
        raise PageAnalysisFeatureError("PageAnalysis.url eksik")

    features = _require_dict(analysis.features, field="")

    text_stats = _require_dict(features.get("text_stats"), field="text_stats")
    controls = _require_dict(features.get("controls"), field="controls")
    layout_regions = _require_dict(features.get("layout_regions") or {}, field="layout_regions")

    element_boxes_raw = _require_list(
        features.get("element_boxes"), field="element_boxes", max_length=_MAX_ELEMENT_BOXES
    )
    contrast_candidates_raw = _require_list(
        features.get("contrast_candidates"),
        field="contrast_candidates",
        max_length=_MAX_CONTRAST_CANDIDATES,
    )

    valid_boxes = [b for b in (_valid_pixel_box(box) for box in element_boxes_raw) if b is not None]
    cta_boxes = [b for b in valid_boxes if b["role"] in _CTA_ROLES]

    contrast_ratios: list[float] = []
    for candidate in contrast_candidates_raw:
        if not isinstance(candidate, dict):
            continue
        try:
            ratio = _require_finite_number(candidate.get("ratio"), field="contrast_candidates[].ratio")
        except PageAnalysisFeatureError:
            continue
        if ratio < 0:
            continue
        contrast_ratios.append(ratio)

    if contrast_ratios:
        min_contrast_ratio = round(min(contrast_ratios), 2)
        avg_contrast_ratio = round(sum(contrast_ratios) / len(contrast_ratios), 2)
    else:
        # Sayfada orneklenebilir gorunur metin bulunamadi - ne "gecti" ne
        # "kaldi" iddiasi uydurulmaz, notr esik degeri kullanilir.
        min_contrast_ratio = WCAG_AA_CONTRAST_THRESHOLD
        avg_contrast_ratio = WCAG_AA_CONTRAST_THRESHOLD

    birincil_cta_box = _valid_layout_region_box(layout_regions.get("birincil_cta"))

    word_count = _require_int(text_stats.get("word_count"), field="text_stats.word_count")
    avg_sentence_word_count = _require_finite_number(
        text_stats.get("avg_sentence_word_count"), field="text_stats.avg_sentence_word_count"
    )
    heading_count = _require_int(text_stats.get("heading_count"), field="text_stats.heading_count")
    form_field_count = _require_int(controls.get("form_field_count"), field="controls.form_field_count")

    snapshot = PageFeatureSnapshot(
        fixture_version=f"{DOM_ADAPTER_VERSION}:{analysis.analyzer_version or 'unknown'}",
        url=analysis.url,
        role=role,
        nav_depth=0,
        primary_cta_count=len(cta_boxes),
        form_field_count=form_field_count,
        above_fold_cta=birincil_cta_box is not None,
        page_word_count=word_count,
        avg_sentence_word_count=round(avg_sentence_word_count, 2),
        heading_count=heading_count,
        min_contrast_ratio=min_contrast_ratio,
        avg_contrast_ratio=avg_contrast_ratio,
        mobile_friendly=True,
        input_hash=analysis.content_sha256,
    )

    cta_evidence = [
        {
            "classification": "dom_interactive_candidate",
            "evidence": "dom_element_role",
            "role": box["role"],
            "x": box["x"],
            "y": box["y"],
            "width": box["width"],
            "height": box["height"],
        }
        for box in cta_boxes
    ]

    attention_region_candidate = None
    if birincil_cta_box is not None:
        attention_region_candidate = {
            "classification": "cta_candidate",
            "evidence": "heuristic_layout_region",
            **birincil_cta_box,
        }

    provenance = {
        "feature_source": "dom",
        "page_analysis_id": str(analysis.id),
        "capture_content_sha256": analysis.content_sha256,
        "analyzer_version": analysis.analyzer_version,
        "snapshot_version": analysis.snapshot_version,
        "image_width": analysis.image_width,
        "image_height": analysis.image_height,
        "coordinate_system": {
            "element_boxes": "css_pixels_viewport_top_left_origin",
            "layout_regions": "percent_viewport_top_left_origin",
        },
        # Bu alanlar gercekten OLCULMEDI - notr/varsayilan degerlerdir (bkz.
        # modul dokstring'i). Kullaniciya olculmus sonuc gibi gosterilmemesi
        # icin acikca isaretlenir.
        "unmeasured_fields": {
            "nav_depth": "assumed",
            "mobile_friendly": "assumed",
        },
    }

    return DomAdaptedInput(
        snapshot=snapshot,
        cta_evidence=cta_evidence,
        attention_region_candidate=attention_region_candidate,
        provenance=provenance,
    )


def _require_unit_fraction(value: object, *, field: str) -> float:
    number = _require_finite_number(value, field=field)
    if number < 0.0 or number > 1.0:
        raise PageAnalysisFeatureError(f"features.{field} [0, 1] araliginda olmalidir")
    return number


def _valid_unit_box(box: object, *, field: str) -> dict | None:
    if not isinstance(box, dict):
        return None
    try:
        return {
            "x": _require_unit_fraction(box.get("x"), field=f"{field}.x"),
            "y": _require_unit_fraction(box.get("y"), field=f"{field}.y"),
            "w": _require_unit_fraction(box.get("w"), field=f"{field}.w"),
            "h": _require_unit_fraction(box.get("h"), field=f"{field}.h"),
        }
    except PageAnalysisFeatureError:
        return None


def adapt_visual_page_analysis(
    analysis: PageAnalysis, *, role: str, user_confirmed_cta: dict | None = None
) -> DomAdaptedInput:
    """SUCCEEDED, gecerli `features` tasiyan bir DesignAsset-kaynakli
    (`feature_source="visual_heuristic"`) PageAnalysis'i motorun bekledigi
    normalize edilmis girdiye cevirir - `adapt_page_analysis`'in (DOM) piksel
    tabanli OpenCV kanitina gore simetrik ikinci fonksiyonu.

    DOM olmadigi icin `page_word_count`/`heading_count`/`form_field_count`
    sifir kabul edilir (sahte metin sezgisi UYDURULMAZ, bkz. `provenance.
    unmeasured_fields`); kontrast, `image_visual_analysis`in urettigi
    bolgesel WCAG luminance kontrasti tahmininden gelir.

    `user_confirmed_cta` verilirse (bkz. app.services.test_wizard - sihirbaz
    taslaginda kullanicinin cizdigi/onayladigi kutu, launch aninda
    `SimulationRun.input_snapshot`'a degismez kopyalanir), CTA kanit
    listesinde EN ONCELIKLI (`classification="user_confirmed_cta"`) olarak
    yer alir ve gorsel adaylarin onune gecer; DesignAsset sonradan
    silinse/degisse bile bu run'in kendi input_snapshot kopyasi etkilenmez.

    `features` semasi eksik/gecersizse (`PageAnalysisFeatureError`)
    firlatilir - cagiran taraf (bkz. app.services.simulation_worker) bunu
    run'i FAILED yapmak icin kullanir, hicbir kosulda sessizce fixture'a
    dusmez."""

    if analysis.features is None:
        raise PageAnalysisFeatureError("PageAnalysis.features bos (SUCCEEDED ama veri yok)")
    if not analysis.content_sha256:
        raise PageAnalysisFeatureError("PageAnalysis.content_sha256 eksik")

    features = _require_dict(analysis.features, field="")
    if features.get("feature_source") != "visual_heuristic":
        raise PageAnalysisFeatureError("features.feature_source 'visual_heuristic' degil")

    candidates_raw = _require_list(
        features.get("visual_cta_candidates"),
        field="visual_cta_candidates",
        max_length=_MAX_VISUAL_CANDIDATES,
    )
    attention = _require_dict(
        features.get("synthetic_attention_estimate"), field="synthetic_attention_estimate"
    )
    if not isinstance(attention.get("cells"), list):
        raise PageAnalysisFeatureError("features.synthetic_attention_estimate.cells eksik")

    valid_candidates: list[dict] = []
    contrast_ratios: list[float] = []
    for candidate in candidates_raw:
        if not isinstance(candidate, dict):
            continue
        box = _valid_unit_box(candidate.get("box"), field="visual_cta_candidates[].box")
        if box is None:
            continue
        try:
            score = _require_finite_number(
                candidate.get("heuristic_score"), field="visual_cta_candidates[].heuristic_score"
            )
        except PageAnalysisFeatureError:
            continue
        valid_candidates.append({**box, "heuristic_score": score})

        ratio = candidate.get("regional_visual_contrast_estimate")
        if (
            isinstance(ratio, int | float)
            and not isinstance(ratio, bool)
            and math.isfinite(ratio)
            and ratio >= 0
        ):
            contrast_ratios.append(float(ratio))

    if contrast_ratios:
        min_contrast_ratio = round(min(contrast_ratios), 2)
        avg_contrast_ratio = round(sum(contrast_ratios) / len(contrast_ratios), 2)
    else:
        # Aday bulunamadi - ne "gecti" ne "kaldi" iddiasi uydurulmaz, notr esik
        # degeri kullanilir (DOM adapter'iyla ayni ilke).
        min_contrast_ratio = WCAG_AA_CONTRAST_THRESHOLD
        avg_contrast_ratio = WCAG_AA_CONTRAST_THRESHOLD

    cta_evidence: list[dict] = []
    primary_cta_count = len(valid_candidates)
    above_fold_cta = False

    if user_confirmed_cta is not None:
        box = user_confirmed_cta.get("box") or {}
        cta_evidence.append(
            {
                "classification": "user_confirmed_cta",
                "evidence": "user_confirmed",
                "role": "user_confirmed",
                "x": box.get("x"),
                "y": box.get("y"),
                "width": box.get("w"),
                "height": box.get("h"),
            }
        )
        primary_cta_count = max(primary_cta_count, 1)
        try:
            above_fold_cta = float(box.get("y", 1.0)) < 0.5
        except (TypeError, ValueError):
            above_fold_cta = False
    elif valid_candidates:
        best = max(valid_candidates, key=lambda c: c["heuristic_score"])
        above_fold_cta = best["y"] < 0.5

    for candidate in sorted(valid_candidates, key=lambda c: c["heuristic_score"], reverse=True):
        cta_evidence.append(
            {
                "classification": "visual_cta_candidate",
                "evidence": "opencv_heuristic",
                "role": "visual_candidate",
                "x": candidate["x"],
                "y": candidate["y"],
                "width": candidate["w"],
                "height": candidate["h"],
                "heuristic_score": candidate["heuristic_score"],
            }
        )

    # `PageFeatureSnapshot.url` (bkz. app.engine.fixtures) tarihsel olarak
    # zorunlu/dolu bir alan - DesignAsset kaynaginda GERCEK bir URL yok, bu
    # yuzden burada yalnizca ic/teshis amacli bir kimlik metni (`source_
    # reference` ile AYNI desen) tasir. Bu deger `baseline.py`/
    # `advanced_modules.py`'de SKORLAMA icin HICBIR ZAMAN okunmaz (skor icin
    # gercek kaynak `snapshot.input_hash` = `analysis.content_sha256`'dir,
    # asagida) - yalnizca `result["page_feature_snapshot"]["url"]` ham
    # teshis ciktisinda gorunur; kullaniciya gosterilen `result["url"]`/
    # rapor "kaynak" alani BUNDAN turemez (bkz. app.services.test_wizard
    # .launch_draft - `input_snapshot["source_reference"]` ayri ve acikca
    # etiketlenir).
    diagnostic_identity = analysis.url or f"design-asset:{analysis.design_asset_id}"
    snapshot = PageFeatureSnapshot(
        fixture_version=f"{VISUAL_ADAPTER_VERSION}:{features.get('algorithm_version') or 'unknown'}",
        url=diagnostic_identity,
        role=role,
        nav_depth=0,
        primary_cta_count=primary_cta_count,
        form_field_count=0,
        above_fold_cta=above_fold_cta,
        page_word_count=0,
        avg_sentence_word_count=0.0,
        heading_count=0,
        min_contrast_ratio=min_contrast_ratio,
        avg_contrast_ratio=avg_contrast_ratio,
        mobile_friendly=True,
        input_hash=analysis.content_sha256,
    )

    provenance = {
        "feature_source": "visual_heuristic",
        "page_analysis_id": str(analysis.id),
        "capture_content_sha256": analysis.content_sha256,
        "analyzer_version": None,
        "snapshot_version": analysis.snapshot_version,
        "image_width": analysis.image_width,
        "image_height": analysis.image_height,
        "coordinate_system": {
            "visual_cta_candidates": "fraction_of_image_top_left_origin",
            "synthetic_attention_estimate": "fraction_of_image_top_left_origin",
        },
        # DOM olmadigi icin bu alanlar hicbir zaman gercekten OLCULMEZ - notr/
        # uygulanamaz olarak acikca isaretlenir (bkz. modul dokstring'i).
        "unmeasured_fields": {
            "nav_depth": "assumed",
            "mobile_friendly": "assumed",
            "page_word_count": "not_applicable_visual_only",
            "heading_count": "not_applicable_visual_only",
            "form_field_count": "not_applicable_visual_only",
        },
        "user_confirmed_cta_applied": user_confirmed_cta is not None,
    }

    return DomAdaptedInput(
        snapshot=snapshot,
        cta_evidence=cta_evidence,
        attention_region_candidate=None,
        provenance=provenance,
    )


__all__ = [
    "DOM_ADAPTER_VERSION",
    "VISUAL_ADAPTER_VERSION",
    "PageAnalysisFeatureError",
    "DomAdaptedInput",
    "adapt_page_analysis",
    "adapt_visual_page_analysis",
]
