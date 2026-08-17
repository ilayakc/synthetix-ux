"""Playwright orkestrasyonu: SSRF-guvenli gezinme, pasif ozellik cikarma,
axe-core erisilebilirlik on kontrolu.

Guvenlik modeli (bkz. docs/security.md "SSRF tehdit modeli"):

1. Hedef URL, `app.url_safety.validate_public_url` ile **bir kez** DNS
   cozumlemesinden gecirilir; tum cozumlenen IP'ler herkese acik olmalidir.
2. Chromium, bu dogrulanan hostname->IP eslesmesine
   `--host-resolver-rules` ile **sabitlenerek** (pinned) baslatilir; boylece
   dogrulama ile gercek baglanti arasinda hostname tekrar cozumlenmez
   (klasik DNS rebinding penceresi kapatilir) - ana navigasyon hedefi icin.
3. Sayfadaki tum istekler (`page.route`) izlenir: pinlenmemis (ana
   hostname'den farkli) her yeni hostname, istek gonderilmeden once ayni
   sekilde dogrulanir (sema + DNS + engellenmis IP kontrolu); basarisiz
   olursa istek `abort()` edilir. Bu ikincil hostname'ler (ör. yönlendirme
   hedefleri, alt kaynaklar) Chromium'un kendi coz -umlemesini kullanir;
   dolayisiyla bunlar icin sabitleme yapilamaz ve cok kisa bir TOCTOU
   penceresi kalir - bu, bilinen ve dokumante edilmis bir kalan risktir.
4. Redirect (yeni hostname'e navigasyon) sayisi `max_redirects` ile
   sinirlanir.
5. Yanit boyutu, `Content-Length` basligi uzerinden `max_response_bytes`
   ile sinirlanir (en iyi caba: `Content-Length` gondermeyen "chunked"
   yanitlar yalnizca navigasyon zaman asimiyla sinirlanir).
6. Form gonderme, giris yapma veya herhangi bir tiklama/etkilesim
   **yapilmaz**; yalnizca navigasyon + pasif okuma (DOM, screenshot,
   performance, axe-core) gerceklestirilir.
"""

from __future__ import annotations

import asyncio
import base64
import logging

from axe_playwright_python.async_playwright import Axe
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Route
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.config import settings
from app.schemas import (
    DeviceNetworkAnalysisResponse,
    DeviceNetworkProfileResult,
    DeviceNetworkTimings,
    PageFeatureSnapshotV1,
)
from app.url_safety import UnsafeUrlError, is_blocked_ip, resolve_host_ips, validate_public_url, validate_url_syntax

logger = logging.getLogger("analyzer.browser")

_semaphore = asyncio.Semaphore(settings.max_concurrent_analyses)
EMPTY_PAGE_SNAPSHOT_CODE = "empty_page_snapshot"
EMPTY_PAGE_SNAPSHOT_MESSAGE = (
    "Sayfa acildi ancak analiz edilebilir metin veya etkilesim alani yuklenmedi. "
    "Site otomatik tarayicilara bos icerik donduruyor olabilir."
)


class AnalysisError(Exception):
    """SSRF disi, calisma zamani analiz hatalari (timeout, yanit boyutu, motor hatasi)."""

    def __init__(self, reason: str, *, code: str = "analysis_failed"):
        self.reason = reason
        self.code = code
        super().__init__(reason)


def _is_empty_page_snapshot(features: dict) -> bool:
    """Bos headless/skeleton cevabini gercek, sade bir sayfadan ayirir.

    Ancak baslik, gorunur metin ve tum etkilesim kanitlari birlikte yoksa
    bos kabul edilir. Boylece yalnizca az icerikli veya image-only bir sayfa,
    gercek bir basligi/kontrolu varsa yanlislikla reddedilmez.
    """

    text_stats = features.get("text_stats") or {}
    controls = features.get("controls") or {}
    return (
        not str(features.get("title") or "").strip()
        and int(text_stats.get("visible_text_char_count") or 0) == 0
        and int(controls.get("link_count") or 0) == 0
        and int(controls.get("button_count") or 0) == 0
        and int(controls.get("form_field_count") or 0) == 0
        and not (features.get("element_boxes") or [])
    )


async def _extract_features_with_bounded_readiness(page, capture_height: int) -> dict:
    """Dinamik icerik icin pasif ve bounded DOM hazirlik kontrolu."""

    max_checks = max(1, min(settings.empty_snapshot_max_checks, 5))
    for check_index in range(max_checks):
        features = await page.evaluate(_FEATURE_EXTRACTION_JS, capture_height)
        if not _is_empty_page_snapshot(features):
            return features
        if check_index + 1 < max_checks:
            await page.wait_for_timeout(max(0, settings.empty_snapshot_retry_delay_ms))
    raise AnalysisError(EMPTY_PAGE_SNAPSHOT_MESSAGE, code=EMPTY_PAGE_SNAPSHOT_CODE)


