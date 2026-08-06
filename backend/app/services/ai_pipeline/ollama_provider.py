"""Yerel Ollama `AIProvider` adaptoru (Faz 3D.2-LOCAL).

`OllamaProvider`, `app.services.ai_pipeline.provider.AIProvider` protokolunu
uygular ve bilgisayarda calisan bir Ollama daemon'inin `POST /api/chat`
uc noktasi uzerinden stage 3/4/6 icin yapilandirilmis (Pydantic) cikti uretir.
Bu modul:

- Tools/web-search/dis kaynak erisimi KULLANMAZ; `stream=False` gonderir.
- Yalnizca loopback host'lara (127.0.0.1/localhost/::1) baglanir - uzak bir
  Ollama sunucusu YALNIZCA `allow_remote_host=True` acikca verilirse kabul
  edilir (bkz. app.config._is_allowed_ollama_host). HTTP client `redirects`
  KAPALIDIR (`follow_redirects=False`) - bir yeniden yonlendirme ile uzak bir
  adrese cikilmasi ENGELLENIR.
- Ham Ollama cevabini domain katmanina ASLA dondurmez; ham prompt/istek
  govdesi/cevap hicbir hata mesajina/loga eklenmez - yalnizca sanitize
  edilmis, sabit gozlemlenebilirlik alanlari (total_duration/load_duration/
  prompt_eval_count/eval_count) DEBUG seviyesinde loglanabilir.
- Provider seviyesinde bounded `asyncio.Semaphore` (`max_concurrency`,
  varsayilan 1) - ayni anda en fazla `max_concurrency` kadar HTTP cagrisi
  calisir; yerel makinenin asiri yuklenmemesi icindir. Semaphore YALNIZCA
  HTTP cagrisini sarar - hicbir DB transaction/lock tutulurken BEKLENMEZ
  (bu provider hic DB'ye dokunmaz).
- `estimated_cost` daima 0.0'dir - bu yalnizca harici API ucretinin sifir
  oldugu anlamina gelir; elektrik/donanim kullanimi hesaplanmaz.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from app.config import _is_allowed_ollama_host
from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.hashing import PromptDescriptor
from app.services.ai_pipeline.provider import (
    OutputT,
    ProviderResult,
    compute_configuration_fingerprint,
    validate_stage_contract,
)
from app.services.ai_pipeline.provider_errors import (
    AIProviderConfigurationError,
    AIProviderError,
    AIProviderInvalidOutputError,
    AIProviderInvalidRequestError,
    AIProviderServerError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)

OllamaStructuredOutputMode = Literal["json", "json_schema"]

# Ollama `/api/chat` `format=<...>` + Pydantic dogrulama modlarina ait,
# deterministik/surumlenmis etiketler - `configuration_fingerprint`e girer.
# Iki mod birbirini DISLAR (bkz. Settings.ollama_structured_output_mode,
# otomatik fallback YOKTUR); her etiket AYRI ve versiyonludur ki temsil
# davranisi degistiginde ilgili etiket de DEGISMEK ZORUNDA kalsin.
#
# Kok neden (Faz 3D.3A.1/3D.3A.2): gercek bir yerel Ollama daemon'ina (0.32.5,
# qwen3:8b) karsi calistirilan kontrollu smoke-testlerde, `format=<json_schema>`
# ile gonderilen TUM gercek pipeline semalari (ic ice `$defs`/`$ref`
# DUZLESTIRILMIS olsa BILE) HTTP 400 ile REDDEDILDI; Ollama'nin kendi sunucu
# logu (`server.log`, hicbir prompt/cevap icermeyen sabit bir hata) bunun
# Ollama'nin KENDI JSON-Schema->GBNF-grammar donusturucusunun urettigi
# grammar'i, yine KENDI grammar parser'inin (llama.cpp) parse edemedigini
# ("failed to parse grammar") gosterdi - generation'in KENDISI hic baslamiyor.
# Bu nedenle "json" (gevsek `format="json"` + promptta acikca verilen sema
# sozlesmesi) VARSAYILANDIR; "json_schema" ileride Ollama/llama.cpp surumu
# duzeldiginde deneysel/opsiyonel olarak denenebilir.
_STRUCTURED_OUTPUT_MODE_LABELS: dict[OllamaStructuredOutputMode, str] = {
    "json": "ollama-chat.format=json+prompt-embedded-schema+pydantic-v1",
    "json_schema": "ollama-chat.format=json_schema-refs-inlined+pydantic-v1",
}

# "json_schema" modunda Ollama'nin structured-output ozelligi semayi ayrica
# `format` alaniyla ZATEN gonderdigi icin promptta yalnizca KISA, sabit bir
# hatirlatma yeterlidir.
_SCHEMA_REMINDER = "Yanit YALNIZCA saglanan JSON semasina uyan tek bir JSON nesnesi olmalidir."

# "json" modunda `format="json"` YALNIZCA "gecerli JSON uret" der - HICBIR
# ALAN/TIP KISITLAMASI tasimaz; bu yuzden modelin uymasi gereken sema, PROMPT
# METNINDE acikca ve KANONIK (deterministik, `sort_keys=True`) bir JSON
# olarak, evidence/kullanici icerigiyle KARISTIRILAMAYACAK sekilde acik
# ayraclarla (BEGIN/END) verilir. Bu metin ham bir prompt/kullanici girdisi
# DEGILDIR - sabit, versiyonlu bir sablondur; hicbir yerde ayrica loglanmaz.
_JSON_MODE_SCHEMA_CONTRACT_TEMPLATE = (
    "\n\nSISTEM SEMA SOZLESMESI (asagidaki JSON Schema bir kullanici/evidence "
    "icerigi DEGILDIR - degistirilemez bir cikti sozlesmesidir):\n"
    "-----BEGIN SCHEMA CONTRACT-----\n"
    "{schema_json}\n"
    "-----END SCHEMA CONTRACT-----\n"
    "Bu sozlesmeye gore:\n"
    "- Yanit YALNIZCA yukaridaki semaya uyan, TEK bir JSON nesnesi olmalidir.\n"
    "- Sema disinda hicbir alan EKLEME (bilinmeyen/fazla alan yasaktir).\n"
    "- 'required' listesindeki TUM alanlari doldur.\n"
    "- JSON disinda hicbir metin, aciklama veya markdown kod bloğu EKLEME."
)


def _inline_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """`$defs`/`$ref` iceren bir Pydantic JSON Schema'sini tamamen kendi
    icinde (self-contained, referanssiz) olacak sekilde duzlestirir.

    Kok neden (Faz 3D.3A.1): ic ice Pydantic modelleri (ornegin
    `ScenarioInterpretation.steps: tuple[TaskStep, ...]`) `model_json_schema()`
    tarafindan `$defs` + `$ref` ile temsil edilir. Yerel bir Ollama
    daemon'ina (0.32.5, qwen3:8b) karsi calistirilan gercek, kontrollu bir
    reproduction cagrisinda bu sema `format` alaninda AYNEN gonderildiginde
    Ollama `/api/chat` HTTP 400 ile REDDETTI (baglanti/timeout/5xx/model-not-
    found DEGIL - gercek bir istek-govdesi reddi). Ayni sema `$ref=2`,
    `$defs=1` (2 ic ice tip) iceriyordu; `anyOf`/`oneOf`/`allOf`/`enum`/
    `format` (date-time vb.) HIC yoktu (0 sayimla elendi) - `$defs`/`$ref`
    ic ice referanslamasi, ekarte edilemeyen tek yapisal aday olarak kaldi.

    Bu fonksiyon SAF ve DETERMINISTIKTIR (yan etkisi yoktur, ayni girdi ->
    ayni cikti). `required`/`properties`/`items`/`enum`/temel tip anlami
    KORUNUR - yalnizca `$ref` isaretcileri, isaret ettikleri `$defs` girdisiyle
    YER DEGISTIRIR ve sonuc semada `$defs` kalmaz. `output_schema.
    model_validate_json` (modelin cevabini ayrıstırma/dogrulama) bu
    fonksiyondan TAMAMEN BAGIMSIZDIR - orijinal (ic ice) Pydantic modeli
    uzerinden, DEGISTIRILMEDEN calismaya devam eder; yalnizca Ollama'ya
    GONDERILEN `format` govdesi normalize edilir.
    """

    defs = schema.get("$defs")
    if not isinstance(defs, dict) or not defs:
        return schema

    def _resolve(node: Any, seen: tuple[str, ...]) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                key = ref.rsplit("/", 1)[-1]
                if key in seen:
                    # Bu kod tabanindaki gercek domain semalarinda (Scenario/
                    # PersonaBehavior/UXReport) hicbir ozyinelemeli (self-
                    # referanslı) tip yok; yine de sonsuz donguye karsi
                    # savunma amacli, guvenli tarafta kalinir.
                    raise ValueError(f"ollama sema normalizasyonu: dongusel $ref ({key})")
                target = _resolve(defs.get(key, {}), (*seen, key))
                extra_keys = {k: v for k, v in node.items() if k not in ("$ref", "$defs")}
                if extra_keys:
                    merged: dict[str, Any] = dict(target) if isinstance(target, dict) else {}
                    merged.update({k: _resolve(v, seen) for k, v in extra_keys.items()})
                    return merged
                return target
            return {k: _resolve(v, seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [_resolve(v, seen) for v in node]
        return node

    return _resolve(schema, ())


def _map_ollama_error(exc: Exception) -> AIProviderError:
    """Ollama HTTP istemci exception TIPINDEN (asla mesaj/string arama ile
    DEGIL) typed domain hatasina cevirir."""

    if isinstance(exc, httpx.TimeoutException):
        return AIProviderTimeoutError("ollama istegi zaman asimina ugradi")
    if isinstance(exc, httpx.ConnectError):
        return AIProviderTransportError("ollama baglanti hatasi")
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 404:
            return AIProviderInvalidRequestError("ollama modeli bulunamadi/yapilandirilmadi")
        if status_code >= 500:
            return AIProviderServerError("ollama sunucu hatasi")
        return AIProviderInvalidRequestError("ollama istegi gecersiz/desteklenmiyor")
    if isinstance(exc, httpx.HTTPError):
        return AIProviderTransportError("ollama ile iletisimde tasima hatasi")
    return AIProviderTransportError("ollama ile iletisimde beklenmeyen hata")


class OllamaProvider:
    """Yerel Ollama `/api/chat` uzerinden calisan `AIProvider` adaptoru."""

    provider_name = "ollama"
    is_mock = False

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: int,
        temperature: float,
        keep_alive: str,
        num_ctx: int | None,
        max_output_tokens: int,
        max_concurrency: int,
        allow_remote_host: bool,
        structured_output_mode: OllamaStructuredOutputMode = "json",
    ) -> None:
        if not _is_allowed_ollama_host(base_url, allow_remote_host=allow_remote_host):
            raise AIProviderConfigurationError("ollama base_url izinli bir host degil")

        self.model_name = model
        self._temperature = temperature
        self._keep_alive = keep_alive
        self._num_ctx = num_ctx
        self._max_output_tokens = max_output_tokens
        self._structured_output_mode = structured_output_mode
        # Semantik yapilandirma kimligi - API key kavrami yok, ama timeout/
        # keep_alive/max_concurrency/base_url (port haric) ASLA icermez (bkz.
        # provider.compute_configuration_fingerprint dokstring'i).
        # `structured_output_mode`, ETIKET (`_STRUCTURED_OUTPUT_MODE_LABELS`)
        # uzerinden fingerprint'e girer - "json" ve "json_schema" farkli
        # fingerprint'ler URETIR (bkz. gorev talimati madde 6).
        self.configuration_fingerprint = compute_configuration_fingerprint(
            provider_name=self.provider_name,
            model_name=model,
            reasoning_effort="n/a",
            structured_output_mode=_STRUCTURED_OUTPUT_MODE_LABELS[structured_output_mode],
            semantic_settings={
                "temperature": temperature,
                "num_ctx": num_ctx,
                "max_output_tokens": max_output_tokens,
            },
        )
        # Redirect KAPALI (SSRF guard) - SDK/kendi otomatik retry'i yok,
        # retry otoritesi mevcut Synthetix worker'idir.
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=float(timeout_seconds), follow_redirects=False
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def aclose(self) -> None:
        """Ollama HTTP client'inin resmi kapatma yasam donguson cagirir."""

        await self._client.aclose()

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

        serialized_input = json.dumps(input_payload.model_dump(mode="json"), ensure_ascii=False)

        # Iki mod birbirini DISLAR, otomatik fallback YOKTUR (bkz. modul ici
        # not, Settings.ollama_structured_output_mode):
        #   "json"        -> `format="json"` (gevsek); sema PROMPT metninde
        #                     acikca ve kanonik (sort_keys=True) verilir -
        #                     `_inline_schema_refs` bu modda HIC CAGRILMAZ.
        #   "json_schema" -> `format=<normalize edilmis sema>` (bkz.
        #                     `_inline_schema_refs`); promptta yalnizca kisa
        #                     bir hatirlatma yeterlidir (sema zaten `format`da).
        # Her iki modda da cevap ASAGIDA AYNI SEKILDE (orijinal, ic ice
        # Pydantic modeliyle) dogrulanir - bu ayrim YALNIZCA giden temsili
        # degistirir, dogrulama sozlesmesini DEGISTIRMEZ.
        if self._structured_output_mode == "json":
            canonical_schema_json = json.dumps(
                output_schema.model_json_schema(), sort_keys=True, ensure_ascii=False
            )
            system_content = prompt.system_instructions + _JSON_MODE_SCHEMA_CONTRACT_TEMPLATE.format(
                schema_json=canonical_schema_json
            )
            format_value: object = "json"
        else:
            system_content = f"{prompt.system_instructions}\n\n{_SCHEMA_REMINDER}"
            format_value = _inline_schema_refs(output_schema.model_json_schema())

        options: dict[str, object] = {"temperature": self._temperature}
        if self._num_ctx is not None:
            options["num_ctx"] = self._num_ctx
        options["num_predict"] = self._max_output_tokens

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": serialized_input},
            ],
            "stream": False,
            "format": format_value,
            "options": options,
            "keep_alive": self._keep_alive,
        }

        start = time.perf_counter()
        try:
            async with self._semaphore:
                response = await self._client.post("/api/chat", json=payload)
                response.raise_for_status()
            body = response.json()
        except AIProviderError:
            raise
        except json.JSONDecodeError:
            raise AIProviderInvalidOutputError("ollama gecersiz JSON govde dondurdu") from None
        except Exception as exc:  # httpx.HTTPError alt siniflari
            raise _map_ollama_error(exc) from None
        duration_ms = int(round((time.perf_counter() - start) * 1000))

        if body.get("done") is not True:
            raise AIProviderInvalidOutputError("ollama yaniti tamamlanmadi (done != true)")

        # `done_reason` varsa (Ollama surumune gore) VE "stop" DEGILSE -
        # ornegin "length" (num_predict/output token siniri asildi) - yarim/
        # kesik bir JSON govdesi olasidir. Iceriginin TESADUFEN semaya uysa
        # bile GUVENLI TARAFTA kalinir ve basarili KABUL EDILMEZ.
        done_reason = body.get("done_reason")
        if done_reason is not None and done_reason != "stop":
            raise AIProviderInvalidOutputError(f"ollama yaniti eksik/kesik (done_reason={done_reason!r})")

        content = body.get("message", {}).get("content")
        if not content:
            raise AIProviderInvalidOutputError("ollama yaniti bos icerik dondurdu")

        try:
            parsed = output_schema.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError):
            raise AIProviderInvalidOutputError("ollama yapilandirilmis ciktiyi parse edemedi") from None

        input_tokens = int(body.get("prompt_eval_count") or 0)
        output_tokens = int(body.get("eval_count") or 0)

        return ProviderResult(
            output=parsed,
            provider_name=self.provider_name,
            model_name=self.model_name,
            is_mock=False,
            configuration_fingerprint=self.configuration_fingerprint,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=0.0,
            request_duration_ms=duration_ms,
            provider_request_id=None,
        )


__all__ = ["OllamaProvider", "OllamaStructuredOutputMode"]
