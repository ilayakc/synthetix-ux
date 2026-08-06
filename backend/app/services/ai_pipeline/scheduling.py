"""Ince arq zamanlama katmani: AI pipeline worker cekirdegini (bkz.
`app.services.ai_pipeline.worker`) mevcut arq/cron polling sistemine baglar
(Faz 3B.2D).

Bu modul YALNIZCA zamanlama/enjeksiyon/gozlemlenebilirlik ekler; retry, CAS,
DAG, stale recovery ve stage is mantigini cekirdek modul saglar - burada
KOPYALANMAZ. Iki ince "cycle" fonksiyonu vardir:

- `run_ai_pipeline_stage_cycle`: en fazla BIR QUEUED stage'i isler
  (`process_one_ai_stage`i YALNIZCA BIR KEZ cagirir; sonsuz loop/sleep YOK).
  Provider, ctx icine ACIKCA enjekte edilmelidir - bu fazda gercek bir provider
  adaptoru YOKTUR; ctx'de provider yoksa hicbir stage claim edilmez, hicbir sey
  FAILED yapilmaz, guvenli bir "disabled" sonucu doner (kayitlar QUEUED kalir).
- `run_ai_pipeline_reap_cycle`: `reap_stale_ai_stages` cekirdegini cagirir;
  provider GEREKTIRMEZ (stale RUNNING stage'leri yeniden kuyruga alir/FAILED
  yapar) ve retry/stale sabitlerini (`retry_policy`) dogrudan cekirdegin
  kullanmasina birakir.

Provider context sozlesmesi: provider, TEK ve ACIK bir ctx anahtari
(`AI_PIPELINE_PROVIDER_CTX_KEY`) ile tasinir. String literal HICBIR yere
dagitilmaz; bu tek sabit kullanilir. Worker baslangicinda gercek provider
OTOMATIK OLUSTURULMAZ; `MockAIProvider`e otomatik dusulmez - Mock yalnizca
test/dev kodu tarafindan ctx'e acikca enjekte edilir.
"""

from __future__ import annotations

import time
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import async_session_maker
from app.logging_config import get_logger
from app.services.ai_pipeline.lifecycle import (
    DEFAULT_INIT_GROUP_LIMIT,
    DEFAULT_SETTLEMENT_GROUP_LIMIT,
    InitializationCycleResult,
    SettlementCycleResult,
    run_ai_pipeline_initialization_cycle,
    run_ai_reservation_settlement_cycle,
)
from app.services.ai_pipeline.provider import AIProvider
from app.services.ai_pipeline.worker import (
    AIStageProcessResult,
    StaleRecoveryResult,
    process_one_ai_stage,
    reap_stale_ai_stages,
)

logger = get_logger("ai_pipeline_worker")

# --- Provider context sozlesmesi (TEK, ACIK anahtar) -----------------------------

# arq `ctx` icinde AI provider'in tasinacagi TEK anahtar. Bu string literal
# baska bir dosyaya DAGITILMAZ; provider'i enjekte eden (test/dev/gelecekteki
# bootstrap) ve okuyan tum kod bu sabiti kullanir.
AI_PIPELINE_PROVIDER_CTX_KEY: Final = "ai_pipeline_provider"

# Kararli, benzersiz task/log adlari (guvenli ozet ve log metadata'sinda kullanilir).
PROCESSOR_TASK_NAME: Final = "process_ai_pipeline_stage"
REAPER_TASK_NAME: Final = "reap_stale_ai_pipeline_stages"
# Faz 3C.2B2: lifecycle (initialization/settlement) arq wrapper task adlari.
INITIALIZATION_TASK_NAME: Final = "initialize_ai_pipeline_groups"
SETTLEMENT_TASK_NAME: Final = "settle_ai_reservations"


class InvalidCtxProviderError(Exception):
    """ctx'deki provider degeri mevcut ama `AIProvider` sozlesmesine uymuyorsa
    (yanlis tip/deger) firlatilir. KONTROLLU bir configuration durumudur: stage
    CLAIM EDILMEDEN fark edilir ve kontrollu bir "misconfigured" sonucuna cevrilir."""


def resolve_ctx_provider(ctx: dict[str, object]) -> AIProvider | None:
    """ctx'den AI provider'i (varsa) guvenli sekilde cozer.

    - Anahtar yok / `None`: `None` doner (== disabled; henuz gercek adapter yok).
    - Mevcut ve `AIProvider` sozlesmesine uygun: provider doner.
    - Mevcut ama yanlis tip: `InvalidCtxProviderError` (stage CLAIM EDILMEZ).

    HICBIR kosulda otomatik `MockAIProvider` olusturmaz / ortam degiskeni okumaz.
    """

    provider = ctx.get(AI_PIPELINE_PROVIDER_CTX_KEY)
    if provider is None:
        return None
    if not isinstance(provider, AIProvider):
        raise InvalidCtxProviderError(
            f"ctx['{AI_PIPELINE_PROVIDER_CTX_KEY}'] AIProvider sozlesmesine uymuyor"
        )
    return provider


