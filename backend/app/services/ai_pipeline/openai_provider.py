"""Gercek OpenAI Responses API `AIProvider` adaptoru (Faz 3D.1).

`OpenAIProvider`, `app.services.ai_pipeline.provider.AIProvider` protokolunu
uygular ve `AsyncOpenAI().responses.parse(...)` ile stage 3/4/6 icin
yapilandirilmis (Pydantic) cikti uretir. Bu modul:

- Streaming/tools/web-search/previous_response_id/prompt-caching/Batch API
  KULLANMAZ.
- `store=False` gonderir; SDK'nin kendi otomatik retry'ini kapatir
  (`max_retries=0` - retry otoritesi mevcut Synthetix worker'dadir, bkz.
  app.services.ai_pipeline.retry_policy).
- Ham OpenAI `Response` nesnesini domain katmanina ASLA dondurmez; ham
  prompt/istek govdesi/cevap hicbir hata mesajina/loga eklenmez.
- API anahtarini yalnizca `AsyncOpenAI` client'ini olustururken okur; repr/log/
  hata mesajinda ASLA gorunmez (bkz. `app.config.Settings.openai_api_key`
  `SecretStr`).
"""

from __future__ import annotations

import json
import time
from typing import Any, cast

# NOT: `openai` SDK'si YALNIZCA gercek `OpenAIProvider` instantiate/kullanildiginda
# gereklidir. Modul, `app.worker` tarafindan (ai_report kapali olsa bile) her zaman
# import edilir; ayrica analyzer container'inda da openai KURULU DEGILDIR. Bu yuzden
# `import openai` MODUL SEVIYESINDE YAPILMAZ - aksi halde openai eksik bir imajda
# `import app.worker` cokup TUM worker'i (simulasyon islemcisi + reaper dahil)
# dusururdu ve run'lar sonsuza kadar RUNNING kalirdi. `openai_selector.py` ile AYNI
# desen (bkz. o modulun basindaki not) - openai yalnizca `__init__` ve
# `_map_openai_error` icinde lazy import edilir.
from pydantic import BaseModel

from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.hashing import PromptDescriptor, hash_payload
from app.services.ai_pipeline.openai_pricing import estimate_cost_usd
from app.services.ai_pipeline.provider import (
    OutputT,
    ProviderResult,
    compute_configuration_fingerprint,
    validate_stage_contract,
)
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

# `responses.parse`in structured-output moduna ait, deterministik/surumlenmis
# etiket - `configuration_fingerprint`e girer (bkz. gorev talimati madde 5).
STRUCTURED_OUTPUT_MODE = "openai-responses.parse+pydantic-v1"

# Safety identifier icin sabit, sirra bagli OLMAYAN namespace (bkz. gorev
# talimati madde 7 - "API key veya baska secret'i hash salt'i olarak
# kullanma"). Bu deger bir sir DEGILDIR, yalnizca hash girdisini diger
# olasi kullanimlardan ayiran sabit bir etikettir.
_SAFETY_IDENTIFIER_NAMESPACE = "synthetix-ai-pipeline-safety-identifier-v1"


def _derive_safety_identifier(input_payload: BaseModel) -> str:
    """OpenAI `safety_identifier`i icin stabil, privacy-preserving bir kimlik
    turetir.

    Ham simulation_run_id/kullanici/organizasyon kimligi ASLA dondurmez ve
    ASLA loglanmaz. `AIProvider.generate_structured` protokolu `simulation_
    run_id`i ayri bir parametre olarak TASIMADIGI icin (bkz. stage_runner.py
    cagrilari), bu fonksiyon Stage 3/4/6 girdilerinin HEPSININ ORTAK tasidigi
    `evidence` alanindan (bir pipeline calistirmasi icinde TUM asamalarda
    ayni PageEvidence) turetir: ayni run -> ayni evidence -> ayni safety
    identifier; farkli run -> farkli evidence -> farkli safety identifier
    (bkz. rapor "bilinen riskler" - protokolu degistirmeden elde edilen
    pratik bir yaklasimdir, kriptografik run-tekilligi IDDIA ETMEZ)."""

    evidence = getattr(input_payload, "evidence", None)
    basis = (
        evidence.model_dump(mode="json") if evidence is not None else input_payload.model_dump(mode="json")
    )
    return hash_payload({"namespace": _SAFETY_IDENTIFIER_NAMESPACE, "evidence": basis})


