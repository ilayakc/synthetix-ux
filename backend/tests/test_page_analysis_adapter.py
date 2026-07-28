"""`app.services.page_analysis_adapter.adapt_page_analysis` icin birim testleri.

Bu adapter, Paket 4B'nin can alici bilimsel durustluk garantisini tasir: gercek
`PageAnalysis.features` (DOM) verisinden turetilen motor girdisinin, eski
`sha256(url)` fixture yer tutucusunun aksine, GERCEKTEN feature icerigine bagli
oldugunu ve URL string'inin/hash'inin gizli bir skor girdisi OLMADIGINI
kanitlar (bkz. plan dosyasi "Gercek verinin kullanildigini kanitlayan testler").
"""

from __future__ import annotations

import uuid

import pytest

from app.engine.fixtures import WCAG_AA_CONTRAST_THRESHOLD
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.services.page_analysis_adapter import PageAnalysisFeatureError, adapt_page_analysis

pytestmark = pytest.mark.unit


def _features(**overrides) -> dict:
    base = {
        "final_url": "https://example.com/",
        "redirect_count": 0,
        "title": "Ornek Sayfa",
        "headings": [{"level": 1, "text": "Ornek Sayfa", "order": 0}],
        "text_stats": {
            "word_count": 400,
            "avg_sentence_word_count": 12.0,
            "visible_text_char_count": 2000,
            "heading_count": 3,
        },
        "controls": {"link_count": 4, "button_count": 2, "form_count": 1, "form_field_count": 3},
        "element_boxes": [
            {"role": "button", "x": 100.0, "y": 200.0, "width": 120.0, "height": 40.0},
            {"role": "link", "x": 10.0, "y": 900.0, "width": 60.0, "height": 20.0},
            {"role": "heading", "x": 0.0, "y": 0.0, "width": 300.0, "height": 50.0},
        ],
        "layout_regions": {
            "ust_navigasyon": None,
            "hero_baslik": None,
            "birincil_cta": {"x_pct": 40.0, "y_pct": 20.0, "width_pct": 20.0, "height_pct": 6.0},
            "govde_metni": None,
            "alt_bilgi": None,
        },
        "performance": {
            "dom_content_loaded_ms": 100.0,
            "load_event_ms": 150.0,
            "first_contentful_paint_ms": 80.0,
            "total_navigation_ms": 150.0,
        },
        "contrast_candidates": [
            {"selector": "p", "foreground": "#000", "background": "#fff", "ratio": 21.0, "meets_aa": True},
            {"selector": "a", "foreground": "#777", "background": "#fff", "ratio": 3.5, "meets_aa": False},
        ],
        "accessibility_precheck": {
            "disclaimer": "on kontrol",
            "violations": [],
            "passes_count": 3,
            "incomplete_count": 0,
        },
        "warnings": [],
    }
    base.update(overrides)
    return base


def _analysis(
    *, features: dict | None, url: str = "https://example.com/", organization_id=None
) -> PageAnalysis:
    return PageAnalysis(
        id=uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        source_kind=PageAnalysisSourceKind.URL,
        url=url,
        authorization_confirmed=True,
        status=PageAnalysisStatus.SUCCEEDED,
        analyzer_version="analyzer-2026.1",
        snapshot_version="page-feature-snapshot-live-2026.2",
        source="analyzer_live",
        features=features,
        content_sha256="a" * 64,
        image_width=1366,
        image_height=900,
    )


def test_adapt_derives_real_scalars_from_features():
    analysis = _analysis(features=_features())
    result = adapt_page_analysis(analysis, role="primary")

    assert result.snapshot.primary_cta_count == 2  # button + link, heading haric
    assert result.snapshot.form_field_count == 3
    assert result.snapshot.above_fold_cta is True  # birincil_cta layout region mevcut
    assert result.snapshot.page_word_count == 400
    assert result.snapshot.heading_count == 3
    assert result.snapshot.avg_contrast_ratio == pytest.approx((21.0 + 3.5) / 2, rel=1e-3)
    assert result.snapshot.min_contrast_ratio == pytest.approx(3.5)
    assert result.snapshot.input_hash == analysis.content_sha256
    assert result.snapshot.fixture_version.startswith("dom-adapter-1:")

    assert len(result.cta_evidence) == 2
    assert {e["role"] for e in result.cta_evidence} == {"button", "link"}
    assert all(e["classification"] == "dom_interactive_candidate" for e in result.cta_evidence)
    assert all(e["evidence"] == "dom_element_role" for e in result.cta_evidence)

    assert result.attention_region_candidate is not None
    assert result.attention_region_candidate["classification"] == "cta_candidate"
    assert result.attention_region_candidate["evidence"] == "heuristic_layout_region"

    assert result.provenance["feature_source"] == "dom"
    assert result.provenance["page_analysis_id"] == str(analysis.id)
    assert result.provenance["capture_content_sha256"] == analysis.content_sha256


