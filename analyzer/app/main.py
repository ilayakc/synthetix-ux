import hmac
import logging
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from app.browser import DEVICE_NETWORK_PROFILES, AnalysisError, analyze_device_network, analyze_url
from app.config import settings
from app.logging_config import configure_logging
from app.schemas import (
    AnalyzeRequest,
    DeviceNetworkAnalysisRequest,
    DeviceNetworkAnalysisResponse,
    PageFeatureSnapshotV1,
)
from app.url_safety import UnsafeUrlError

configure_logging(settings.environment)
logger = logging.getLogger("analyzer.main")

app = FastAPI(title="Synthetix UX Analyzer", version="0.1.0")


def _verify_token(x_analyzer_token: str | None) -> None:
    """Backend<->analyzer paylasilan sir dogrulamasi (sabit zamanli karsilastirma).

    Bu servis, docker compose ag'i disina bir host portu yayinlamaz (bkz.
    compose.yaml); bu token, ic ag icinde de yalnizca backend/worker'in
    cagirabildigini garanti altina alan ikinci bir savunma katmanidir.
    """

    if not x_analyzer_token or not hmac.compare_digest(x_analyzer_token, settings.analyzer_shared_token):
        raise HTTPException(status_code=401, detail="Gecersiz analyzer token")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/internal/analyze", response_model=PageFeatureSnapshotV1)
async def analyze(
    body: AnalyzeRequest,
    x_analyzer_token: str | None = Header(default=None),
) -> PageFeatureSnapshotV1 | JSONResponse:
    """Verilen URL'yi pasif olarak analiz eder (SSRF-guvenli navigasyon + axe-core on kontrol).

    Form gonderme, giris yapma veya herhangi bir yikici tiklama yapilmaz.
    """

    _verify_token(x_analyzer_token)

    if not body.authorization_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Kullanici bu URL'yi analiz etme yetkisini onaylamadan istek islenemez",
        )

    try:
        snapshot = await analyze_url(body.url)
    except UnsafeUrlError as exc:
        logger.info("URL reddedildi (SSRF/sema): %s", exc.reason)
        raise HTTPException(status_code=400, detail=exc.reason) from exc
    except AnalysisError as exc:
        logger.warning("analiz basarisiz: %s", exc.reason)
        raise HTTPException(
            status_code=502,
            detail=(
                {"code": exc.code, "message": exc.reason}
                if exc.code != "analysis_failed"
                else exc.reason
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - son care: beklenmeyen hata 500+traceback SIZDIRMAZ
        # Beklenmeyen calisma zamani hatasi (ör. Playwright/driver dahili hatasi,
        # bellek baskisi). Correlation ID ile SUNUCU tarafinda loglanir; istemciye
        # yalnizca genel, GECICI (yeniden-denenebilir) bir 502 doner - ham
        # traceback/PII/URL ASLA yansitilmaz (bkz. gorev talimati F).
        correlation_id = uuid.uuid4().hex
        logger.exception("analiz beklenmeyen hatayla basarisiz (correlation_id=%s)", correlation_id)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "analyzer_internal_error",
                "message": "Analiz beklenmeyen bir hatayla tamamlanamadi",
                "correlation_id": correlation_id,
            },
        ) from exc

    return snapshot


@app.post("/internal/analyze-device-network", response_model=DeviceNetworkAnalysisResponse)
async def analyze_device_network_endpoint(
    body: DeviceNetworkAnalysisRequest,
    x_analyzer_token: str | None = Header(default=None),
) -> DeviceNetworkAnalysisResponse | JSONResponse:
    """Verilen URL'yi sabit cihaz/ag profillerinde gercekten olcer (network_device_test modulu).

    Tek bir profilin basarisiz olmasi tum istegi dusurmez (bkz.
    `DeviceNetworkAnalysisResponse.error_rate`); yalnizca URL guvenlik
    dogrulamasi basarisiz olursa (SSRF/sema) ya da bilinmeyen bir profil
    istenirse istegin tamami reddedilir.
    """

    _verify_token(x_analyzer_token)

    if not body.authorization_confirmed:
        raise HTTPException(
            status_code=400,
            detail="Kullanici bu URL'yi analiz etme yetkisini onaylamadan istek islenemez",
        )

    unknown_profiles = [key for key in body.profile_keys if key not in DEVICE_NETWORK_PROFILES]
    if unknown_profiles:
        raise HTTPException(status_code=400, detail=f"Bilinmeyen cihaz/ag profili: {', '.join(unknown_profiles)}")

    try:
        response = await analyze_device_network(body.url, body.profile_keys)
    except UnsafeUrlError as exc:
        logger.info("URL reddedildi (SSRF/sema): %s", exc.reason)
        raise HTTPException(status_code=400, detail=exc.reason) from exc
    except AnalysisError as exc:
        logger.warning("ag/cihaz analizi basarisiz: %s", exc.reason)
        raise HTTPException(
            status_code=502,
            detail=(
                {"code": exc.code, "message": exc.reason}
                if exc.code != "analysis_failed"
                else exc.reason
            ),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - son care: beklenmeyen hata 500+traceback SIZDIRMAZ
        correlation_id = uuid.uuid4().hex
        logger.exception(
            "ag/cihaz analizi beklenmeyen hatayla basarisiz (correlation_id=%s)", correlation_id
        )
        raise HTTPException(
            status_code=502,
            detail={
                "code": "analyzer_internal_error",
                "message": "Analiz beklenmeyen bir hatayla tamamlanamadi",
                "correlation_id": correlation_id,
            },
        ) from exc

    return response