async def _validate_and_cache_host(hostname: str, validated_hosts: dict[str, bool]) -> None:
    if hostname in validated_hosts:
        if not validated_hosts[hostname]:
            raise UnsafeUrlError(f"Host onceden guvensiz bulunmustu: {hostname}")
        return
    try:
        ips = await asyncio.to_thread(resolve_host_ips, hostname)
    except UnsafeUrlError:
        validated_hosts[hostname] = False
        raise
    if any(is_blocked_ip(ip) for ip in ips):
        validated_hosts[hostname] = False
        raise UnsafeUrlError(f"Host engellenmis bir IP'ye cozumleniyor: {hostname}")
    validated_hosts[hostname] = True


def _make_route_handler(pinned_hostname: str, validated_hosts: dict[str, bool], redirect_state: dict[str, int]):
    async def handler(route: Route) -> None:
        request = route.request
        url = request.url
        try:
            _, hostname = validate_url_syntax(url)
        except UnsafeUrlError as exc:
            logger.warning("istek engellendi (sema/hostname gecersiz): %s", exc.reason)
            await route.abort()
            return

        if hostname != pinned_hostname:
            if request.resource_type == "document":
                redirect_state["count"] += 1
                if redirect_state["count"] > settings.max_redirects:
                    logger.warning("redirect siniri asildi (%s): %s", settings.max_redirects, url)
                    await route.abort()
                    return
            try:
                await _validate_and_cache_host(hostname, validated_hosts)
            except UnsafeUrlError as exc:
                logger.warning("istek engellendi (SSRF): %s", exc.reason)
                await route.abort()
                return

        await route.continue_()

    return handler


