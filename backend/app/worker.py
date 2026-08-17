import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.config_security import validate_production_secrets
from app.logging_config import configure_logging
from app.services import analytics as analytics_service
from app.services import design_assets as design_assets_service
from app.services import design_generation as design_generation_service
from app.services import page_analysis as page_analysis_service
from app.services.ai_interaction_heatmap.openai_selector import (
    INTERACTION_HEATMAP_SELECTOR_CTX_KEY,
    InteractionHeatmapOpenAISelector,
    MockInteractionHeatmapSelector,
)
from app.services.ai_interaction_heatmap.worker import (
    run_interaction_heatmap_queue_cycle,
    run_interaction_heatmap_reap_cycle,
    run_interaction_heatmap_settlement_cycle,
)
from app.services.ai_pipeline.mock_provider import MockAIProvider
from app.services.ai_pipeline.ollama_provider import OllamaProvider
from app.services.ai_pipeline.openai_provider import OpenAIProvider
from app.services.ai_pipeline.scheduling import (
    AI_PIPELINE_PROVIDER_CTX_KEY,
    run_ai_pipeline_initialization_job,
    run_ai_pipeline_reap_cycle,
    run_ai_pipeline_stage_cycle,
    run_ai_reservation_settlement_job,
)
from app.services.simulation_worker import run_fail_blocked_cycle, run_queue_cycle, run_reap_cycle
from app.worker_constants import ARQ_JOB_TIMEOUT_SECONDS

configure_logging(
    settings.environment,
    log_level=settings.log_level,
    log_format=settings.log_format,
)
logger = logging.getLogger("synthetix.worker")


async def ping_redis(ctx: dict) -> bool:
    """Zararsiz dogrulama gorevi: worker'in Redis kuyruguna baglandigini kanitlar."""
    redis = ctx["redis"]
    pong = await redis.ping()
    logger.info("worker redis ping: %s", pong)
    return bool(pong)


async def process_queued_simulations(ctx: dict) -> None:
    """Bekleyen sentetik simulasyon islerini alir ve isler (bkz. app.services.simulation_worker)."""
    await run_queue_cycle()


async def reap_stale_simulations(ctx: dict) -> None:
    """'running' durumunda takili kalmis isleri kurtarir (bkz. app.services.simulation_worker)."""
    await run_reap_cycle()


async def fail_simulations_blocked_by_page_analysis(ctx: dict) -> None:
    """Bagli PageAnalysis'i FAILED olan QUEUED run'lari FAILED yapar (Paket 4B,
    bkz. app.services.simulation_worker.fail_runs_blocked_by_failed_page_analysis)."""
    await run_fail_blocked_cycle()


async def process_queued_page_analyses(ctx: dict) -> None:
    """Bekleyen URL analiz islerini alir ve isler (bkz. app.services.page_analysis)."""
    await page_analysis_service.run_queue_cycle()


async def reap_stale_page_analyses(ctx: dict) -> None:
    """'running' durumunda takili kalmis analiz islerini kurtarir."""
    await page_analysis_service.run_reap_cycle()


async def purge_expired_page_analysis_screenshots(ctx: dict) -> None:
    """Saklama suresi dolmus analiz ekran goruntusu verilerini siler (bkz. docs/security.md)."""
    await page_analysis_service.run_purge_cycle()


async def purge_expired_design_assets(ctx: dict) -> None:
    """Saklama suresi dolmus, kullanici tarafindan yuklenen tasarim gorseli verilerini siler."""
    await design_assets_service.run_purge_cycle()


async def process_queued_design_generations(ctx: dict) -> None:
    """Bekleyen AI tasarim uretim islerini alir ve isler (bkz. app.services.design_generation)."""
    await design_generation_service.run_queue_cycle()


async def reap_stale_design_generations(ctx: dict) -> None:
    """'running' durumunda takili kalmis AI tasarim uretim islerini kurtarir."""
    await design_generation_service.run_reap_cycle()


async def purge_expired_design_generations(ctx: dict) -> None:
    """Saklama suresi dolmus AI tasarim uretim islerinin prompt metnini temizler."""
    await design_generation_service.run_purge_cycle()


