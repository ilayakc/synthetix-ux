"""Faz 3D.2-LOCAL: tek cagrilik, MANUEL yerel Ollama smoke-test entrypoint'i.

Otomatik pytest suite'inin BIR PARCASI DEGILDIR ve pytest tarafindan ASLA
otomatik calistirilmaz - yalnizca bir gelistirici, gercekten calisan bir
Ollama daemon'ina karsi manuel olarak calistirdiginda bir seyler yapar
(`python -m app.services.ai_pipeline.ollama_smoke --confirm-local-live`).

Guvenlik kapilari:
- `--confirm-local-live` ACIKCA verilmeden hicbir provider olusturulmaz,
  hicbir istek atilmaz.
- `settings.ai_report_provider` "ollama" DEGILSE calismayi reddeder (baska
  bir provider'a sessizce GECMEZ).
- Tam olarak BIR `generate_structured` cagrisi yapar; DB/chip/pipeline
  kaydi OLUSTURMAZ; OpenAI/Gemini cagirmaz.
- Terminale yalnizca sanitize edilmis ozet alanlari yazdirir - ham
  prompt/response icerigi ASLA yazdirilmaz.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings
from app.models.ai_pipeline import AIPipelineStageType
from app.services.ai_pipeline.evidence import prepare_page_evidence
from app.services.ai_pipeline.ollama_provider import OllamaProvider
from app.services.ai_pipeline.prompts import get_prompt
from app.services.ai_pipeline.provider_errors import AIProviderError
from app.services.ai_pipeline.schemas import ScenarioInterpretation
from app.services.ai_pipeline.stage_inputs import ScenarioInterpretationInput


def _build_fixture_input() -> ScenarioInterpretationInput:
    """En kucuk mevcut Pydantic pipeline semasi (Stage 3) icin, kucuk/
    sentetik/kisisel veri ICERMEYEN sabit bir ornek girdi."""

    evidence = prepare_page_evidence(
        source_type="sim", metrics={"task_completion_probability": {"point_estimate": 0.5}}
    )
    return ScenarioInterpretationInput(
        evidence=evidence,
        target_task="Ornek gorev: sepete urun ekle",
        test_name="smoke-test",
        test_description="Yerel Ollama smoke-test icin sentetik ornek",
        methodology_context="Manuel smoke-test - gercek kullanici verisi icermez",
    )


async def _run(*, confirm_local_live: bool) -> int:
    if not confirm_local_live:
        print("HATA: --confirm-local-live bayragi olmadan calismaz.", file=sys.stderr)
        return 2

    if settings.ai_report_provider != "ollama":
        print(
            f"HATA: AI_REPORT_PROVIDER='{settings.ai_report_provider}' (beklenen: 'ollama').",
            file=sys.stderr,
        )
        return 2

    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        temperature=settings.ollama_temperature,
        keep_alive=settings.ollama_keep_alive,
        num_ctx=settings.ollama_num_ctx,
        max_output_tokens=settings.ollama_max_output_tokens,
        max_concurrency=settings.ollama_max_concurrency,
        allow_remote_host=settings.ollama_allow_remote_host,
        structured_output_mode=settings.ollama_structured_output_mode,
    )
    try:
        prompt = get_prompt(AIPipelineStageType.SCENARIO_INTERPRETATION)
        try:
            result = await provider.generate_structured(
                stage_type=AIPipelineStageType.SCENARIO_INTERPRETATION,
                batch_index=None,
                prompt=prompt,
                input_payload=_build_fixture_input(),
                output_schema=ScenarioInterpretation,
            )
        except AIProviderError as exc:
            print("provider:", provider.provider_name)
            print("model:", provider.model_name)
            print("schema:", ScenarioInterpretation.__name__)
            print("success: False")
            print("error_code:", exc.error_code)
            return 1

        print("provider:", provider.provider_name)
        print("model:", provider.model_name)
        print("schema:", ScenarioInterpretation.__name__)
        print("success: True")
        print("input_tokens:", result.input_tokens)
        print("output_tokens:", result.output_tokens)
        print("duration_ms:", result.request_duration_ms)
        print("estimated_cost_usd:", result.estimated_cost)
        return 0
    finally:
        await provider.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manuel, tek cagrilik yerel Ollama smoke-test (gercek network cagrisi yapar)."
    )
    parser.add_argument(
        "--confirm-local-live",
        action="store_true",
        default=False,
        help="Gercek/yerel Ollama'ya tek bir istek atmayi ACIKCA onaylar (zorunlu).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(confirm_local_live=args.confirm_local_live))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
