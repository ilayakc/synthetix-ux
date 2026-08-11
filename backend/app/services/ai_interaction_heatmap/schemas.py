"""AI etkilesim isi haritasi (ai_interaction_heatmap) veri sozlesmeleri.

Bu modul, gorev-odakli AI tiklama tahmininin (bkz. gorev talimati "AI ETKILESIM
TAHMINI") TUM yapilandirilmis tiplerini tek bir yerde tutar. Iki temel guvenlik
ilkesi buraya gomulur:

1. KOORDINAT GUVENLIGI: AI/siralayici YALNIZCA bir `candidate_id` secebilir
   (`AIHotspotSelection`); serbest x/y/w/h URETEMEZ. Haritada kullanilan
   koordinatlar backend tarafindan dogrulanmis analyzer verisinden
   (`InteractionCandidate`) alinir ve `ResolvedHotspot`e backend tarafindan
   yazilir. `AIHotspotSelection` `extra="forbid"` oldugu icin, bir provider
   yaniti yanlislikla koordinat alani icerse SEMA TARAFINDAN reddedilir.

2. SURUMLENME: `INTERACTION_HEATMAP_SCHEMA_VERSION` / `..._PROMPT_VERSION`
   fingerprint'e girer (bkz. service.compute_interaction_heatmap_fingerprint);
   sema/prompt degisirse eski cache gecersizlesir, sonuc yeniden uretilir.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Cikti semasi ve prompt/siralama mantigi surumleri - fingerprint'e girer.
INTERACTION_HEATMAP_SCHEMA_VERSION = "2026.1"
INTERACTION_HEATMAP_PROMPT_VERSION = "2026.2"

# En fazla hotspot sayisi (gorev talimati "AI CIKTI SEMASI" kurallari).
MAX_HOTSPOTS = 5

# Yontem/veri kaynagi etiketleri (UI'da gosterilir). Model gorsel girdi
# DESTEKLEMEDIGI icin (bkz. app.services.ai_pipeline.openai_provider - yalnizca
# metin gonderir) varsayilan yontem analyzer'in DOM/gorsel ozelliklerine dayali
# siralamadir; ekran goruntusunun modele gonderildigi IDDIA EDILMEZ.
METHOD_DOM_VISUAL_RANKING = "dom_visual_feature_ranking"
# Gercek OpenAI provider'i worker icinde cagrildiginda kullanilan yontem: model
# YALNIZCA gercek aday `candidate_id`'lerinden secim yapar; koordinat URETMEZ.
# Model gorsel girdiyi DESTEKLEMEDIGI icin (yalnizca metin: DOM + cikarilmis
# gorsel ozellikler gonderilir) ekran goruntusunun analiz edildigi IDDIA EDILMEZ.
METHOD_OPENAI_SELECTION = "openai_candidate_selection"
METHOD_OPENAI_VISION_SELECTION = "openai_vision_candidate_selection"
METHOD_LABELS: dict[str, str] = {
    METHOD_DOM_VISUAL_RANKING: "DOM ve görsel özellik destekli AI sıralaması",
    METHOD_OPENAI_SELECTION: "OpenAI aday seçimi (DOM ve görsel özellik destekli)",
    METHOD_OPENAI_VISION_SELECTION: "OpenAI görsel + DOM aday seçimi",
}

# Isı haritasinda her zaman gorunur kalan zorunlu aciklama (gorev talimati).
# ONEMLI: model gorsel girdiyi desteklemedigi icin "sayfa goruntusu analiz
# edildi" IDDIASI YOKTUR - yalnizca "sayfadaki gercek etkilesim alanlari" ve
# gorev/kitle baglami ile calisildigi belirtilir.
INTERACTION_HEATMAP_DISCLAIMER = (
    "Bu harita hedef görev, hedef kitle ve sayfadaki gerçek etkileşim "
    "alanları kullanılarak oluşturulan sentetik bir AI tahminidir. Gerçek "
    "kullanıcı tıklaması veya göz takibi verisi değildir."
)

# Gorevle guclu eslesen aday bulunamadiginda gosterilecek uyari (kirmizi alan
# UYDURULMAZ - gorev talimati "GUCLU ESLESME YOKSA").
NO_STRONG_MATCH_WARNING = "Görevle güçlü eşleşen bir etkileşim alanı bulunamadı."

TaskRelevance = Literal["direct", "related", "weak", "none"]
Confidence = Literal["high", "medium", "low"]


class InteractionCandidate(BaseModel):
    """Analyzer'dan cikarilmis, backend tarafindan DOGRULANMIS tek bir gercek
    etkilesim alani. Koordinatlar viewport fraksiyonudur ([0,1]); AI bunlari
    URETMEZ, yalnizca `candidate_id` ile secer."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    label: str
    interaction_kind: str
    role: str
    x: float
    y: float
    w: float
    h: float
    above_fold: bool


class AIHotspotSelection(BaseModel):
    """AI/siralayicinin dondurdugu TEK secim birimi. YALNIZCA `candidate_id`
    tasir; koordinat alani (x/y/w/h) icermesi `extra="forbid"` nedeniyle SEMA
    hatasi verir - AI koordinat dondurememelidir (gorev talimati)."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    score: float = Field(ge=0.0, le=1.0)
    confidence: Confidence
    reason: str
    task_relevance: TaskRelevance


class AIInteractionOutput(BaseModel):
    """AI'dan/siralayicidan alinan tam yapilandirilmis JSON (gorev talimati
    "AI CIKTI SEMASI"). Gercek provider yaniti da, deterministik siralayici da
    AYNI bu sozlesmeyi uretir; dogrulama tek yerden yapilir (bkz. service)."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    hotspots: list[AIHotspotSelection]
    unmatched_task_warning: str | None = None


class ResolvedHotspot(BaseModel):
    """Dogrulanmis bir secimin, aday listesinden alinan GERCEK koordinat ve
    etiketiyle birlestirilmis hali - haritada cizilen nihai birim."""

    candidate_id: str
    label: str
    interaction_kind: str
    role: str
    x: float
    y: float
    w: float
    h: float
    above_fold: bool
    score: float
    confidence: Confidence
    reason: str
    task_relevance: TaskRelevance


class InteractionHeatmapResult(BaseModel):
    """AI etkilesim isi haritasinin nihai, cache'lenebilir ciktisi."""

    schema_version: str
    prompt_version: str
    method: str
    method_label: str
    model_name: str
    provider: str
    fingerprint: str
    summary: str
    hotspots: list[ResolvedHotspot]
    unmatched_task_warning: str | None
    disclaimer: str


__all__ = [
    "INTERACTION_HEATMAP_SCHEMA_VERSION",
    "INTERACTION_HEATMAP_PROMPT_VERSION",
    "MAX_HOTSPOTS",
    "METHOD_DOM_VISUAL_RANKING",
    "METHOD_OPENAI_SELECTION",
    "METHOD_OPENAI_VISION_SELECTION",
    "METHOD_LABELS",
    "INTERACTION_HEATMAP_DISCLAIMER",
    "NO_STRONG_MATCH_WARNING",
    "TaskRelevance",
    "Confidence",
    "InteractionCandidate",
    "AIHotspotSelection",
    "AIInteractionOutput",
    "ResolvedHotspot",
    "InteractionHeatmapResult",
]
