"""Uygulama genelinde tek, tutarli log yapilandirmasi.

`backend/app/main.py` (API sureci) ve `backend/app/worker.py` (arq isci
sureci) - production'da fiilen calisan iki uzun-omurlu surec - bu modulu
kullanir. Onceden yalnizca `worker.py` kendi `logging.basicConfig(level=
logging.INFO)` cagrisini yapiyordu; `main.py` (uvicorn ile calistirilan API
sureci) HICBIR sekilde kok logger'i yapilandirmiyordu - bu, `app.services.*`
icindeki `logger.info(...)` cagrilarinin (worker disinda, dogrudan API
istegi isleyen kod yollarinda) kok logger'in varsayilan seviyesi (WARNING)
nedeniyle SESSIZCE dusurulmesine yol aciyordu.

Production'da (`environment="production"`) tek satirlik JSON ciktisi
uretilir (bir log toplama/parse aracina beslenebilmesi icin); development/
test'te okunabilir duz metin formati kullanilir. Idempotenttir - ayni
surecte (ornegin testlerde `app.main` VE `app.worker` birlikte import
edildiginde) birden fazla cagri kok logger'i yeniden yapilandirmaz/handler
coglaltmaz.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

_CONFIGURED_ATTR = "_synthetix_logging_configured"


class JsonLogFormatter(logging.Formatter):
    """Bir log kaydini tek satirlik, makine tarafindan ayristirilabilir bir
    JSON nesnesine cevirir. Ham exception stack trace'i (varsa)
    `exception` alaninda duz metin olarak tasinir - ayrica bir yapiya
    ayristirilmaz (log toplama aracinin kendi ayristirmasina birakilir)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_PLAIN_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(environment: str, *, logger: logging.Logger | None = None) -> None:
    """Verilen logger'a (varsayilan: kok logger) tek bir `StreamHandler`
    (stdout) baglar.

    `environment="production"` -> `JsonLogFormatter`; aksi halde okunabilir
    duz metin. Zaten yapilandirilmissa (bu logger icin daha once
    cagrilmissa) hicbir sey yapmaz - boylece `app.main` VE `app.worker` ayni
    surecte (ornegin testlerde) import edilse bile handler coklanmaz/
    mukerrer log satiri uretilmez. `logger` parametresi yalnizca testler
    icindir (gercek kok logger'i kirletmeden davranisi dogrulamak icin);
    uygulama kodu bunu HICBIR ZAMAN gecmemelidir.
    """

    target = logger if logger is not None else logging.getLogger()
    if getattr(target, _CONFIGURED_ATTR, False):
        return

    handler = logging.StreamHandler(sys.stdout)
    if environment == "production":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter(_PLAIN_TEXT_FORMAT))

    target.setLevel(logging.INFO)
    target.addHandler(handler)
    setattr(target, _CONFIGURED_ATTR, True)


__all__ = ["JsonLogFormatter", "configure_logging"]