_FEATURE_EXTRACTION_JS = """
(captureHeight) => {
  function isVisible(el) {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function isInsideCapture(el) {
    if (!isVisible(el)) return false;
    const rect = el.getBoundingClientRect();
    return rect.right > 0 && rect.left < window.innerWidth && rect.bottom > 0 && rect.top < captureHeight;
  }

  function accessibleName(el) {
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll('img[alt]') : [])
      .map((img) => img.getAttribute('alt') || '').join(' ');
    return [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'), el.value, imageAlt]
      .filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
  }

  function safeInteractiveLabel(el) {
    const tag = (el.tagName || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const imageAlt = Array.from(el.querySelectorAll ? el.querySelectorAll('img[alt]') : [])
      .map((img) => img.getAttribute('alt') || '').join(' ');
    const controlValue = tag === 'input' && ['submit', 'button'].includes(type) ? el.value : '';
    const parts = [el.innerText, el.getAttribute('aria-label'), el.getAttribute('title'), controlValue, imageAlt]
      .filter(Boolean).map((value) => String(value).replace(/\\s+/g, ' ').trim()).filter(Boolean);
    const unique = [];
    const seen = new Set();
    parts.forEach((part) => {
      const key = part.toLocaleLowerCase('tr-TR');
      if (!seen.has(key)) {
        seen.add(key);
        unique.push(part);
      }
    });
    return unique.join(' ').trim().slice(0, 120) || null;
  }

  function isCtaLikeLink(el) {
    if ((el.tagName || '').toLowerCase() !== 'a') return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 80 || rect.height < 30) return false;
    const style = window.getComputedStyle(el);
    const background = parseColor(style.backgroundColor);
    const hasBackground = Boolean(background && background.a > 0.08);
    const borderWidth = ['borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth']
      .reduce((sum, key) => sum + (parseFloat(style[key]) || 0), 0);
    const padding = ['paddingLeft', 'paddingRight', 'paddingTop', 'paddingBottom']
      .reduce((sum, key) => sum + (parseFloat(style[key]) || 0), 0);
    const hint = [el.className, el.id].filter((value) => typeof value === 'string').join(' ').toLowerCase();
    return hasBackground || borderWidth > 0 || padding >= 18 || /(btn|button|cta|primary|secondary)/.test(hint);
  }

  function controlSemantic(el) {
    // Ikon-only kontroller (sepet/hesap/arama/menu...) icin GUVENLI, sabit bir
    // semantik anahtar cikarir - yalnizca ZATEN var olan guvenli ipuclarindan
    // (aria-label/title/data-testid/name/id/class + nested img alt / svg title);
    // gorunur sayfa metni okunmaz. Eslesme yoksa null (uydurma yapilmaz).
    const attrs = [
      el.getAttribute('aria-label'), el.getAttribute('title'),
      el.getAttribute('data-testid'), el.getAttribute('data-test'),
      el.getAttribute('name'), el.id,
      typeof el.className === 'string' ? el.className : '',
    ];
    const nestedAlt = Array.from(el.querySelectorAll ? el.querySelectorAll('img[alt]') : [])
      .map((img) => img.getAttribute('alt') || '');
    const svgHints = Array.from(el.querySelectorAll ? el.querySelectorAll('svg title, use') : [])
      .map((n) => (n.textContent || '') + ' ' + ((n.getAttribute && (n.getAttribute('href') || n.getAttribute('xlink:href'))) || ''));
    const hint = attrs.concat(nestedAlt).concat(svgHints)
      .filter((v) => typeof v === 'string').join(' ').toLowerCase();
    if (!hint) return null;
    if (/(sepet|cart|basket)/.test(hint)) return 'cart';
    if (/(canta|shopping-bag|shoppingbag)/.test(hint)) return 'bag';
    if (/(favori|wishlist|wish-list)/.test(hint)) return 'wishlist';
    if (/(hesab|hesap|account|profil|kullanici)/.test(hint)) return 'account';
    if (/(giris|oturum|login|sign-in|signin)/.test(hint)) return 'login';
    if (/(arama|search)/.test(hint)) return 'search';
    if (/(menu|hamburger)/.test(hint)) return 'menu';
    return null;
  }

  function isMeaningfulInteractive(el) {
    // Metin/accessible name (>=2) YA DA ikon-only kontroller icin cikarilabilen
    // guvenli bir semantik varsa aday kabul edilir - boylece etiketsiz sepet/
    // hesap/arama ikonlari veri disinda kalmaz (onceki davranista kaliyordu).
    return isInsideCapture(el) && (accessibleName(el).length >= 2 || controlSemantic(el) !== null);
  }

  function interactionKind(el, role) {
    const rect = el.getBoundingClientRect();
    const tag = (el.tagName || '').toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const inForm = Boolean(el.closest('form'));
    const inNavigation = Boolean(el.closest('nav, header, [role="navigation"]'));
    const inCarousel = Boolean(el.closest('[class*="carousel"], [class*="slider"], [class*="swiper"], [class*="slick"]'));
    const hasImage = Boolean(el.querySelector && el.querySelector('img, svg'));
    const controlHint = [el.className, el.id, el.getAttribute('aria-label'), el.getAttribute('title')]
      .filter((value) => typeof value === 'string').join(' ').toLowerCase();
    const aspect = rect.height > 0 ? rect.width / rect.height : Number.POSITIVE_INFINITY;

    if (rect.width > window.innerWidth * 0.55 || aspect > 16) return 'container_link';
    if (
      rect.width <= 14 && rect.height <= 14 ||
      inCarousel ||
      /(carousel|slider|swiper|slick|pagination|next|prev|ileri|geri)/.test(controlHint)
    ) return 'pagination_control';
    if (role === 'button') {
      if (type === 'submit' || inForm) return 'form_action';
      if (inNavigation) return 'navigation_action';
      return 'button';
    }
    if (inNavigation) return 'navigation_link';
    if ((hasImage || tag === 'a' && el.children.length > 2) && rect.height >= 80) return 'image_link';
    if (isCtaLikeLink(el)) return 'cta_link';
    if (hasImage || tag === 'a' && el.children.length > 2) return 'image_link';
    return 'content_link';
  }

  function interactionPriority(el, role) {
    const rect = el.getBoundingClientRect();
    const kind = interactionKind(el, role);
    const kindWeight = {
      form_action: 10,
      button: 7,
      cta_link: 7,
      content_link: 4.5,
      image_link: 3.5,
      navigation_action: 3,
      navigation_link: 2,
      pagination_control: 0.8,
      container_link: 0.05,
    }[kind] || 1;
    const sizeBonus = Math.min(2, Math.sqrt(Math.max(1, rect.width * rect.height)) / 45);
    const centerX = rect.x + rect.width / 2;
    const centerBonus = Math.max(0, 1 - Math.abs(centerX - window.innerWidth / 2) / (window.innerWidth / 2));
    const visibleStartBonus = rect.y >= 0 && rect.y < window.innerHeight * 0.8 ? 0.5 : 0;
    return kindWeight + sizeBonus + centerBonus * 0.35 + visibleStartBonus;
  }

  function relativeLuminance(r, g, b) {
    const chan = (c) => {
      c /= 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b);
  }

  function parseColor(str) {
    const m = (str || '').match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(',').map((s) => parseFloat(s.trim()));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  }

  function effectiveBackground(el) {
    let node = el;
    while (node) {
      const style = window.getComputedStyle(node);
      const color = parseColor(style.backgroundColor);
      if (color && color.a > 0) return color;
      node = node.parentElement;
    }
    return { r: 255, g: 255, b: 255, a: 1 };
  }

  function contrastRatio(fg, bg) {
    const l1 = relativeLuminance(fg.r, fg.g, fg.b) + 0.05;
    const l2 = relativeLuminance(bg.r, bg.g, bg.b) + 0.05;
    return l1 > l2 ? l1 / l2 : l2 / l1;
  }

  const title = document.title || '';
  const headingEls = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6')).filter(isInsideCapture);
  const headings = headingEls.slice(0, 50).map((el, i) => ({
    level: parseInt(el.tagName.substring(1), 10),
    text: (el.innerText || '').trim().slice(0, 200),
    order: i,
  }));

  const bodyText = (document.body ? document.body.innerText : '') || '';
  const words = bodyText.trim().split(/\\s+/).filter(Boolean);
  const sentences = bodyText.split(/[.!?]+/).map((s) => s.trim()).filter(Boolean);
  const sentenceWordCounts = sentences.map((s) => s.split(/\\s+/).filter(Boolean).length);
  const avgSentenceWordCount = sentenceWordCounts.length
    ? sentenceWordCounts.reduce((a, b) => a + b, 0) / sentenceWordCounts.length
    : 0;

  const links = Array.from(document.querySelectorAll('a[href]')).filter(isMeaningfulInteractive);
  const buttons = Array.from(
    document.querySelectorAll('button, input[type="submit"], input[type="button"], [role="button"]')
  ).filter(isMeaningfulInteractive);
  const forms = Array.from(document.querySelectorAll('form'));
  const formFields = Array.from(
    document.querySelectorAll('form input, form select, form textarea')
  ).filter((el) => !['hidden', 'submit', 'button'].includes((el.getAttribute('type') || '').toLowerCase()) && isInsideCapture(el));

  const interactiveCandidates = [];
  const seenInteractive = new Set();
  buttons.forEach((el) => {
    if (!seenInteractive.has(el)) {
      seenInteractive.add(el);
      interactiveCandidates.push(['button', el]);
    }
  });
  links.forEach((el) => {
    if (!seenInteractive.has(el)) {
      seenInteractive.add(el);
      interactiveCandidates.push(['link', el]);
    }
  });
  interactiveCandidates.sort((a, b) => interactionPriority(b[1], b[0]) - interactionPriority(a[1], a[0]));

  const boxCandidates = [];
  headingEls.slice(0, 5).forEach((el) => boxCandidates.push(['heading', el]));
  // Hedef gorev analyzer tarafindan bilinmez. Yalnizca ilk 12 adayi saklamak,
  // uzun e-ticaret sayfalarinda gorevle ilgili hesap/kayit CTA'sini veri
  // disinda birakabiliyordu. Hala bounded, fakat rapor katmaninin hedef gorev
  // metniyle eslestirebilmesi icin daha genis bir aday havuzu korunur.
  interactiveCandidates.slice(0, 40).forEach(([role, el]) => boxCandidates.push([role, el]));
  forms.filter(isInsideCapture).slice(0, 3).forEach((el) => boxCandidates.push(['form', el]));

  const elementBoxes = boxCandidates.slice(0, 50).map(([role, el]) => {
    const rect = el.getBoundingClientRect();
    return {
      role,
      interaction_kind: role === 'button' || role === 'link' ? interactionKind(el, role) : null,
      label: role === 'button' || role === 'link' ? safeInteractiveLabel(el) : null,
      control_semantic: role === 'button' || role === 'link' ? controlSemantic(el) : null,
      x: rect.x,
      y: rect.y,
      width: rect.width,
      height: rect.height,
    };
  });

  // Sentetik dikkat isi haritasi icin sinirli (bounded) yerlesim bolgeleri:
  // yalnizca gorunur (viewport) alanla kesisen, GERCEK bir DOM elemanina
  // karsilik gelen bolgeler viewport yuzdesi olarak donulur; element metni
  // ASLA okunmaz/saklanmaz. Eslesen bir eleman yoksa (ör. <footer> yok) o
  // bolge icin null donulur - rastgele/tahmini koordinat uretilmez.
  function boundedBoxPercent(el) {
    if (!el || !isInsideCapture(el)) return null;
    const rect = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = captureHeight;
    const x = Math.max(0, rect.x);
    const y = Math.max(0, rect.y);
    const width = Math.min(rect.x + rect.width, vw) - x;
    const height = Math.min(rect.y + rect.height, vh) - y;
    if (width <= 0 || height <= 0) return null;
    return {
      x_pct: Math.round((x / vw) * 10000) / 100,
      y_pct: Math.round((y / vh) * 10000) / 100,
      width_pct: Math.round((width / vw) * 10000) / 100,
      height_pct: Math.round((height / vh) * 10000) / 100,
    };
  }

  const navEl = document.querySelector('nav, header');
  const heroEl = headingEls[0] || null;
  const ctaEl = buttons[0] || null;
  const contentEl = document.querySelector('main, article');
  const footerEl = document.querySelector('footer');

  const layoutRegions = {
    ust_navigasyon: boundedBoxPercent(navEl),
    hero_baslik: boundedBoxPercent(heroEl),
    birincil_cta: boundedBoxPercent(ctaEl),
    govde_metni: boundedBoxPercent(contentEl),
    alt_bilgi: boundedBoxPercent(footerEl),
  };

  const textCandidates = Array.from(document.querySelectorAll('body *')).filter((el) => {
    if (!isInsideCapture(el)) return false;
    const ownText = Array.from(el.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent.trim())
      .join('');
    return ownText.length >= 3;
  });

  const sampleStep = Math.max(1, Math.floor(textCandidates.length / 15));
  const contrastCandidates = [];
  for (let i = 0; i < textCandidates.length && contrastCandidates.length < 15; i += sampleStep) {
    const el = textCandidates[i];
    const style = window.getComputedStyle(el);
    const fg = parseColor(style.color);
    if (!fg) continue;
    const bg = effectiveBackground(el);
    const ratio = contrastRatio(fg, bg);
    const classPart = el.className && typeof el.className === 'string'
      ? '.' + el.className.split(' ').filter(Boolean).slice(0, 2).join('.')
      : '';
    contrastCandidates.push({
      selector: (el.tagName.toLowerCase() + classPart).slice(0, 100),
      foreground: style.color,
      background: `rgb(${Math.round(bg.r)}, ${Math.round(bg.g)}, ${Math.round(bg.b)})`,
      ratio: Math.round(ratio * 100) / 100,
      meets_aa: ratio >= 4.5,
    });
  }

  const timing = performance.timing;
  const navStart = timing.navigationStart;
  const domContentLoadedMs = timing.domContentLoadedEventEnd > 0 ? timing.domContentLoadedEventEnd - navStart : null;
  const loadEventMs = timing.loadEventEnd > 0 ? timing.loadEventEnd - navStart : null;
  const paintEntries = performance.getEntriesByType ? performance.getEntriesByType('paint') : [];
  const fcpEntry = paintEntries.find((e) => e.name === 'first-contentful-paint');

  return {
    title,
    headings,
    text_stats: {
      word_count: words.length,
      avg_sentence_word_count: Math.round(avgSentenceWordCount * 100) / 100,
      visible_text_char_count: bodyText.length,
      heading_count: headingEls.length,
    },
    controls: {
      link_count: links.length,
      button_count: buttons.length,
      form_count: forms.length,
      form_field_count: formFields.length,
    },
    element_boxes: elementBoxes,
    layout_regions: layoutRegions,
    performance: {
      dom_content_loaded_ms: domContentLoadedMs,
      load_event_ms: loadEventMs,
      first_contentful_paint_ms: fcpEntry ? Math.round(fcpEntry.startTime * 100) / 100 : null,
      total_navigation_ms: loadEventMs,
    },
    contrast_candidates: contrastCandidates,
  };
}
"""


