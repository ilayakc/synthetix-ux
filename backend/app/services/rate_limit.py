"""Redis tabanli, giris denemesi icin basit sabit-pencere hiz siniri.

Anahtar, IP adresi ve normallestirilmis e-posta kombinasyonundan uretilir;
boylece hem tek bir IP'den farkli hesaplara karsi kaba kuvvet, hem de
farkli IP'lerden ayni hesaba karsi kaba kuvvet sinirlanir. Pencere asilirsa
`login_rate_limit_lockout_seconds` boyunca ek denemeler reddedilir.
"""

from redis.asyncio import Redis

from app.config import settings


def _attempts_key(identifier: str) -> str:
    return f"auth:login:attempts:{identifier}"


def _lockout_key(identifier: str) -> str:
    return f"auth:login:lockout:{identifier}"


async def is_locked_out(redis: Redis, identifier: str) -> bool:
    return bool(await redis.exists(_lockout_key(identifier)))


async def register_failed_attempt(redis: Redis, identifier: str) -> None:
    key = _attempts_key(identifier)
    attempts = await redis.incr(key)
    if attempts == 1:
        await redis.expire(key, settings.login_rate_limit_window_seconds)

    if attempts >= settings.login_rate_limit_max_attempts:
        await redis.set(
            _lockout_key(identifier),
            "1",
            ex=settings.login_rate_limit_lockout_seconds,
        )
        await redis.delete(key)


async def clear_attempts(redis: Redis, identifier: str) -> None:
    await redis.delete(_attempts_key(identifier))
    await redis.delete(_lockout_key(identifier))
