"""`network_device_test` gelismis modulu icin analyzer istemcisi.

`app.services.page_analysis._call_analyzer` ile ayni desen: analyzer
container'indaki (gercek Playwright, SSRF-guvenli) `/internal/analyze-device-network`
uc noktasini HTTP uzerinden cagirir. Bu modul, digerlerinin (kampanya CTA,
sentetik dikkat - bkz. app.engine.advanced_modules) aksine SENTETIK degildir;
analyzer'in urettigi sonuc, farkli cihaz/ag profillerinde gercekten olculmus
yukleme sureleri/erisilebilirlik ihlal sayilaridir (bkz.
analyzer/app/schemas.py NETWORK_DEVICE_DISCLAIMER).

Analyzer'a hic ulasilamamasi (baglanti hatasi, HTTP hata kodu, zaman asimi)
`ModuleProcessingError` olarak yukari firlatilir; `app.services.
simulation_worker.process_run` bunu `SimulationInputError` ile ayni sekilde
ele alir (tum run 'failed', rezervasyon serbest birakilir - Chip haksiz
tuketilmez). Buna karsin sabit profil kumesindeki TEK BIR profilin
basarisiz olmasi (ör. o profilde sayfa zaman asimina ugramasi) bir
"ModuleProcessingError" DEGILDIR - bu, modulun kendi `error_rate` metriginin
parcasi olan, beklenen/dokumante edilmis bir kismi sonuctur.
"""

from __future__ import annotations

import httpx

from app.config import settings
from app.services.exceptions import ModuleProcessingError

NETWORK_DEVICE_PROFILE_KEYS: tuple[str, ...] = (
    "desktop_broadband",
    "mobile_4g",
    "mobile_slow_3g",
    "tablet_wifi",
    "desktop_constrained_wifi",
)


async def run_network_device_test(url: str, *, client: httpx.AsyncClient | None = None) -> dict:
    """Verilen URL icin sabit cihaz/ag profil kumesini analyzer uzerinden olcer.

    Donen sozluk, `SimulationRun.result["modules"]["network_device_test"]`
    altina dogrudan yazilabilecek, salt JSON-uyumlu bir yapidir.
    """

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient()

    try:
        response = await client.post(
            f"{settings.analyzer_base_url}/internal/analyze-device-network",
            json={
                "url": url,
                "authorization_confirmed": True,
                "profile_keys": list(NETWORK_DEVICE_PROFILE_KEYS),
            },
            headers={"X-Analyzer-Token": settings.analyzer_shared_token},
            timeout=settings.analyzer_device_network_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise ModuleProcessingError(f"analyzer'a ulasilamadi (network_device_test): {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code != 200:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ModuleProcessingError(f"network_device_test analyzer hatasi ({response.status_code}): {detail}")

    payload = response.json()
    return {
        "module_key": "network_device_test",
        "analyzer_version": payload["analyzer_version"],
        "url": payload["url"],
        "profiles": payload["results"],
        "error_rate": payload["error_rate"],
        "warnings": payload.get("warnings", []),
        "disclaimer": payload["disclaimer"],
    }


__all__ = ["NETWORK_DEVICE_PROFILE_KEYS", "run_network_device_test"]