async def purge_expired_analytics(ctx: dict) -> None:
    """Saklama suresi (`ANALYTICS_RETENTION_DAYS`) dolmus ziyaretci/trafik analitigi
    satirlarini guvenle siler (bkz. app.services.analytics.purge_expired)."""
    counts = await analytics_service.run_purge_cycle()
    if counts.events or counts.sessions or counts.visitors:
        logger.info(
            "analitik retention temizligi: events=%s sessions=%s visitors=%s",
            counts.events,
            counts.sessions,
            counts.visitors,
        )


async def process_ai_pipeline_stage_job(ctx: dict) -> dict:
    """AI pipeline: en fazla BIR QUEUED stage'i isler (bkz.
    app.services.ai_pipeline.scheduling.run_ai_pipeline_stage_cycle).

    Provider ctx'e ACIKCA enjekte edilmemisse (bu fazda gercek adapter yok)
    hicbir stage claim edilmez ve hicbir sey FAILED yapilmaz - kayitlar QUEUED
    kalir (bkz. AI_PIPELINE_PROVIDER_CTX_KEY sozlesmesi)."""
    return await run_ai_pipeline_stage_cycle(ctx)


async def reap_stale_ai_pipeline_stages_job(ctx: dict) -> dict:
    """AI pipeline: RUNNING'de takili kalmis stale stage'leri kurtarir (provider
    GEREKTIRMEZ, bkz. app.services.ai_pipeline.scheduling.run_ai_pipeline_reap_cycle)."""
    return await run_ai_pipeline_reap_cycle(ctx)


async def initialize_ai_pipeline_groups_job(ctx: dict) -> dict:
    """AI pipeline lifecycle (Faz 3C.2B2): `ai_report` secilmis, SUCCEEDED,
    henuz pipeline'i olmayan launch gruplari icin bekleyen AI pipeline
    initialization'larini isler (bkz. app.services.ai_pipeline.scheduling.
    run_ai_pipeline_initialization_job / app.services.ai_pipeline.lifecycle.
    run_ai_pipeline_initialization_cycle). Provider/network GEREKTIRMEZ."""
    return await run_ai_pipeline_initialization_job(ctx)


async def settle_ai_reservations_job(ctx: dict) -> dict:
    """AI pipeline lifecycle (Faz 3C.2B2): paylasilan AI Chip rezervasyonunu
    grubun AI pipeline sonuclarina gore CONSUMED/RELEASED yapar (bkz.
    app.services.ai_pipeline.scheduling.run_ai_reservation_settlement_job /
    app.services.ai_pipeline.lifecycle.run_ai_reservation_settlement_cycle).
    Provider/network GEREKTIRMEZ, stage CALISTIRMAZ."""
    return await run_ai_reservation_settlement_job(ctx)


async def process_interaction_heatmap_jobs(ctx: dict) -> dict:
    """AI etkilesim isi haritasi: en fazla BIR QUEUED job'i (baseline SUCCEEDED)
    isler - gercek OpenAI (veya mock) secicisi ctx'e enjekte edilmisse (bkz.
    on_startup / interaction_heatmap_provider_ready). Secici yoksa hicbir sey
    claim edilmez, hicbir sey FAILED yapilmaz (job'lar QUEUED kalir)."""
    return await run_interaction_heatmap_queue_cycle(ctx)


async def reap_stale_interaction_heatmaps_job(ctx: dict) -> dict:
    """AI etkilesim isi haritasi: RUNNING'de takili kalmis job'lari kurtarir
    (secici GEREKTIRMEZ)."""
    return await run_interaction_heatmap_reap_cycle()


async def settle_interaction_heatmap_reservations_job(ctx: dict) -> dict:
    """AI etkilesim isi haritasi: heatmap Chip rezervasyonunu job sonuclarina gore
    CONSUMED/RELEASED yapar (secici/network GEREKTIRMEZ)."""
    return await run_interaction_heatmap_settlement_cycle()


