"""Surumlu, deterministik "page_feature_snapshot" fixture ureticisi.

Bu asamada Playwright (ya da baska bir gercek tarayici/crawler) yoktur;
hicbir URL gercekten ziyaret edilmez. Bunun yerine, verilen `url` + `role`
(ornegin "existing"/"new"/"primary") girdisinden **deterministik** (sha256
tabanli) bir sentetik sayfa ozellik kumesi turetilir. Bu, gercek sayfa
analizinin yerini TUTMAZ; yalnizca heuristic motorun girdi ihtiyacini
(sabit sema, surumlu) karsilayan bir yer tutucudur - bkz. docs/methodology.md
"Girdi kaynagi" bolumu.

Ayni (url, role) her zaman ayni snapshot'i uretir (kalici depolama
gerekmez); `PAGE_FEATURE_SNAPSHOT_VERSION` semasi degistiginde surum
artirilir ve eski calistirmalarin `fixture_version` alaniyla karsilastirma
yapilabilir.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

PAGE_FEATURE_SNAPSHOT_VERSION = "page-feature-snapshot-2026.1"

# WCAG AA (normal metin) minimum kontrast orani.
WCAG_AA_CONTRAST_THRESHOLD = 4.5


@dataclass(frozen=True)
class PageFeatureSnapshot:
    fixture_version: str
    url: str
    role: str
    # --- Yapisal ozellikler ---
    nav_depth: int  # ana gorevi tamamlamak icin gereken tahmini tiklama/adim sayisi
    primary_cta_count: int  # ust katmanda gorunen birincil eylem cagrisi sayisi
    form_field_count: int  # gorev yolundaki form alani sayisi
    above_fold_cta: bool  # birincil CTA ilk ekranda mi
    # --- Metin/okunabilirlik ozellikleri ---
    page_word_count: int
    avg_sentence_word_count: float
    heading_count: int
    # --- Kontrast ---
    min_contrast_ratio: float
    avg_contrast_ratio: float
    # --- Cihaz uyumu ---
    mobile_friendly: bool
    input_hash: str


def _seeded_unit_values(digest: bytes, count: int) -> list[float]:
    """Sha256 digest'inden [0, 1) araliginda `count` adet deterministik deger uretir."""

    values: list[float] = []
    for i in range(count):
        chunk = hashlib.sha256(digest + i.to_bytes(2, "big")).digest()
        values.append(int.from_bytes(chunk[:8], "big") / 2**64)
    return values


def get_page_feature_snapshot(url: str, role: str) -> PageFeatureSnapshot:
    """Verilen url+role icin deterministik sentetik sayfa ozellik kumesi dondurur."""

    normalized = f"{PAGE_FEATURE_SNAPSHOT_VERSION}:{role}:{url.strip().lower()}"
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    u = _seeded_unit_values(digest, 9)

    nav_depth = 2 + round(u[0] * 5)  # 2..7 adim
    primary_cta_count = 1 + round(u[1] * 3)  # 1..4
    form_field_count = round(u[2] * 8)  # 0..8
    above_fold_cta = u[3] > 0.35
    page_word_count = 120 + round(u[4] * 1800)  # 120..1920
    avg_sentence_word_count = 8 + u[5] * 14  # 8..22
    heading_count = 1 + round(u[6] * 9)  # 1..10
    # Kontrast oranlari 1.5..12.5 arasinda; min <= avg olacak sekilde tutulur.
    avg_contrast_ratio = round(1.5 + u[7] * 11.0, 2)
    min_contrast_ratio = round(max(1.0, avg_contrast_ratio - u[8] * 4.0), 2)
    mobile_friendly = u[0] > 0.25

    return PageFeatureSnapshot(
        fixture_version=PAGE_FEATURE_SNAPSHOT_VERSION,
        url=url,
        role=role,
        nav_depth=nav_depth,
        primary_cta_count=primary_cta_count,
        form_field_count=form_field_count,
        above_fold_cta=above_fold_cta,
        page_word_count=page_word_count,
        avg_sentence_word_count=round(avg_sentence_word_count, 2),
        heading_count=heading_count,
        min_contrast_ratio=min_contrast_ratio,
        avg_contrast_ratio=avg_contrast_ratio,
        mobile_friendly=mobile_friendly,
        input_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "PAGE_FEATURE_SNAPSHOT_VERSION",
    "WCAG_AA_CONTRAST_THRESHOLD",
    "PageFeatureSnapshot",
    "get_page_feature_snapshot",
]
