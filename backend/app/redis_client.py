import redis.asyncio as redis

from app.config import settings

redis_client: redis.Redis = redis.from_url(settings.redis_url, decode_responses=True)


async def check_redis_connection() -> bool:
    """Redis baglantisinin ayakta olup olmadigini kontrol eder."""
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False