async def on_startup(ctx: dict) -> None:
    # Fail-closed: backend ile ayni dogrulama (bkz. app.config_security) -
    # production'da eksik/zayif/placeholder secret'la worker gorev almaya baslamaz.
    validate_production_secrets()
    # AI pipeline provider context sozlesmesi (Faz 3D.1 / 3D.2-LOCAL):
    # readiness FALSE ise (varsayilan) hicbir client OLUSTURULMAZ, hicbir
    # ag/health-check/model-pull cagrisi YAPILMAZ - yalnizca yapilandirma
    # dogrulanir (bkz. Settings.ai_report_provider_ready). readiness TRUE ise
    # `ai_report_provider`e gore TEK bir provider olusturulup ctx'e enjekte
    # edilir; baska bir provider'a OTOMATIK DUSULMEZ (bkz.
    # app.services.ai_pipeline.scheduling).
    # Fail-safe: provider baslatma (ozellikle `openai` SDK'sinin lazy import'u -
    # bkz. openai_provider modul basi notu) BASARISIZ olsa bile TUM worker
    # DUSMEMELIDIR. Aksi halde eksik/eski bir imajda (ornegin `openai` kurulu
    # degil) worker crash-loop'a girer, simulasyon islemcisi + reaper hic
    # calismaz ve run'lar sonsuza kadar RUNNING'de takili kalir (gozlemlenen
    # production hatasi). Provider baslatilamazsa ACIK bir ERROR loglanir
    # (gizli veri icermez), AI raporu devre disi kalir ve simulasyon isleme
    # ETKILENMEDEN devam eder.
    if settings.ai_report_provider_ready:
        try:
            if settings.ai_report_provider == "openai" and settings.openai_api_key is not None:
                ctx[AI_PIPELINE_PROVIDER_CTX_KEY] = OpenAIProvider(
                    api_key=settings.openai_api_key.get_secret_value(),
                    model=settings.openai_model,
                    reasoning_effort=settings.openai_reasoning_effort,
                    timeout_seconds=settings.openai_timeout_seconds,
                    max_output_tokens=settings.openai_max_output_tokens,
                )
                logger.info(
                    "worker basladi: OpenAI AI pipeline provider'i hazir (model=%s)",
                    settings.openai_model,
                )
            elif settings.ai_report_provider == "ollama":
                ctx[AI_PIPELINE_PROVIDER_CTX_KEY] = OllamaProvider(
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
                logger.info(
                    "worker basladi: Ollama AI pipeline provider'i hazir (model=%s)",
                    settings.ollama_model,
                )
            elif settings.ai_report_provider == "mock":
                ctx[AI_PIPELINE_PROVIDER_CTX_KEY] = MockAIProvider()
                logger.info("worker basladi: Mock AI pipeline provider'i hazir")
            else:
                ctx.setdefault(AI_PIPELINE_PROVIDER_CTX_KEY, None)
                logger.info("worker basladi (redis_url=%s)", settings.redis_url)
        except Exception:  # noqa: BLE001 - provider hatasi worker'i DUSURMEMELI
            ctx[AI_PIPELINE_PROVIDER_CTX_KEY] = None
            logger.error(
                "AI pipeline provider'i (%s) baslatilamadi; AI raporu DEVRE DISI, "
                "simulasyon isleme + reaper ETKILENMEDEN devam eder. Gerekli "
                "runtime bagimligi (ornegin 'openai') imajda kurulu mu?",
                settings.ai_report_provider,
                exc_info=True,
            )
    else:
        ctx.setdefault(AI_PIPELINE_PROVIDER_CTX_KEY, None)
        logger.info("worker basladi (redis_url=%s)", settings.redis_url)

    # AI etkilesim isi haritasi secicisi (ai_report'tan TAMAMEN AYRI ikinci kapi):
    # readiness FALSE ise (varsayilan) hicbir secici olusturulmaz, hicbir ag
    # cagrisi YAPILMAZ (bkz. Settings.interaction_heatmap_provider_ready). readiness
    # TRUE ise `ai_interaction_heatmap_provider`e gore TEK bir secici enjekte edilir;
    # baska bir seciciye OTOMATIK DUSULMEZ.
    # Ayni fail-safe (bkz. yukaridaki AI pipeline provider notu): heatmap
    # secicisi baslatilamazsa worker DUSMEZ - heatmap devre disi kalir,
    # simulasyon isleme etkilenmez.
    if settings.interaction_heatmap_provider_ready:
        try:
            if settings.ai_interaction_heatmap_provider == "openai" and settings.openai_api_key is not None:
                ctx[INTERACTION_HEATMAP_SELECTOR_CTX_KEY] = InteractionHeatmapOpenAISelector(
                    api_key=settings.openai_api_key.get_secret_value(),
                    model=settings.openai_model,
                    reasoning_effort=settings.openai_reasoning_effort,
                    timeout_seconds=settings.openai_timeout_seconds,
                    max_output_tokens=settings.openai_max_output_tokens,
                )
                logger.info(
                    "worker: OpenAI etkilesim isi haritasi secicisi hazir (model=%s)",
                    settings.openai_model,
                )
            elif settings.ai_interaction_heatmap_provider == "mock":
                ctx[INTERACTION_HEATMAP_SELECTOR_CTX_KEY] = MockInteractionHeatmapSelector()
                logger.info("worker: Mock etkilesim isi haritasi secicisi hazir")
            else:
                ctx.setdefault(INTERACTION_HEATMAP_SELECTOR_CTX_KEY, None)
        except Exception:  # noqa: BLE001 - secici hatasi worker'i DUSURMEMELI
            ctx[INTERACTION_HEATMAP_SELECTOR_CTX_KEY] = None
            logger.error(
                "Etkilesim isi haritasi secicisi (%s) baslatilamadi; heatmap DEVRE DISI, "
                "simulasyon isleme etkilenmez",
                settings.ai_interaction_heatmap_provider,
                exc_info=True,
            )
    else:
        ctx.setdefault(INTERACTION_HEATMAP_SELECTOR_CTX_KEY, None)


async def on_shutdown(ctx: dict) -> None:
    provider = ctx.get(AI_PIPELINE_PROVIDER_CTX_KEY)
    if isinstance(provider, OpenAIProvider | OllamaProvider):
        await provider.aclose()
    heatmap_selector = ctx.get(INTERACTION_HEATMAP_SELECTOR_CTX_KEY)
    if isinstance(heatmap_selector, InteractionHeatmapOpenAISelector):
        await heatmap_selector.aclose()
    logger.info("worker kapatiliyor")


class WorkerSettings:
    # arq'nin kendi 300sn'lik varsayilanina ORTUK olarak guvenmek yerine
    # ACIKCA `app.worker_constants.ARQ_JOB_TIMEOUT_SECONDS`e baglanir - bu,
    # provider timeout dogrulamasinin (bkz. app.config.Settings.
    # _ensure_provider_timeout_below_job_timeout) referans aldigi TEK
    # dogruluk kaynagidir (Faz 3D.2.1 madde 1).
    job_timeout = ARQ_JOB_TIMEOUT_SECONDS
    functions = [
        ping_redis,
        process_queued_simulations,
        reap_stale_simulations,
        fail_simulations_blocked_by_page_analysis,
        process_queued_page_analyses,
        reap_stale_page_analyses,
        purge_expired_page_analysis_screenshots,
        purge_expired_design_assets,
        purge_expired_analytics,
        process_queued_design_generations,
        reap_stale_design_generations,
        purge_expired_design_generations,
        process_ai_pipeline_stage_job,
        reap_stale_ai_pipeline_stages_job,
        initialize_ai_pipeline_groups_job,
        settle_ai_reservations_job,
        process_interaction_heatmap_jobs,
        reap_stale_interaction_heatmaps_job,
        settle_interaction_heatmap_reservations_job,
    ]
    # AI pipeline zamanlama araliklari (Faz 3B.2D): mevcut simulation/design
    # polling deseniyle AYNI mantik. Processor tek invocation'da yalnizca BIR
    # stage isler ve provider yoksa guvenle no-op doner; bu nedenle (provider/LLM
    # cagrisi ICEREBILEN design uretimindeki gibi) ~5s'lik bir polling araligi
    # referans alinir - diger job'larla ayni saniyede tetiklenmemesi icin ofset
    # (4) verilir. Reaper provider gerektirmez ve stale esigi 600s (bkz.
    # retry_policy.STALE_RUNNING_TIMEOUT_SECONDS) oldugu icin cok daha seyrek
    # (design reaper gibi 5 dakikada bir, ofset saniye 50) calisir.
    _AI_STAGE_PROCESSOR_SECONDS = set(range(4, 60, 5))
    _AI_STALE_REAPER_SECONDS = {50}
    _AI_STALE_REAPER_MINUTES = set(range(0, 60, 5))
    # AI pipeline LIFECYCLE zamanlama araliklari (Faz 3C.2B2): initialization/
    # settlement, uzun baseline-run transaction'larindan AYRI, dakikada iki
    # kez calisan, bounded/deterministik reconciliation cycle'lardir (bkz.
    # app.services.ai_pipeline.lifecycle modul dokstring'i). Saniye
    # degerleri, mevcut is'lerin (processor/reaper/simulation/page-analysis/
    # design-generation) sabit degerleriyle (0,30 / 5,20,35,50 / 7,22,37,52 /
    # 10 / 25 / 40 / 35 / 50) BIREBIR CAKISMAYACAK sekilde secilmistir - dakika
    # bazinda 3'un veya 5'in katlarini kapsayan genis `range(...)` polling
    # kumeleri (process_queued_simulations/process_queued_page_analyses/
    # process_queued_design_generations/AI stage processor) tum saniye
    # eksenini kapladigi icin (pigeonhole) TAM cakismasizlik matematiksel
    # olarak mumkun degildir - bu, arq'da GUVENLIDIR (bagimsiz, DB-kilitli
    # cron'lar ayni saniyede tetiklenebilir, birbirini engellemez); burada
    # yalnizca MEVCUT TEKIL (kucuk, literal) saniye kumeleriyle birebir
    # AYNI olmamasi saglanmistir.
    _AI_INIT_CYCLE_SECONDS = {13, 43}
    _AI_SETTLEMENT_CYCLE_SECONDS = {28, 58}
    cron_jobs = [
        cron(ping_redis, second={0, 30}),
        # Sentetik simulasyon motoru: bekleyen isleri sik araliklarla isler
        # (gercek zamanli bir kuyruk tuketicisi degil, basit ve gozlemlenebilir
        # bir polling cron'u - bkz. docs/architecture.md "arq" secimi).
        cron(process_queued_simulations, second=set(range(0, 60, 3))),
        cron(reap_stale_simulations, second={0, 15, 30, 45}),
        cron(fail_simulations_blocked_by_page_analysis, second={7, 22, 37, 52}),
        # URL analiz servisi (analyzer): ayni polling cron deseni.
        cron(process_queued_page_analyses, second=set(range(1, 60, 3))),
        cron(reap_stale_page_analyses, second={5, 20, 35, 50}),
        cron(purge_expired_page_analysis_screenshots, second={10}, minute=set(range(0, 60, 10))),
        cron(purge_expired_design_assets, second={25}, minute=set(range(0, 60, 10))),
        # Analitik retention: gunluk yeterli olurdu ama seyrek/deterministik bir
        # saatlik cron kullanilir (saat basi degil, 55. saniye + 15. dakika).
        cron(purge_expired_analytics, second={55}, minute={15}),
        # AI tasarim uretim isleri: gorsel uretimi bir sayfa analizinden cok
        # daha uzun surebilecegi icin (bkz. design_generation_stale_timeout_seconds)
        # daha seyrek bir polling araligi kullanilir.
        cron(process_queued_design_generations, second=set(range(2, 60, 5))),
        cron(reap_stale_design_generations, second={40}, minute=set(range(0, 60, 5))),
        cron(purge_expired_design_generations, second={35}, minute=set(range(0, 60, 10))),
        # AI pipeline (Faz 3B.2D): processor sik araliklarla yalnizca BIR stage
        # isler (provider yoksa guvenle no-op); reaper seyrek calisir.
        cron(process_ai_pipeline_stage_job, second=_AI_STAGE_PROCESSOR_SECONDS),
        cron(
            reap_stale_ai_pipeline_stages_job,
            second=_AI_STALE_REAPER_SECONDS,
            minute=_AI_STALE_REAPER_MINUTES,
        ),
        # AI pipeline lifecycle (Faz 3C.2B2): initialization/settlement,
        # dakikada iki kez calisan bagimsiz reconciliation cycle'lari.
        cron(initialize_ai_pipeline_groups_job, second=_AI_INIT_CYCLE_SECONDS),
        cron(settle_ai_reservations_job, second=_AI_SETTLEMENT_CYCLE_SECONDS),
        # AI etkilesim isi haritasi (ai_report'tan BAGIMSIZ lifecycle): processor
        # sik araliklarla yalnizca BIR job isler (secici yoksa guvenle no-op);
        # reaper seyrek; settlement dakikada iki kez. Bagimsiz DB-kilitli cron'lar
        # oldugu icin diger cron'larla ayni saniyede tetiklenmesi GUVENLIDIR (bkz.
        # yukaridaki AI pipeline cron aciklamasi).
        cron(process_interaction_heatmap_jobs, second=set(range(3, 60, 6))),
        cron(reap_stale_interaction_heatmaps_job, second={45}, minute=set(range(0, 60, 5))),
        cron(settle_interaction_heatmap_reservations_job, second={18, 48}),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = on_startup
    on_shutdown = on_shutdown