def _build_snapshot(
    *,
    url: str,
    final_url: str,
    redirect_count: int,
    features: dict,
    screenshot_bytes: bytes,
    axe_response: dict,
    warnings: list[str],
    screenshot_width: int,
    screenshot_height: int,
) -> PageFeatureSnapshotV1:
    accessibility = {
        "scan_status": axe_response.get("scan_status", "completed"),
        "violations": [
            {
                "rule_id": v["id"],
                "impact": v.get("impact"),
                "help": v.get("help", ""),
                "nodes_count": len(v.get("nodes", [])),
            }
            for v in axe_response.get("violations", [])
        ],
        "passes_count": len(axe_response.get("passes", [])),
        "incomplete_count": len(axe_response.get("incomplete", [])),
    }

    payload = {
        "url": url,
        "final_url": final_url,
        "redirect_count": redirect_count,
        "title": features["title"],
        "headings": features["headings"],
        "text_stats": features["text_stats"],
        "controls": features["controls"],
        "element_boxes": features["element_boxes"],
        "layout_regions": features["layout_regions"],
        "performance": features["performance"],
        "contrast_candidates": features["contrast_candidates"],
        "accessibility_precheck": accessibility,
        "screenshot": {
            "width": screenshot_width,
            "height": screenshot_height,
            "base64_data": base64.b64encode(screenshot_bytes).decode("ascii"),
        },
        "warnings": warnings,
    }
    return PageFeatureSnapshotV1.model_validate(payload)


