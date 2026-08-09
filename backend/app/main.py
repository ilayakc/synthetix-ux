from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.config import settings as default_settings
from app.config_security import validate_production_secrets
from app.db import engine, get_session
from app.logging_config import configure_logging
from app.logging_middleware import install_request_logging
from app.redis_client import check_redis_connection
from app.routers.admin import router as admin_router
from app.routers.ai_explanations import router as ai_explanations_router
from app.routers.analysis_modules import router as analysis_modules_router
from app.routers.auth import router as auth_router
from app.routers.billing import router as billing_router
from app.routers.design_assets import router as design_assets_router
from app.routers.design_generations import router as design_generations_router
from app.routers.page_analysis import router as page_analysis_router
from app.routers.personas import router as personas_router
from app.routers.projects import router as projects_router
from app.routers.reports import router as reports_router
from app.routers.settings import router as settings_router
from app.routers.simulations import router as simulations_router
from app.routers.test_wizard import router as test_wizard_router

# Modul importunda BIR KEZ yapilandirilir (gercek calisma zamani ortamina
# gore) - `create_app()`'in kendisi degil, cunku bu fabrika testlerde farkli
# `Settings` ile TEKRAR TEKRAR cagrilir ve log formati her cagrida
# degismemelidir (bkz. app.logging_config.configure_logging idempotentligi).
configure_logging(
    default_settings.environment,
    log_level=default_settings.log_level,
    log_format=default_settings.log_format,
)

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    # React'in birkac sayfada kullandigi `style={{...}}` inline stil
    # attribute'lari (bkz. ChipTopUp/Dashboard/PersonaPresets/ReportDetail/
    # Simulations/DesignSourcePicker/Step5Summary) 'unsafe-inline' olmadan
    # tarayici tarafindan engellenir; script-src'ye BU ISTISNA UYGULANMAZ.
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Uygulanan `settings.environment`'e gore FastAPI app'ini KURAR.

    Modul-seviyesinde tek bir `app` singleton'i yerine bir factory kullanilir:
    docs/redoc/openapi kapatma, `TrustedHostMiddleware` ve guvenlik header
    middleware'i app KURULURKEN (import zamaninda) `settings.environment`'e
    bakilarak eklenir/eklenmez. Bir kez kurulmus bir `FastAPI` ornegi uzerinde
    `settings.environment`'i sonradan degistirmenin (ornegin testte
    monkeypatch ile) bu davranislari ETKILEMEYECEGINI unutmayin - production
    davranisini test etmek icin HER ZAMAN bu factory'i (mevcut `settings`
    ile) yeniden cagirip TAZE bir app ornegi olusturun.
    """

    cfg = settings if settings is not None else default_settings
    is_production = cfg.environment == "production"

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Fail-closed: production'da eksik/zayif/placeholder secret'lar servis
        # vermeye baslamadan once tespit edilir (bkz. app.config_security).
        validate_production_secrets(cfg)
        yield
        # Havuzlanmis DB baglantilarini kapanista birak (bkz. asagidaki not).
        #
        # Bu olmadan (ozellikle testlerde, her `TestClient(app)` kendi event
        # loop'unu acip kapatirken) havuzdaki asyncpg baglantilari bir sonraki
        # `TestClient` ornegi farkli bir event loop'ta bu baglantilari yeniden
        # kullanmaya calisir ve "attached to a different loop" hatasiyla cokerdi.
        await engine.dispose()

    # Production'da /docs, /redoc ve /openapi.json tamamen kapatilir (route
    # hic kaydedilmez -> 404); development/test'te degisiklik yoktur.
    app = FastAPI(
        title="Synthetix UX API",
        version="0.1.0",
        lifespan=_lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    # Yalnizca yapilandirilmis frontend origin'ine izin verilir; cookie tabanli
    # kimlik dogrulama kullanildigi icin `allow_credentials=True` zorunludur
    # (bu ayarla birlikte `allow_origins=["*"]` tarayicilar tarafindan zaten
    # reddedilir, bu yuzden tek bir acik origin kullanilir).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[cfg.cors_allowed_origin],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if is_production:
        # Fail-closed dogrulama (bkz. app.config_security.validate_production_secrets)
        # ALLOWED_HOSTS'un bos/"*" olmasini zaten reddeder; burada yalnizca
        # gecerli, acikca belirtilmis host adlari middleware'e verilir. CORS'tan
        # SONRA eklenir ki (Starlette middleware stack'i son eklenenin en dista
        # olacagi sekilde kurulur) bilinmeyen bir Host header'iyla gelen istek,
        # diger hicbir middleware/route calismadan en once reddedilsin.
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[h.strip() for h in cfg.allowed_hosts.split(",") if h.strip()],
        )

        # Tek, celiskisiz bir guvenlik header seti - yalnizca production'da ve
        # yalnizca backend (API) cevaplarinda eklenir. Statik frontend dosyalari
        # (index.html/js/css) bu middleware'den GECMEZ - ayni header seti orada
        # nginx tarafindan eklenir (bkz. frontend/nginx.prod.conf); iki katman da
        # AYNI yolu (proxy edilen /api istekleri) ikinci kez isaretlemez, boylece
        # duplicate/celiskili header uretilmez.
        @app.middleware("http")
        async def _security_headers_middleware(request: Request, call_next) -> Response:
            response = await call_next(request)
            response.headers["Content-Security-Policy"] = _CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
            return response

    # En son eklenir ki (Starlette middleware yigini "son eklenen en dista"
    # sirasiyla kurulur) CORS/TrustedHost/guvenlik-header katmanlarini da
    # SARSIN - boylece bu katmanlar tarafindan reddedilen istekler de
    # (ornekin bilinmeyen bir Host header'i) suresi/durumuyla loglanir.
    install_request_logging(app, cfg)

    app.include_router(admin_router)
    app.include_router(ai_explanations_router)
    app.include_router(analysis_modules_router)
    app.include_router(auth_router)
    app.include_router(billing_router)
    app.include_router(design_assets_router)
    app.include_router(design_generations_router)
    app.include_router(page_analysis_router)
    app.include_router(personas_router)
    app.include_router(projects_router)
    app.include_router(reports_router)
    app.include_router(settings_router)
    app.include_router(simulations_router)
    app.include_router(test_wizard_router)

    @app.get("/api/health")
    async def health() -> dict:
        """Liveness kontrolu: sadece sürecin ayakta oldugunu dogrular."""
        return {"status": "ok"}

    async def _check_db_session(session: AsyncSession) -> bool:
        try:
            await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    @app.get("/api/ready")
    async def ready(session: AsyncSession = Depends(get_session)) -> JSONResponse:
        """Readiness kontrolu: PostgreSQL ve Redis baglantilarini ayri ayri raporlar."""
        db_ok = await _check_db_session(session)
        redis_ok = await check_redis_connection()
        is_ready = db_ok and redis_ok

        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={
                "ready": is_ready,
                "database": "ok" if db_ok else "unavailable",
                "redis": "ok" if redis_ok else "unavailable",
                "environment": cfg.environment,
            },
        )

    return app


app = create_app()
