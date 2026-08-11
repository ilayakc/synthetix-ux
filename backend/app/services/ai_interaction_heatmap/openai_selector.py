"""AI etkilesim isi haritasi icin GERCEK OpenAI secici (selector) adaptoru.

Bu modul, mevcut OpenAI provider ALTYAPISINI (SDK, hata haritalamasi, maliyet
tahmini, `SecretStr` API key disiplini - bkz. app.services.ai_pipeline.
openai_provider) YENIDEN KULLANIR; Anthropic SDK EKLENMEZ. `ai_report`
pipeline'inin cok asamali stage sozlesmesinden (bkz. ai_pipeline.provider)
KASITLI olarak AYRIDIR: burada TEK, amaca-ozel bir istek yapilir - modele bir
GERCEK etkilesim adayi listesi verilir ve model YALNIZCA `candidate_id` secer.

KOORDINAT GUVENLIGI: cikti semasi `AIInteractionOutput`tur; `AIHotspotSelection`
`extra="forbid"` oldugu icin model bir koordinat (x/y/w/h) alani dondurse bile
SEMA TARAFINDAN reddedilir - model koordinat URETEMEZ. Haritada kullanilan
koordinatlar backend'de, dogrulanmis analyzer aday kayitlarindan (bkz.
service._resolve) yazilir.

GORSEL GIRDI: model gorsel girdi DESTEKLEMEDIGI icin (yalnizca metin gonderilir:
DOM + cikarilmis gorsel ozellikler) ekran goruntusunun modele gonderildigi /
analiz edildigi IDDIA EDILMEZ (bkz. schemas.INTERACTION_HEATMAP_DISCLAIMER /
METHOD_OPENAI_SELECTION).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

# NOT: `openai` SDK'si YALNIZCA gercek `InteractionHeatmapOpenAISelector` icinde
# (worker container'i) gerekir. Bu modul mock/protokol/ctx-key icin backend
# container'inda da import edilebilmelidir (openai orada kurulu degildir) - bu
# yuzden `import openai` MODUL SEVIYESINDE YAPILMAZ, yalnizca OpenAI secicisinin
# __init__/hata haritalama fonksiyonu icinde LAZILY yapilir.
from app.services.ai_interaction_heatmap.ranking import rank_interaction_hotspots
from app.services.ai_interaction_heatmap.schemas import (
    METHOD_DOM_VISUAL_RANKING,
    METHOD_OPENAI_SELECTION,
    METHOD_OPENAI_VISION_SELECTION,
    AIInteractionOutput,
    InteractionCandidate,
)
from app.services.ai_pipeline.hashing import hash_payload
from app.services.ai_pipeline.openai_pricing import estimate_cost_usd
from app.services.ai_pipeline.provider_errors import (
    AIProviderAuthenticationError,
    AIProviderError,
    AIProviderInvalidOutputError,
    AIProviderInvalidRequestError,
    AIProviderPermissionError,
    AIProviderRateLimitError,
    AIProviderRefusalError,
    AIProviderServerError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)

# arq `ctx` icinde heatmap seciciyi tasiyan TEK, ACIK anahtar (ai_report'un
# AI_PIPELINE_PROVIDER_CTX_KEY deseniyle ayni). String literal baska dosyaya
# DAGITILMAZ; secici enjekte eden (worker on_startup / test) ve okuyan tum kod
# bu sabiti kullanir.
INTERACTION_HEATMAP_SELECTOR_CTX_KEY = "interaction_heatmap_selector"

# `responses.parse` structured-output modu (deterministik/surumlenmis etiket).
_STRUCTURED_OUTPUT_MODE = "openai-responses.parse+pydantic-v1"
_SAFETY_IDENTIFIER_NAMESPACE = "synthetix-interaction-heatmap-safety-identifier-v1"

# Model gorseli DESTEKLEMEDIGI icin yalnizca metin gonderilir; sistem talimati
# koordinat-guvenligi ve "guclu eslesme yoksa uydurma" kurallarini ACIKCA kodlar.
_SYSTEM_INSTRUCTIONS = (
    "Bir sentetik UX arastirma asistanisin. Sana bir web sayfasindaki GERCEK "
    "etkilesim alanlarinin (adaylarin) listesi, bir hedef gorev ve hedef kitle "
    "verilir. Gorevin, verilen HEDEF GOREV icin sentetik bir kullanicinin daha "
    "OLASI etkilesecegi alanlari secmektir.\n"
    "KATI KURALLAR:\n"
    "1. YALNIZCA sana verilen 'candidate_id' degerlerinden secim yapabilirsin. "
    "Yeni bir aday veya koordinat (x/y/w/h) URETEMEZSIN.\n"
    "2. En fazla 5 hotspot dondur; en olası olandan baslayarak sirala.\n"
    "3. Bir adayin YALNIZCA buyuk/one cikan olmasi onu yuksek yapmaz; oncelik "
    "hedef gorevle anlam eslesmesindedir.\n"
    "4. Hedef gorevle GUCLU eslesen hicbir aday yoksa hotspot listesini BOS "
    "birak ve 'unmatched_task_warning' alanini doldur - alan UYDURMA.\n"
    "5. Bu gercek tiklama/goz takibi verisi degildir; sentetik bir tahmindir."
)


def _map_openai_error(exc: Exception) -> AIProviderError:
    """OpenAI SDK exception TIPINDEN (asla mesaj arama ile DEGIL) typed domain
    hatasina cevirir (openai_provider._map_openai_error ile ayni siniflandirma).
    `openai` LAZILY import edilir (bkz. modul basi notu)."""

    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        UnprocessableEntityError,
    )

    if isinstance(exc, APITimeoutError):
        return AIProviderTimeoutError("openai istegi zaman asimina ugradi")
    if isinstance(exc, APIConnectionError):
        return AIProviderTransportError("openai baglanti hatasi")
    if isinstance(exc, RateLimitError):
        return AIProviderRateLimitError("openai rate limit sinirina ulasildi")
    if isinstance(exc, AuthenticationError):
        return AIProviderAuthenticationError("openai kimlik dogrulamasi basarisiz")
    if isinstance(exc, PermissionDeniedError):
        return AIProviderPermissionError("openai istegi icin izin reddedildi")
    if isinstance(exc, BadRequestError | NotFoundError | UnprocessableEntityError):
        return AIProviderInvalidRequestError("openai istegi gecersiz/desteklenmiyor")
    if isinstance(exc, InternalServerError):
        return AIProviderServerError("openai sunucu hatasi")
    if isinstance(exc, APIStatusError):
        if exc.status_code >= 500:
            return AIProviderServerError("openai sunucu hatasi")
        return AIProviderInvalidRequestError("openai istegi reddedildi")
    return AIProviderTransportError("openai ile iletisimde beklenmeyen hata")


@dataclass(frozen=True)
class SelectorResult:
    """Bir secicinin (gercek/mock) dondurdugu tek, guvenli tasima nesnesi -
    ham prompt/istek/cevap TASIMAZ, yalnizca dogrulanmamis-DEGIL semaya UYGUN
    `AIInteractionOutput` + kisa metadata."""

    output: AIInteractionOutput
    provider_name: str
    model_name: str
    method: str
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float | None = None


@runtime_checkable
class InteractionHeatmapSelector(Protocol):
    """Bir heatmap secicisinin sozlesmesi. `select`, adaylardan gorev-odakli bir
    `AIInteractionOutput` uretir (yalnizca candidate_id secimi) veya bir
    `AIProviderError` alt sinifi firlatir - asla sessizce None/yari sonuc
    dondurmez."""

    provider_name: str
    model_name: str
    method: str
    is_mock: bool

    async def select(
        self,
        *,
        candidates: list[InteractionCandidate],
        target_task: str,
        target_audience: str | None,
        persona_distribution: dict | None,
        confirmed_candidate_id: str | None,
        screenshot_data: bytes | None,
        screenshot_content_type: str | None,
    ) -> SelectorResult: ...


def _candidate_view(candidate: InteractionCandidate, prerank_score: float) -> dict[str, Any]:
    """Modele gonderilen, koordinat-siz aday gorunumu. Ham x/y/w/h GONDERILMEZ
    (model onlari echo edip 'koordinat uretmis' gorunmesin diye); yalnizca
    kaba konum/boyut ipuclari + deterministik on-siralama skoru verilir."""

    if candidate.y < 0.34:
        vertical = "ust"
    elif candidate.y < 0.67:
        vertical = "orta"
    else:
        vertical = "alt"
    area = max(0.0, candidate.w) * max(0.0, candidate.h)
    size = "buyuk" if area >= 0.08 else ("orta" if area >= 0.02 else "kucuk")
    return {
        "candidate_id": candidate.candidate_id,
        "label": candidate.label,
        "interaction_kind": candidate.interaction_kind,
        "role": candidate.role,
        "above_fold": candidate.above_fold,
        "position": vertical,
        "size": size,
        "prerank_score": round(prerank_score, 4),
    }


def _build_input_payload(
    *,
    candidates: list[InteractionCandidate],
    target_task: str,
    target_audience: str | None,
    persona_distribution: dict | None,
    confirmed_candidate_id: str | None,
) -> dict[str, Any]:
    """Modele gonderilen JSON girdisi. Adaylar, deterministik siralayicinin
    (bkz. ranking.rank_interaction_hotspots) urettigi CANDIDATE PRE-RANKING
    skoruyla birlikte, on-siralamaya gore sirali verilir - bu yalnizca bir
    ipucudur (gorev talimati 'candidate pre-ranking'); NIHAI secimi model yapar."""

    prerank = rank_interaction_hotspots(
        candidates, target_task=target_task, confirmed_candidate_id=confirmed_candidate_id
    )
    prerank_scores = {h.candidate_id: h.score for h in prerank.hotspots}
    ordered = sorted(
        candidates, key=lambda c: (-prerank_scores.get(c.candidate_id, 0.0), c.candidate_id)
    )
    return {
        "target_task": target_task,
        "target_audience": target_audience,
        "persona_distribution": persona_distribution,
        "confirmed_candidate_id": confirmed_candidate_id,
        "candidates": [_candidate_view(c, prerank_scores.get(c.candidate_id, 0.0)) for c in ordered],
    }


class InteractionHeatmapOpenAISelector:
    """GERCEK OpenAI Responses API uzerinden calisan heatmap secicisi."""

    is_mock = False
    provider_name = "openai"
    method = METHOD_OPENAI_SELECTION

    _IMAGE_CAPABLE_MODEL_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-5", "o3", "o4")
    _SUPPORTED_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    _MAX_IMAGE_BYTES = 20 * 1024 * 1024

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: int,
        max_output_tokens: int,
    ) -> None:
        from openai import AsyncOpenAI

        self.model_name = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        # SDK otomatik retry KAPALI (retry otoritesi worker'dadir); `timeout`
        # SDK'nin resmi mekanizmasidir (ikinci bir asyncio.wait_for eklenmez).
        self._client = AsyncOpenAI(api_key=api_key, timeout=float(timeout_seconds), max_retries=0)

    async def aclose(self) -> None:
        await self._client.close()

    async def select(
        self,
        *,
        candidates: list[InteractionCandidate],
        target_task: str,
        target_audience: str | None,
        persona_distribution: dict | None,
        confirmed_candidate_id: str | None,
        screenshot_data: bytes | None,
        screenshot_content_type: str | None,
    ) -> SelectorResult:
        payload = _build_input_payload(
            candidates=candidates,
            target_task=target_task,
            target_audience=target_audience,
            persona_distribution=persona_distribution,
            confirmed_candidate_id=confirmed_candidate_id,
        )
        serialized_input = json.dumps(payload, ensure_ascii=False)
        safety_identifier = hash_payload(
            {"namespace": _SAFETY_IDENTIFIER_NAMESPACE, "payload": payload}
        )

        image_sent = bool(
            screenshot_data
            and screenshot_content_type in self._SUPPORTED_IMAGE_CONTENT_TYPES
            and len(screenshot_data) <= self._MAX_IMAGE_BYTES
            and self.model_name.lower().startswith(self._IMAGE_CAPABLE_MODEL_PREFIXES)
        )
        user_content: list[dict[str, Any]] = [{"type": "input_text", "text": serialized_input}]
        if image_sent and screenshot_data is not None:
            encoded = base64.b64encode(screenshot_data).decode("ascii")
            user_content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{screenshot_content_type};base64,{encoded}",
                    "detail": "high",
                }
            )

        try:
            response = await self._client.responses.parse(
                model=self.model_name,
                input=[
                    {"role": "system", "content": _SYSTEM_INSTRUCTIONS},
                    {"role": "user", "content": cast(Any, user_content)},
                ],
                text_format=AIInteractionOutput,
                reasoning={"effort": cast(Any, self._reasoning_effort)},
                store=False,
                max_output_tokens=self._max_output_tokens,
                safety_identifier=safety_identifier,
            )
        except AIProviderError:
            raise
        except Exception as exc:  # openai.OpenAIError alt siniflari
            raise _map_openai_error(exc) from None

        if response.status != "completed":
            raise AIProviderInvalidOutputError(
                f"openai yaniti tamamlanmadi (status={response.status})"
            )
        for item in response.output:
            if getattr(item, "type", None) == "message":
                for content in getattr(item, "content", ()):
                    if getattr(content, "type", None) == "refusal":
                        raise AIProviderRefusalError("openai istegi reddetti (refusal)")

        parsed = response.output_parsed
        if parsed is None or not isinstance(parsed, AIInteractionOutput):
            raise AIProviderInvalidOutputError("openai yapilandirilmis ciktiyi parse edemedi")

        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0
        estimated_cost = estimate_cost_usd(
            model=self.model_name, input_tokens=input_tokens, output_tokens=output_tokens
        )
        return SelectorResult(
            output=parsed,
            provider_name=self.provider_name,
            model_name=self.model_name,
            method=METHOD_OPENAI_VISION_SELECTION if image_sent else self.method,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=float(estimated_cost) if estimated_cost is not None else None,
        )


class MockInteractionHeatmapSelector:
    """Network'suz, DETERMINISTIK gelistirme/test secicisi. Ciktiyi deterministik
    siralayicidan (rank_interaction_hotspots) uretir ve ACIKCA `provider="mock"`
    + deterministik yontem etiketiyle isaretler - bu, "sessizce AI gibi
    gosterme" yasagini ihlal ETMEZ (provider acikca mock'tur) ve production'da
    `settings.allow_mock_ai_provider` ile kapilanir (bkz. config.
    interaction_heatmap_provider_ready). Gercek OpenAI degildir."""

    is_mock = True
    provider_name = "mock"
    model_name = "deterministic-dom-visual-ranker"
    method = METHOD_DOM_VISUAL_RANKING

    async def select(
        self,
        *,
        candidates: list[InteractionCandidate],
        target_task: str,
        target_audience: str | None,
        persona_distribution: dict | None,
        confirmed_candidate_id: str | None,
        screenshot_data: bytes | None,
        screenshot_content_type: str | None,
    ) -> SelectorResult:
        output = rank_interaction_hotspots(
            candidates, target_task=target_task, confirmed_candidate_id=confirmed_candidate_id
        )
        return SelectorResult(
            output=output,
            provider_name=self.provider_name,
            model_name=self.model_name,
            method=self.method,
        )


__all__ = [
    "INTERACTION_HEATMAP_SELECTOR_CTX_KEY",
    "InteractionHeatmapSelector",
    "InteractionHeatmapOpenAISelector",
    "MockInteractionHeatmapSelector",
    "SelectorResult",
]