async def _run_accessibility_precheck(page, warnings: list[str]) -> dict:
    """axe-core'u sinirli ve best-effort calistirir.

    Erisilebilirlik on kontrolu, ozellikle buyuk e-ticaret DOM'larinda CPU
    sinirli ortamlarda uzun surebilir. Tarama tek basina tum URL snapshot'ini
    dusurmez; eksik sonuc `scan_status=skipped` ve uyariyla acikca belirtilir.
    """

    axe = Axe()
    try:
        axe_results = await asyncio.wait_for(
            axe.run(page, options={"resultTypes": ["violations", "passes", "incomplete"]}),
            timeout=settings.accessibility_scan_timeout_seconds,
        )
    except (TimeoutError, PlaywrightError) as exc:
        warnings.append(
            "Erisilebilirlik on kontrolu sure/kaynak sinirina ulasti; "
            "temel sayfa analizi mevcut verilerle tamamlandi"
        )
        logger.warning("axe-core on kontrolu atlandi: %s", exc)
        return {
            "scan_status": "skipped",
            "violations": [],
            "passes": [],
            "incomplete": [],
        }
    except Exception as exc:  # axe wrapper/runtime hatasi temel snapshot'i dusurmemeli
        warnings.append(
            "Erisilebilirlik on kontrolu tamamlanamadi; "
            "temel sayfa analizi mevcut verilerle tamamlandi"
        )
        logger.exception("axe-core on kontrolu beklenmeyen hatayla atlandi: %s", exc)
        return {
            "scan_status": "skipped",
            "violations": [],
            "passes": [],
            "incomplete": [],
        }

    return {"scan_status": "completed", **axe_results.response}


