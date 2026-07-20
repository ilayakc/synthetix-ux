import logging

from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.services import page_analysis as page_analysis_service
from app.services.simulation_worker import run_queue_cycle, run_reap_cycle

logger = logging.getLogger("synthetix.worker")
logging.basicConfig(level=logging.INFO)


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


async def process_queued_page_analyses(ctx: dict) -> None:
    """Bekleyen URL analiz islerini alir ve isler (bkz. app.services.page_analysis)."""
    await page_analysis_service.run_queue_cycle()


async def reap_stale_page_analyses(ctx: dict) -> None:
    """'running' durumunda takili kalmis analiz islerini kurtarir."""
    await page_analysis_service.run_reap_cycle()


async def purge_expired_page_analysis_screenshots(ctx: dict) -> None:
    """Saklama suresi dolmus analiz ekran goruntusu verilerini siler (bkz. docs/security.md)."""
    await page_analysis_service.run_purge_cycle()


async def on_startup(ctx: dict) -> None:
    logger.info("worker basladi (redis_url=%s)", settings.redis_url)


async def on_shutdown(ctx: dict) -> None:
    logger.info("worker kapatiliyor")


class WorkerSettings:
    functions = [
        ping_redis,
        process_queued_simulations,
        reap_stale_simulations,
        process_queued_page_analyses,
        reap_stale_page_analyses,
        purge_expired_page_analysis_screenshots,
    ]
    cron_jobs = [
        cron(ping_redis, second={0, 30}),
        # Sentetik simulasyon motoru: bekleyen isleri sik araliklarla isler
        # (gercek zamanli bir kuyruk tuketicisi degil, basit ve gozlemlenebilir
        # bir polling cron'u - bkz. docs/architecture.md "arq" secimi).
        cron(process_queued_simulations, second=set(range(0, 60, 3))),
        cron(reap_stale_simulations, second={0, 15, 30, 45}),
        # URL analiz servisi (analyzer): ayni polling cron deseni.
        cron(process_queued_page_analyses, second=set(range(1, 60, 3))),
        cron(reap_stale_page_analyses, second={5, 20, 35, 50}),
        cron(purge_expired_page_analysis_screenshots, second={10}, minute=set(range(0, 60, 10))),
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = on_startup
    on_shutdown = on_shutdown
