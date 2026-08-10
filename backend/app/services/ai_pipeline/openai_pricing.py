"""OpenAI Responses API icin merkezi, SURUMLENMIS iç maliyet-tahmini metadata'si
(Faz 3D.1).

`app.services.pricing`in surumlenme deseniyle aynidir (bkz. o modulun
dokstring'i): fiyat sabitleri kodun geneline dagitilmaz, tek bir surumlu
yapida (`_OPENAI_MODEL_PRICING`) tutulur. Bu, KULLANICIYA yansitilan Chip
fiyatiyla (bkz. app.services.pricing.AI_REPORT_CHIP_COST) HICBIR ILISKISI
OLMAYAN, yalnizca ic muhasebe/gozlemlenebilirlik amacli TAHMINI bir USD
maliyettir - Chip fiyati bu modulden ETKILENMEZ.

Tum parasal hesaplamalar `Decimal` ile yapilir (float yuvarlama hatasi
YOK); yalnizca `ProviderResult.estimated_cost` sinirinda (mevcut domain
semasi `float`dur, bkz. bilinen riskler) `float`e cevrilir.

Bilinmeyen/kayitli olmayan bir model icin maliyet UYDURULMAZ - `None` doner.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

# Bu fiyat metadata'sinin dogrulandigi surum/tarih - yeni bir fiyat
# guncellemesi/dogrulamasi icin bu deger ARTAR ve ESKI kayitlar DEGISTIRILMEZ
# (bkz. app.services.pricing versioned deseni).
OPENAI_PRICING_VERSION = "2026.08-v2"


@dataclass(frozen=True)
class OpenAIModelPricing:
    """Tek bir OpenAI modelinin, 1 milyon token basina USD fiyati."""

    model: str
    input_rate_per_million: Decimal
    output_rate_per_million: Decimal
    verified_at: str


# Resmi OpenAI model sayfasinda dogrulanan degerler (gpt-5.6-terra,
# varsayilan model). Yeni bir model eklenirse buraya AYRI bir kayit eklenir.
_OPENAI_MODEL_PRICING: dict[str, OpenAIModelPricing] = {
    "gpt-5.6-terra": OpenAIModelPricing(
        model="gpt-5.6-terra",
        input_rate_per_million=Decimal("2.00"),
        output_rate_per_million=Decimal("12.00"),
        verified_at="2026-08-10",
    ),
}


def get_model_pricing(model: str) -> OpenAIModelPricing | None:
    """Verilen model icin kayitli fiyat metadata'sini dondurur (yoksa `None`)."""

    return _OPENAI_MODEL_PRICING.get(model)


def estimate_cost_usd(*, model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    """`model` icin TAHMINI USD maliyeti (`Decimal`) hesaplar.

    Model kayitli degilse (bilinmeyen/desteklenmeyen model) maliyet
    UYDURULMAZ - `None` doner (cagiran taraf `ProviderResult.estimated_cost`i
    `None` birakir)."""

    pricing = get_model_pricing(model)
    if pricing is None:
        return None
    million = Decimal(1_000_000)
    return (Decimal(input_tokens) * pricing.input_rate_per_million / million) + (
        Decimal(output_tokens) * pricing.output_rate_per_million / million
    )


__all__ = [
    "OPENAI_PRICING_VERSION",
    "OpenAIModelPricing",
    "get_model_pricing",
    "estimate_cost_usd",
]