def _disabled_result(*, reason: str) -> dict[str, object]:
    """Provider yok/yanlis oldugunda donen guvenli, kucuk sonuc (hicbir stage
    claim edilmedi, hicbir sey FAILED yapilmadi)."""

    return {
        "task": PROCESSOR_TASK_NAME,
        "status": "disabled",
        "reason": reason,
        "claimed": False,
    }


def _summarize_process_result(result: AIStageProcessResult) -> dict[str, object]:
    """Cekirdek `AIStageProcessResult`u kucuk, guvenli (secretsiz) bir task
    ozetine cevirir - yalnizca kararli tanimlayicilar ve kisa kodlar."""

    return {
        "task": PROCESSOR_TASK_NAME,
        "status": "processed",
        "outcome": result.outcome.value,
        "pipeline_id": str(result.pipeline_id) if result.pipeline_id is not None else None,
        "stage_id": str(result.stage_id) if result.stage_id is not None else None,
        "stage_type": result.stage_type.value if result.stage_type is not None else None,
        "batch_index": result.batch_index,
        "error_code": result.error_code,
        "pipeline_completed": result.pipeline_completed,
    }


async def run_ai_pipeline_stage_cycle(
    ctx: dict[str, object],
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
) -> dict[str, object]:
    """En fazla BIR AI pipeline stage'ini isler (arq processor cycle).

    Provider ctx'e ACIKCA enjekte edilmelidir (`AI_PIPELINE_PROVIDER_CTX_KEY`).
    Provider yok/yanlis tip ise: hicbir stage claim edilmez, hicbir sey FAILED
    yapilmaz, guvenli bir disabled/misconfigured sonucu doner (kayitlar QUEUED
    kalir; provider sonradan enjekte edilince kaldigi yerden islenebilir).

    Sonsuz loop/sleep ICERMEZ; `process_one_ai_stage`i YALNIZCA BIR KEZ cagirir;
    stage/DAG/retry mantigini UYGULAMAZ (cekirdege devreder).
    """

    try:
        provider = resolve_ctx_provider(ctx)
    except InvalidCtxProviderError:
        # Yanlis provider context tipi: stage CLAIM EDILMEDEN kontrollu sonuc.
        # Yalnizca dusuk seviyeli, secretsiz bir debug logu (traceback YOK).
        logger.debug(
            "ai pipeline stage processor: gecersiz provider context tipi, atlaniyor",
            extra={"task": PROCESSOR_TASK_NAME, "outcome": "misconfigured"},
        )
        return _disabled_result(reason="invalid_provider_context")

    if provider is None:
        # Provider yapilandirilmamis: bu beklenen/kontrollu bir durumdur (henuz
        # gercek adapter yok). Traceback LOGLANMAZ; yalnizca debug.
        logger.debug(
            "ai pipeline stage processor: provider yok, atlaniyor (kayitlar QUEUED kalir)",
            extra={"task": PROCESSOR_TASK_NAME, "outcome": "disabled"},
        )
        return _disabled_result(reason="no_provider")

    started = time.perf_counter()
    try:
        result = await process_one_ai_stage(session_maker, provider=provider)
    except Exception:
        # Beklenmeyen altyapi (infra) hatasi: guvenli context ile logla ve arq'nun
        # hata gorunurlugunu kaybetmemek icin YENIDEN FIRLAT (stage RUNNING kalir;
        # stale reaper sonradan kurtarir). Beklenen domain sonuclari buraya
        # DUSMEZ - onlar `process_one_ai_stage` icinde sonuc olarak donulur.
        logger.exception(
            "ai pipeline stage processor beklenmeyen hata",
            extra={"task": PROCESSOR_TASK_NAME},
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "ai pipeline stage islendi",
        extra={
            "task": PROCESSOR_TASK_NAME,
            "outcome": result.outcome.value,
            "pipeline_id": str(result.pipeline_id) if result.pipeline_id is not None else None,
            "stage_id": str(result.stage_id) if result.stage_id is not None else None,
            "stage_type": result.stage_type.value if result.stage_type is not None else None,
            "batch_index": result.batch_index,
            "pipeline_completed": result.pipeline_completed,
            "duration_ms": duration_ms,
        },
    )
    return _summarize_process_result(result)


def _summarize_reap_result(result: StaleRecoveryResult) -> dict[str, object]:
    return {
        "task": REAPER_TASK_NAME,
        "scanned": result.scanned,
        "requeued": result.requeued,
        "failed": result.failed,
        "cancelled": result.cancelled,
    }