async def _run_analysis(browser, validated) -> PageFeatureSnapshotV1:
    context = await browser.new_context(
        viewport={"width": settings.viewport_width, "height": settings.viewport_height}
    )
    page = await context.new_page()
    page.set_default_navigation_timeout(settings.navigation_timeout_seconds * 1000)
    page.set_default_timeout(settings.navigation_timeout_seconds * 1000)

    validated_hosts: dict[str, bool] = {validated.hostname: True}
    redirect_state = {"count": 0}
    oversized = {"flag": False}
    warnings: list[str] = []

    async def _on_response(response) -> None:
        try:
            length = response.headers.get("content-length")
            if length and int(length) > settings.max_response_bytes:
                oversized["flag"] = True
                await context.close()
        except Exception:  # yanit basligi ayristirma hatalari analiz basarisizligina donusmemeli
            logger.debug("content-length kontrolu basarisiz oldu", exc_info=True)

    page.on("response", _on_response)
    await page.route("**/*", _make_route_handler(validated.hostname, validated_hosts, redirect_state))

    try:
        await page.goto(validated.url, wait_until="load")
    except PlaywrightTimeoutError as exc:
        raise AnalysisError(f"Sayfa zaman asimina ugradi ({settings.navigation_timeout_seconds}s)") from exc
    except PlaywrightError as exc:
        if oversized["flag"]:
            raise AnalysisError(f"Yanit boyutu sinirini asti (>{settings.max_response_bytes} bayt)") from exc
        raise AnalysisError(f"Sayfa yuklenemedi: {exc}") from exc

    if oversized["flag"]:
        raise AnalysisError(f"Yanit boyutu sinirini asti (>{settings.max_response_bytes} bayt)")

    final_url = page.url
    try:
        _, final_hostname = validate_url_syntax(final_url)
    except UnsafeUrlError as exc:
        raise AnalysisError(f"Nihai URL guvensiz: {exc.reason}") from exc
    if not validated_hosts.get(final_hostname):
        raise AnalysisError("Nihai URL guvenlik dogrulamasindan gecemedi")

    if redirect_state["count"] > 0:
        warnings.append(f"{redirect_state['count']} yonlendirme takip edildi")

    # SPA ve tembel yuklenen bolumlerin yerlesmesi icin sinirli bir bekleme
    # ve pasif kaydirma yapilir; tiklama/form gonderme yapilmaz.
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except PlaywrightTimeoutError:
        warnings.append("Ag istekleri 5 saniyede sakinlesmedi; mevcut icerikle devam edildi")

    await page.evaluate(
        """async ({step, limit, settle}) => {
          const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
          let previousHeight = 0;
          for (let y = 0; y < limit; y += step) {
            window.scrollTo(0, y);
            await sleep(settle);
            const height = Math.max(document.body?.scrollHeight || 0, document.documentElement.scrollHeight || 0);
            if (height === previousHeight && y + step >= height) break;
            previousHeight = height;
          }
          window.scrollTo(0, 0);
          await sleep(settle);
        }""",
        {
            "step": settings.viewport_height,
            "limit": settings.screenshot_max_height,
            "settle": settings.dynamic_content_settle_ms,
        },
    )
    document_height = await page.evaluate(
        "Math.max(document.body?.scrollHeight || 0, document.documentElement.scrollHeight || 0)"
    )
    capture_height = min(
        max(settings.viewport_height, int(document_height)), settings.screenshot_max_height
    )
    if int(document_height) > capture_height:
        warnings.append(
            f"Sayfa {int(document_height)} px; ekran goruntusu guvenlik siniri nedeniyle {capture_height} px ile sinirlandi"
        )

    try:
        features = await _extract_features_with_bounded_readiness(page, capture_height)
    except AnalysisError:
        await context.close()
        raise
    screenshot_bytes = await page.screenshot(
        type="png",
        animations="disabled",
        clip={"x": 0, "y": 0, "width": settings.viewport_width, "height": capture_height},
    )
    axe_response = await _run_accessibility_precheck(page, warnings)

    await context.close()

    return _build_snapshot(
        url=validated.url,
        final_url=final_url,
        redirect_count=redirect_state["count"],
        features=features,
        screenshot_bytes=screenshot_bytes,
        axe_response=axe_response,
        warnings=warnings,
        screenshot_width=settings.viewport_width,
        screenshot_height=capture_height,
    )