def _map_openai_error(exc: Exception) -> AIProviderError:
    """OpenAI SDK exception TIPINDEN (asla mesaj/string arama ile DEGIL) typed
    domain hatasina cevirir (bkz. gorev talimati madde 10). `openai` LAZILY
    import edilir (bkz. modul basi notu)."""

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


class OpenAIProvider:
    """OpenAI Responses API uzerinden calisan gercek `AIProvider` adaptoru."""

    provider_name = "openai"
    is_mock = False

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: int,
        max_output_tokens: int,
    ) -> None:
        self.model_name = model
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        # Semantik yapilandirma kimligi - API key/timeout/secret/pid/
        # timestamp/rastgele deger ASLA icermez (bkz. provider.
        # compute_configuration_fingerprint dokstring'i).
        self.configuration_fingerprint = compute_configuration_fingerprint(
            provider_name=self.provider_name,
            model_name=model,
            reasoning_effort=reasoning_effort,
            structured_output_mode=STRUCTURED_OUTPUT_MODE,
        )
        # SDK otomatik retry KAPALI (max_retries=0) - retry otoritesi mevcut
        # Synthetix worker'idir (bkz. modul dokstring'i). `timeout` SDK'nin
        # resmi timeout mekanizmasidir; ikinci bir `asyncio.wait_for` katmani
        # EKLENMEZ. `openai` LAZILY import edilir (bkz. modul basi notu) -
        # eksikse burada acik `ModuleNotFoundError` firlar ve cagiran (worker
        # on_startup) bunu yakalayip provider'i devre disi birakir.
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=float(timeout_seconds), max_retries=0)

    async def aclose(self) -> None:
        """OpenAI async client'inin resmi kapatma yasam donguson cagirir."""

        await self._client.close()

    async def generate_structured(
        self,
        *,
        stage_type: AIPipelineStageType,
        batch_index: int | None,
        prompt: PromptDescriptor,
        input_payload: BaseModel,
        output_schema: type[OutputT],
    ) -> ProviderResult[OutputT]:
        validate_stage_contract(
            stage_type=stage_type,
            input_payload=input_payload,
            output_schema=output_schema,
        )

        safety_identifier = _derive_safety_identifier(input_payload)
        serialized_input = json.dumps(input_payload.model_dump(mode="json"), ensure_ascii=False)

        start = time.perf_counter()
        try:
            response = await self._client.responses.parse(
                model=self.model_name,
                input=[
                    {"role": "system", "content": prompt.system_instructions},
                    {"role": "user", "content": serialized_input},
                ],
                text_format=output_schema,
                reasoning={"effort": cast(Any, self._reasoning_effort)},
                store=False,
                max_output_tokens=self._max_output_tokens,
                safety_identifier=safety_identifier,
            )
        except AIProviderError:
            raise
        except Exception as exc:  # openai.OpenAIError alt siniflari
            raise _map_openai_error(exc) from None
        duration_ms = int(round((time.perf_counter() - start) * 1000))

        if response.status != "completed":
            # incomplete/failed/cancelled/in_progress/queued: senkron cagri
            # tamamlanmadan donmustur (ornegin max_output_tokens kesintisi).
            # Ham detay/govde LOGLANMAZ - yalnizca sabit/guvenli bir aciklama.
            raise AIProviderInvalidOutputError(f"openai yaniti tamamlanmadi (status={response.status})")

        for item in response.output:
            if getattr(item, "type", None) == "message":
                for content in getattr(item, "content", ()):
                    if getattr(content, "type", None) == "refusal":
                        raise AIProviderRefusalError("openai istegi reddetti (refusal)")

        parsed = response.output_parsed
        if parsed is None or not isinstance(parsed, output_schema):
            raise AIProviderInvalidOutputError("openai yapilandirilmis ciktiyi parse edemedi")

        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else 0
        output_tokens = usage.output_tokens if usage is not None else 0
        estimated_cost = estimate_cost_usd(
            model=self.model_name, input_tokens=input_tokens, output_tokens=output_tokens
        )

        return ProviderResult(
            output=parsed,
            provider_name=self.provider_name,
            model_name=self.model_name,
            is_mock=False,
            configuration_fingerprint=self.configuration_fingerprint,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=float(estimated_cost) if estimated_cost is not None else None,
            request_duration_ms=duration_ms,
            provider_request_id=response.id,
        )


__all__ = ["OpenAIProvider", "STRUCTURED_OUTPUT_MODE"]
