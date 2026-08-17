"""Analyzer istek/yanit semalari.

`PageFeatureSnapshotV1`, surumlu ("versioned") bir sozlesmedir: alanlari
degistiginde `SNAPSHOT_VERSION` artirilmalidir. Bu, ileride Prompt 7
motorunun `backend/app/engine/fixtures.py` icindeki sentetik yer tutucu
yerine bu (gercek) anlik goruntuyu tuketebilmesini saglayacak sabit bir
sema saglar; bu asamada motor entegrasyonu yapilmaz, yalnizca sema/uretim
hazirlanir.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ANALYZER_VERSION = "analyzer-2026.1"
SNAPSHOT_VERSION = "page-feature-snapshot-live-2026.3"
SNAPSHOT_SOURCE = "analyzer_live"

ACCESSIBILITY_PRECHECK_DISCLAIMER = (
    "Bu, axe-core ile calistirilan otomatik bir ON KONTROLDUR; tam bir WCAG "
    "uygunluk sertifikasi veya yasal uygunluk degerlendirmesi degildir. "
    "Otomatik araclar erisilebilirlik sorunlarinin yalnizca bir kismini "
    "tespit edebilir; manuel inceleme ve gercek kullanici testi gerektirir."
)


class AnalyzeRequest(BaseModel):
    url: str
    authorization_confirmed: bool = Field(
        description="Kullanicinin bu URL'yi analiz etme yetkisine sahip oldugunu onayladigini belirtir."
    )


class HeadingInfo(BaseModel):
    level: int
    text: str
    order: int


class ElementBox(BaseModel):
    role: str
    interaction_kind: str | None = None
    label: str | None = Field(default=None, max_length=120)
    # Ikon-only kontroller icin GUVENLI, sabit bir semantik anahtar (cart/bag/
    # account/search/menu/...). Yalnizca aria-label/title/alt/class/id/data-testid
    # gibi zaten var olan GUVENLI ipuclarindan cikarilir - hassas sayfa metni
    # DEGIL. Rapor katmani bunu insan-okur bir etikete cevirir (bkz. backend
    # app.services.ai_interaction_heatmap.candidates._SEMANTIC_LABELS).
    control_semantic: str | None = Field(default=None, max_length=40)
    x: float
    y: float
    width: float
    height: float


class ContrastCandidate(BaseModel):
    selector: str
    foreground: str
    background: str
    ratio: float
    meets_aa: bool


class AccessibilityViolation(BaseModel):
    rule_id: str
    impact: str | None
    help: str
    nodes_count: int


class AccessibilityPrecheck(BaseModel):
    disclaimer: str = ACCESSIBILITY_PRECHECK_DISCLAIMER
    scan_status: Literal["completed", "skipped"] = "completed"
    violations: list[AccessibilityViolation]
    passes_count: int
    incomplete_count: int


class PerformanceTimings(BaseModel):
    dom_content_loaded_ms: float | None
    load_event_ms: float | None
    first_contentful_paint_ms: float | None
    total_navigation_ms: float | None


class TextStats(BaseModel):
    word_count: int
    avg_sentence_word_count: float
    visible_text_char_count: int
    heading_count: int


class ControlCounts(BaseModel):
    link_count: int
    button_count: int
    form_count: int
    form_field_count: int


class Screenshot(BaseModel):
    format: str = "png"
    width: int
    height: int
    base64_data: str


# --- Sinirli yerlesim bolgeleri (layout_regions) -----------------------------
#
# Sentetik dikkat tahmini isi haritasinin ekran goruntusu uzerinde
# konumlandirilabilmesi icin, sayfanin gorunur (viewport, above-the-fold)
# alanindaki bes anonim yerlesim bolgesinin (nav, hero/baslik, birincil CTA,
# govde icerigi, alt bilgi) sinirlayici kutusu (bounding box) yakalanan
# uzun sayfa goruntusunun yuzdesi olarak hesaplanir. Element METNI, form degeri veya baska bir
# kisisel/hassas veri ASLA saklanmaz - yalnizca anonim rol + geometrik alan.
# Sayfada karsilik gelen bir eleman bulunamazsa (ör. footer yoksa) o bolge
# icin `None` donulur; RASTGELE/tahmini koordinat UYDURULMAZ.


class LayoutRegionBox(BaseModel):
    x_pct: float
    y_pct: float
    width_pct: float
    height_pct: float


class LayoutRegions(BaseModel):
    ust_navigasyon: LayoutRegionBox | None = None
    hero_baslik: LayoutRegionBox | None = None
    birincil_cta: LayoutRegionBox | None = None
    govde_metni: LayoutRegionBox | None = None
    alt_bilgi: LayoutRegionBox | None = None


class PageFeatureSnapshotV1(BaseModel):
    snapshot_version: str = SNAPSHOT_VERSION
    source: str = SNAPSHOT_SOURCE
    analyzer_version: str = ANALYZER_VERSION

    url: str
    final_url: str
    redirect_count: int

    title: str
    headings: list[HeadingInfo]
    text_stats: TextStats
    controls: ControlCounts
    element_boxes: list[ElementBox]
    layout_regions: LayoutRegions = LayoutRegions()
    performance: PerformanceTimings
    contrast_candidates: list[ContrastCandidate]
    accessibility_precheck: AccessibilityPrecheck
    screenshot: Screenshot

    warnings: list[str] = []


class AnalyzeErrorResponse(BaseModel):
    error: str
    reason: str


# --- Ag ve cihaz testi (network_device_test) --------------------------------
#
# `docs/scientific-integrity.md`: bu, farkli cihaz/ag profillerinde olculen
# GERCEK teknik performans verisidir (Playwright ile calisir); gercek bir
# kullanicinin oznel deneyimini/memnuniyetini TEMSIL ETMEZ - yalnizca sayfa
# yukleme zamanlamalarini ve otomatik erisilebilirlik ihlali sayisini olcer.

NETWORK_DEVICE_DISCLAIMER = (
    "Bu, farkli cihaz/ag profillerinde (Playwright ile) olculen GERCEK teknik "
    "performans (yukleme suresi, erisilebilirlik ihlali sayisi) verisidir; "
    "gercek bir kullanicinin oznel deneyimini veya memnuniyetini temsil etmez."
)

DEVICE_NETWORK_ANALYZER_VERSION = "device-network-analyzer-2026.1"


class DeviceNetworkAnalysisRequest(BaseModel):
    url: str
    authorization_confirmed: bool = Field(
        description="Kullanicinin bu URL'yi analiz etme yetkisine sahip oldugunu onayladigini belirtir."
    )
    profile_keys: list[str] = Field(
        description="Olculecek cihaz/ag profili anahtarlari (bkz. app.browser.DEVICE_NETWORK_PROFILES)."
    )


class DeviceNetworkTimings(BaseModel):
    dom_content_loaded_ms: float | None
    load_event_ms: float | None
    total_navigation_ms: float | None


class DeviceNetworkProfileResult(BaseModel):
    profile_key: str
    device_label: str
    network_label: str
    succeeded: bool
    error: str | None = None
    timings: DeviceNetworkTimings | None = None
    accessibility_violation_count: int | None = None


class DeviceNetworkAnalysisResponse(BaseModel):
    analyzer_version: str = DEVICE_NETWORK_ANALYZER_VERSION
    url: str
    disclaimer: str = NETWORK_DEVICE_DISCLAIMER
    results: list[DeviceNetworkProfileResult]
    error_rate: float
    warnings: list[str] = []
