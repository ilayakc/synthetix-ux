"""Docker healthcheck icin: worker'in Redis kuyruguna erisebildigini dogrular.

Kullanim: python -m app.worker_healthcheck
Cikis kodu 0 basarili, 1 basarisiz baglantiyi belirtir.
"""

import asyncio
import sys

from app.redis_client import check_redis_connection


async def main() -> int:
    ok = await check_redis_connection()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