def test_nav_depth_and_mobile_friendly_are_unmeasured_neutral_assumptions():
    analysis = _analysis(features=_features())
    result = adapt_page_analysis(analysis, role="primary")

    # nav_depth=0 ve mobile_friendly=True, baseline.py formullerine SIFIR
    # katki saglayan notr degerlerdir (bkz. modul dokstring'i) - gercekten
    # olculmediler, bu yuzden provenance acikca "assumed" isaretler.
    assert result.snapshot.nav_depth == 0
    assert result.snapshot.mobile_friendly is True
    assert result.provenance["unmeasured_fields"] == {
        "nav_depth": "assumed",
        "mobile_friendly": "assumed",
    }


def test_same_url_different_features_produce_different_output():
    """Ayni URL, iki farkli gercek DOM feature seti -> farkli CTA/skaler cikti."""

    analysis_a = _analysis(features=_features())
    analysis_b = _analysis(
        features=_features(
            controls={"link_count": 0, "button_count": 0, "form_count": 0, "form_field_count": 0},
            element_boxes=[{"role": "heading", "x": 0.0, "y": 0.0, "width": 300.0, "height": 50.0}],
            layout_regions={
                "ust_navigasyon": None,
                "hero_baslik": None,
                "birincil_cta": None,
                "govde_metni": None,
                "alt_bilgi": None,
            },
        ),
        url=analysis_a.url,
    )

    result_a = adapt_page_analysis(analysis_a, role="primary")
    result_b = adapt_page_analysis(analysis_b, role="primary")

    assert result_a.snapshot.primary_cta_count != result_b.snapshot.primary_cta_count
    assert result_a.snapshot.above_fold_cta != result_b.snapshot.above_fold_cta


def test_different_url_same_features_produce_same_scalar_output():
    """Farkli URL, ayni gercek DOM feature seti -> URL hash'inden bagimsiz ayni skalerler."""

    features = _features()
    analysis_a = _analysis(features=features, url="https://example.com/a")
    analysis_b = _analysis(features=features, url="https://example.com/b", organization_id=uuid.uuid4())
    # content_sha256 de kasitli olarak ayni tutuluyor - "ayni gercek icerik"
    # senaryosu simule ediliyor (farkli URL, ayni sayfa gorunumu).
    analysis_b.content_sha256 = analysis_a.content_sha256

    result_a = adapt_page_analysis(analysis_a, role="primary")
    result_b = adapt_page_analysis(analysis_b, role="primary")

    assert result_a.snapshot.primary_cta_count == result_b.snapshot.primary_cta_count
    assert result_a.snapshot.form_field_count == result_b.snapshot.form_field_count
    assert result_a.snapshot.above_fold_cta == result_b.snapshot.above_fold_cta
    assert result_a.snapshot.avg_contrast_ratio == result_b.snapshot.avg_contrast_ratio
    assert result_a.snapshot.input_hash == result_b.snapshot.input_hash


def test_missing_features_raises():
    analysis = _analysis(features=None)
    with pytest.raises(PageAnalysisFeatureError):
        adapt_page_analysis(analysis, role="primary")


def test_missing_content_sha256_raises():
    analysis = _analysis(features=_features())
    analysis.content_sha256 = None
    with pytest.raises(PageAnalysisFeatureError):
        adapt_page_analysis(analysis, role="primary")


@pytest.mark.parametrize(
    "bad_features",
    [
        {
            **_features(),
            "text_stats": {"word_count": "not-a-number", "avg_sentence_word_count": 1, "heading_count": 1},
        },
        {**_features(), "controls": "not-a-dict"},
        {
            **_features(),
            "text_stats": {"word_count": float("nan"), "avg_sentence_word_count": 1, "heading_count": 1},
        },
    ],
)
def test_invalid_schema_raises(bad_features):
    analysis = _analysis(features=bad_features)
    with pytest.raises(PageAnalysisFeatureError):
        adapt_page_analysis(analysis, role="primary")


def test_empty_contrast_candidates_uses_neutral_threshold():
    analysis = _analysis(features=_features(contrast_candidates=[]))
    result = adapt_page_analysis(analysis, role="primary")
    assert result.snapshot.min_contrast_ratio == WCAG_AA_CONTRAST_THRESHOLD
    assert result.snapshot.avg_contrast_ratio == WCAG_AA_CONTRAST_THRESHOLD


def test_out_of_range_layout_region_is_dropped_not_fabricated():
    analysis = _analysis(
        features=_features(
            layout_regions={
                "ust_navigasyon": None,
                "hero_baslik": None,
                "birincil_cta": {"x_pct": 500.0, "y_pct": 20.0, "width_pct": 20.0, "height_pct": 6.0},
                "govde_metni": None,
                "alt_bilgi": None,
            }
        )
    )
    result = adapt_page_analysis(analysis, role="primary")
    assert result.snapshot.above_fold_cta is False
    assert result.attention_region_candidate is None


def test_negative_element_box_is_dropped_not_fabricated():
    analysis = _analysis(
        features=_features(
            element_boxes=[
                {"role": "button", "x": -5.0, "y": 10.0, "width": 20.0, "height": 20.0},
                {"role": "link", "x": 10.0, "y": 10.0, "width": 20.0, "height": 20.0},
            ]
        )
    )
    result = adapt_page_analysis(analysis, role="primary")
    assert result.snapshot.primary_cta_count == 1
    assert len(result.cta_evidence) == 1
    assert result.cta_evidence[0]["role"] == "link"
