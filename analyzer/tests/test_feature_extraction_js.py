"""Gercek (headless Chromium) tarayicida `_FEATURE_EXTRACTION_JS` enjekte
edilmis cikarim JS'ini dogrular - ozellikle ikon-only kontrollerin (sepet vb.)
`element_boxes`e girmesi ve `control_semantic` uretimi (rapor katmaninin
ikon-only sepet adayini uretebilmesi icin gereken authoritative kaynak).

Bu test GERCEK bir DOM/tarayici gerektirir; analyzer Docker imaji (Playwright +
Chromium taban imaji) icinde calisir.
"""

from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from app.browser import _FEATURE_EXTRACTION_JS

_CAPTURE_HEIGHT = 800

# Ikon-only sepet (metin/aria YOK, yalnizca class ipucu) + aria-label'li sepet
# baglantisi + nested SVG'li buton + dekoratif (semantiksiz) ikon buton +
# normal metinli buton (baseline).
_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  body { margin: 0; font-family: sans-serif; }
  header { display: flex; gap: 12px; padding: 12px; }
  .ctrl { width: 44px; height: 44px; display: inline-flex; align-items: center; justify-content: center; }
  .txt { width: 160px; height: 40px; }
  svg { width: 24px; height: 24px; }
</style></head><body>
  <header>
    <button class="cart-btn ctrl"><svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg></button>
    <a href="/sepet" class="ctrl" aria-label="Sepetim"><svg viewBox="0 0 24 24"><path d="M2 2h20v20H2z"/></svg></a>
    <button class="account ctrl" title="Hesabım"><svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/></svg></button>
    <button class="decor ctrl"><svg viewBox="0 0 24 24"><path d="M3 3h18v18H3z"/></svg></button>
    <button class="txt">Ürünleri İncele</button>
  </header>
</body></html>
"""


async def _extract() -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 1280, "height": _CAPTURE_HEIGHT})
            await page.set_content(_HTML)
            return await page.evaluate(_FEATURE_EXTRACTION_JS, _CAPTURE_HEIGHT)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_icon_only_cart_is_extracted_with_control_semantic():
    features = await _extract()
    boxes = features["element_boxes"]

    # Ikon-only (metinsiz, aria'siz) sepet butonu class ipucundan yakalanir.
    cart_semantics = [b for b in boxes if b.get("control_semantic") == "cart"]
    assert cart_semantics, "ikon-only sepet kontrolu element_boxes'a girmeli"

    # aria-label'li sepet baglantisi: label aria-label'dan tasinir + semantic 'cart'.
    aria_cart = [b for b in boxes if (b.get("label") or "").strip() == "Sepetim"]
    assert aria_cart, "aria-label 'Sepetim' candidate label'a tasinmali"
    assert aria_cart[0]["control_semantic"] == "cart"

    # title'dan hesap semantigi.
    assert any(b.get("control_semantic") == "account" for b in boxes)


@pytest.mark.asyncio
async def test_nested_svg_button_is_single_candidate_and_has_real_box():
    features = await _extract()
    boxes = features["element_boxes"]

    # Nested SVG/path bagimsiz aday OLMAZ: yalnizca parent buton tek kutu uretir.
    cart_boxes = [b for b in boxes if b.get("control_semantic") == "cart" and b["role"] == "button"]
    assert len(cart_boxes) == 1
    box = cart_boxes[0]
    # Gercek DOM bounding box: sonlu, pozitif boyut (sahte/uydurma koordinat yok).
    assert box["width"] > 0 and box["height"] > 0
    for key in ("x", "y", "width", "height"):
        assert isinstance(box[key], (int, float))


@pytest.mark.asyncio
async def test_decorative_icon_without_semantic_or_text_is_not_extracted():
    features = await _extract()
    boxes = features["element_boxes"]
    # Semantik ipucu ve metni OLMAYAN dekoratif ikon buton aday yapilmaz.
    assert not any((b.get("label") is None and b.get("control_semantic") is None) for b in boxes)


# Yogun navigasyonlu sayfada ikon-only sepet kontrolunun aday slice'inda
# (oncelik takviyesi sayesinde) KORUNDUGUNU dogrular - takviye olmadan dusuk
# oncelikli sepet ikonu ilk-N aday disinda kalip rapor katmanina ulasmiyordu.
_DENSE_NAV_HTML = """
<!doctype html><html><head><meta charset="utf-8"><style>
  body { margin: 0; font-family: sans-serif; }
  a.navlink { display: inline-block; width: 120px; height: 30px; }
  .cart { width: 44px; height: 44px; display: inline-flex; }
  svg { width: 24px; height: 24px; }
</style></head><body>
  <nav>
    __LINKS__
    <button class="cart"><svg viewBox="0 0 24 24"><path d="M1 1h22v22H1z"/></svg></button>
  </nav>
</body></html>
"""


async def _extract_dense() -> dict:
    links = "\n".join(
        f'<a class="navlink" href="/kategori-{i}">Kategori {i}</a>' for i in range(60)
    )
    html = _DENSE_NAV_HTML.replace("__LINKS__", links)
    async with async_playwright() as p:
        browser_obj = await p.chromium.launch()
        try:
            page = await browser_obj.new_page(viewport={"width": 1280, "height": _CAPTURE_HEIGHT})
            await page.set_content(html)
            return await page.evaluate(_FEATURE_EXTRACTION_JS, _CAPTURE_HEIGHT)
        finally:
            await browser_obj.close()


@pytest.mark.asyncio
async def test_icon_only_cart_survives_dense_nav_priority_slice():
    features = await _extract_dense()
    boxes = features["element_boxes"]
    assert any(b.get("control_semantic") == "cart" for b in boxes), (
        "yogun navigasyonda ikon-only sepet kontrolu oncelik takviyesiyle "
        "aday slice'inda korunmali"
    )
