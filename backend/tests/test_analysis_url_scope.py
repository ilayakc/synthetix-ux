import pytest

from app.services.analysis_url_scope import (
    UnsupportedAnalysisUrlError,
    rejection_reason,
    validate_analysis_url,
)


@pytest.mark.parametrize(
    "url",
    (
        "https://www.youtube.com/watch?v=abc",
        "https://subdomain.instagram.com/example",
        "https://www.google.com/maps/place/example",
        "https://www.amazon.com/product/example",
        "https://www.trendyol.com/example",
        "https://example.com/catalog.pdf?download=1",
    ),
)
def test_rejects_predictably_heavy_or_non_html_urls(url: str) -> None:
    assert rejection_reason(url) is not None
    with pytest.raises(UnsupportedAnalysisUrlError) as exc_info:
        validate_analysis_url(url, field="current_url")
    assert exc_info.value.code == "UNSUPPORTED_ANALYSIS_URL"
    assert exc_info.value.field == "current_url"


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com",
        "https://company.example/landing-page",
        "https://shop.example/products/example",
    ),
)
def test_allows_standard_html_pages(url: str) -> None:
    assert rejection_reason(url) is None
    validate_analysis_url(url, field="current_url")


def test_leaves_syntax_errors_to_existing_validation() -> None:
    assert rejection_reason("not-a-url") is None