async def run_ai_pipeline_reap_cycle(
    ctx: dict[str, object],
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
) -> dict[str, object]:
    """Stale RUNNING AI pipeline stage'lerini kurtarir (arq reaper cycle).

    Provider GEREKTIRMEZ ve provider yapilandirilmasa da calisir; yeni bir
    provider cagrisi OLUSTURMAZ. Stale timeout / maksimum deneme mantigini
    KOPYALAMAZ - `reap_stale_ai_stages` cekirdegi `retry_policy` sabitlerini
    dogrudan kullanir. Kisa/guvenli bir ozet doner; raw stage output veya hata
    metni LOGLAMAZ.
    """

    started = time.perf_counter()
    try:
        result = await reap_stale_ai_stages(session_maker)
    except Exception:
        # Beklenmeyen infra hatasi: guvenli logla + yeniden firlat (sessizce yutma).
        logger.exception(
            "ai pipeline stale reaper beklenmeyen hata",
            extra={"task": REAPER_TASK_NAME},
        )
        raise

    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "ai pipeline stale reaper calisti",
        extra={
            "task": REAPER_TASK_NAME,
            "scanned": result.scanned,
            "requeued": result.requeued,
            "failed": result.failed,
            "cancelled": result.cancelled,
            "duration_ms": duration_ms,
        },
    )
    return _summarize_reap_result(result)


# --- Faz 3C.2B2: lifecycle (initialization/settlement) ince arq wrapper'lari ----
#
# Ikisi de `app.services.ai_pipeline.lifecycle`daki cekirdek cycle
# fonksiyonunu YALNIZCA BIR KEZ cagirir; provider ctx GEREKTIRMEZ (ikisi de
# hicbir provider/network cagrisi yapmaz - bkz. lifecycle modul dokstring'i),
# Mock OLUSTURMAZ, stage/DAG mantigini KOPYALAMAZ. Sonsuz drain loop YOKTUR.


def _summarize_initialization_result(result: InitializationCycleResult) -> dict[str, object]:
    return {
        "task": INITIALIZATION_TASK_NAME,
        "groups_scanned": result.groups_scanned,
        "groups_initialized": result.groups_initialized,
        "pipelines_initialized": result.pipelines_initialized,
        "groups_not_ready": result.groups_not_ready,
        "groups_permanent_error": result.groups_permanent_error,
        "duration_ms": result.duration_ms,
    }


async def run_ai_pipeline_initialization_job(
    ctx: dict[str, object],
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    limit: int = DEFAULT_INIT_GROUP_LIMIT,
) -> dict[str, object]:
    """Bekleyen AI pipeline initialization'larini isleyen arq cycle wrapper'i
    (bkz. `app.services.ai_pipeline.lifecycle.run_ai_pipeline_initialization_cycle`).

    Provider GEREKTIRMEZ, ctx'ten hicbir sey OKUMAZ (yalnizca arq imzasi icin
    parametre olarak alinir). Beklenmeyen hata guvenli loglanir ve arq
    gorunurlugu icin YENIDEN FIRLATILIR (sessizce yutulmaz)."""

    try:
        result = await run_ai_pipeline_initialization_cycle(session_maker, limit=limit)
    except Exception:
        logger.exception(
            "ai pipeline initialization job beklenmeyen hata",
            extra={"task": INITIALIZATION_TASK_NAME},
        )
        raise

    summary = _summarize_initialization_result(result)
    logger.info("ai pipeline initialization job tamamlandi", extra=summary)
    return summary


def _summarize_settlement_result(result: SettlementCycleResult) -> dict[str, object]:
    return {
        "task": SETTLEMENT_TASK_NAME,
        "groups_scanned": result.groups_scanned,
        "groups_consumed": result.groups_consumed,
        "groups_released": result.groups_released,
        "groups_waiting": result.groups_waiting,
        "groups_skipped_integrity": result.groups_skipped_integrity,
        "duration_ms": result.duration_ms,
    }


async def run_ai_reservation_settlement_job(
    ctx: dict[str, object],
    *,
    session_maker: async_sessionmaker[AsyncSession] = async_session_maker,
    limit: int = DEFAULT_SETTLEMENT_GROUP_LIMIT,
) -> dict[str, object]:
    """AI Chip rezervasyon settlement'ini isleyen arq cycle wrapper'i (bkz.
    `app.services.ai_pipeline.lifecycle.run_ai_reservation_settlement_cycle`).

    Provider GEREKTIRMEZ, pipeline stage CALISTIRMAZ. Beklenmeyen hata guvenli
    loglanir ve arq gorunurlugu icin YENIDEN FIRLATILIR."""

    try:
        result = await run_ai_reservation_settlement_cycle(session_maker, limit=limit)
    except Exception:
        logger.exception(
            "ai reservation settlement job beklenmeyen hata",
            extra={"task": SETTLEMENT_TASK_NAME},
        )
        raise

    summary = _summarize_settlement_result(result)
    logger.info("ai reservation settlement job tamamlandi", extra=summary)
    return summary


__all__ = [
    "AI_PIPELINE_PROVIDER_CTX_KEY",
    "PROCESSOR_TASK_NAME",
    "REAPER_TASK_NAME",
    "INITIALIZATION_TASK_NAME",
    "SETTLEMENT_TASK_NAME",
    "InvalidCtxProviderError",
    "resolve_ctx_provider",
    "run_ai_pipeline_stage_cycle",
    "run_ai_pipeline_reap_cycle",
    "run_ai_pipeline_initialization_job",
    "run_ai_reservation_settlement_job",
]