# --- Ag ve cihaz testi (network_device_test) --------------------------------
#
# Sabit dort profil: her biri Playwright context secenekleri (viewport, user
# agent, mobil/dokunma) + CDP `Network.emulateNetworkConditions` parametreleri
# (bayt/sn, ms) tasir. Degerler Chrome DevTools "Fast 4G"/"Slow 3G"
# presetlerine yakin secilmistir (yaklasik/onaylanmis degildir - bkz.
# docs/methodology.md).

DEVICE_NETWORK_PROFILES: dict[str, dict] = {
    "desktop_broadband": {
        "device_label": "Masaustu",
        "network_label": "Genis bant (throttling yok)",
        "context_options": {
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        },
        "network": None,
    },
    "mobile_4g": {
        "device_label": "Mobil",
        "network_label": "4G (~4 Mbps indirme, 20ms gecikme)",
        "context_options": {
            "viewport": {"width": 390, "height": 844},
            "device_scale_factor": 3,
            "is_mobile": True,
            "has_touch": True,
            "user_agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
            ),
        },
        "network": {"download_bytes_per_sec": 4 * 1024 * 1024 // 8, "upload_bytes_per_sec": 3 * 1024 * 1024 // 8, "latency_ms": 20},
    },
    "mobile_slow_3g": {
        "device_label": "Mobil",
        "network_label": "Yavas 3G (~400 Kbps, 400ms gecikme)",
        "context_options": {
            "viewport": {"width": 390, "height": 844},
            "device_scale_factor": 2,
            "is_mobile": True,
            "has_touch": True,
            "user_agent": (
                "Mozilla/5.0 (Linux; Android 10; SM-G960F) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
        },
        "network": {"download_bytes_per_sec": 400 * 1024 // 8, "upload_bytes_per_sec": 400 * 1024 // 8, "latency_ms": 400},
    },
    "tablet_wifi": {
        "device_label": "Tablet",
        "network_label": "Wi-Fi (throttling yok)",
        "context_options": {
            "viewport": {"width": 810, "height": 1080},
            "device_scale_factor": 2,
            "is_mobile": True,
            "has_touch": True,
        },
        "network": None,
    },
    "desktop_constrained_wifi": {
        "device_label": "Masaustu",
        "network_label": "Kisitli Wi-Fi/DSL (~1.5 Mbps indirme, 100ms gecikme)",
        "context_options": {
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        },
        "network": {"download_bytes_per_sec": 1536 * 1024 // 8, "upload_bytes_per_sec": 768 * 1024 // 8, "latency_ms": 100},
    },
}

_TIMING_EXTRACTION_JS = """
() => {
  const timing = performance.timing;
  const navStart = timing.navigationStart;
  const domContentLoadedMs = timing.domContentLoadedEventEnd > 0 ? timing.domContentLoadedEventEnd - navStart : null;
  const loadEventMs = timing.loadEventEnd > 0 ? timing.loadEventEnd - navStart : null;
  return {
    dom_content_loaded_ms: domContentLoadedMs,
    load_event_ms: loadEventMs,
    total_navigation_ms: loadEventMs,
  };
}
"""


async def _measure_device_network_profile(
    browser, validated, profile_key: str, profile: dict
) -> DeviceNetworkProfileResult:
    context = None
    try:
        context = await browser.new_context(**profile["context_options"])
        page = await context.new_page()
        page.set_default_navigation_timeout(settings.navigation_timeout_seconds * 1000)
        page.set_default_timeout(settings.navigation_timeout_seconds * 1000)

        validated_hosts: dict[str, bool] = {validated.hostname: True}
        redirect_state = {"count": 0}
        await page.route("**/*", _make_route_handler(validated.hostname, validated_hosts, redirect_state))

        network = profile.get("network")
        if network is not None:
            cdp = await context.new_cdp_session(page)
            await cdp.send("Network.enable")
            await cdp.send(
                "Network.emulateNetworkConditions",
                {
                    "offline": False,
                    "latency": network["latency_ms"],
                    "downloadThroughput": network["download_bytes_per_sec"],
                    "uploadThroughput": network["upload_bytes_per_sec"],
                },
            )

        await page.goto(validated.url, wait_until="load")

        timings_raw = await page.evaluate(_TIMING_EXTRACTION_JS)
        timings = DeviceNetworkTimings.model_validate(timings_raw)

        axe = Axe()
        axe_results = await axe.run(page, options={"resultTypes": ["violations"]})
        violation_count = len(axe_results.response.get("violations", []))

        return DeviceNetworkProfileResult(
            profile_key=profile_key,
            device_label=profile["device_label"],
            network_label=profile["network_label"],
            succeeded=True,
            timings=timings,
            accessibility_violation_count=violation_count,
        )
    except PlaywrightTimeoutError:
        return DeviceNetworkProfileResult(
            profile_key=profile_key,
            device_label=profile["device_label"],
            network_label=profile["network_label"],
            succeeded=False,
            error=f"Sayfa zaman asimina ugradi ({settings.navigation_timeout_seconds}s)",
        )
    except (PlaywrightError, UnsafeUrlError) as exc:
        return DeviceNetworkProfileResult(
            profile_key=profile_key,
            device_label=profile["device_label"],
            network_label=profile["network_label"],
            succeeded=False,
            error=str(exc),
        )
    finally:
        if context is not None:
            await context.close()


async def analyze_device_network(url: str, profile_keys: list[str]) -> DeviceNetworkAnalysisResponse:
    """Verilen URL'yi sabit cihaz/ag profillerinde gercekten olcer (Playwright + CDP throttling).

    `analyze_url` ile ayni SSRF modelini kullanir (bir kez DNS dogrulamasi +
    host-resolver-rules pinleme); tek bir profilin basarisiz olmasi (timeout
    vb.) digerlerinin islenmesini engellemez - o profil `succeeded=False` ile
    kaydedilir (bkz. `error_rate`).
    """

    unknown = [key for key in profile_keys if key not in DEVICE_NETWORK_PROFILES]
    if unknown:
        raise AnalysisError(f"Bilinmeyen cihaz/ag profili: {', '.join(unknown)}")

    validated = validate_public_url(url)
    pinned_ip = validated.resolved_ips[0]
    host_rule = f"MAP {validated.hostname} {pinned_ip}"

    warnings: list[str] = []
    results: list[DeviceNetworkProfileResult] = []

    async with _semaphore:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    f"--host-resolver-rules={host_rule}",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                for profile_key in profile_keys:
                    profile = DEVICE_NETWORK_PROFILES[profile_key]
                    result = await _measure_device_network_profile(browser, validated, profile_key, profile)
                    if not result.succeeded:
                        warnings.append(f"Profil basarisiz ({profile_key}): {result.error}")
                    results.append(result)
            finally:
                await browser.close()

    failed_count = sum(1 for r in results if not r.succeeded)
    error_rate = round(failed_count / len(results), 4) if results else 0.0

    return DeviceNetworkAnalysisResponse(
        url=validated.url,
        results=results,
        error_rate=error_rate,
        warnings=warnings,
    )


async def analyze_url(url: str) -> PageFeatureSnapshotV1:
    """Verilen URL'yi guvenlik dogrulamasindan gecirir ve pasif olarak analiz eder.

    `UnsafeUrlError` (SSRF/sema reddi) ve `AnalysisError` (timeout, boyut,
    motor hatasi) ayri turler olarak firlatilir; cagiran taraf (bkz.
    `app.main`) bunlari farkli HTTP durum kodlarina esler.
    """

    validated = validate_public_url(url)
    pinned_ip = validated.resolved_ips[0]
    host_rule = f"MAP {validated.hostname} {pinned_ip}"

    async with _semaphore:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    f"--host-resolver-rules={host_rule}",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            try:
                return await _run_analysis(browser, validated)
            finally:
                await browser.close()


__all__ = ["AnalysisError", "analyze_url"]
