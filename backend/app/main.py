from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import engine, get_session
from app.redis_client import check_redis_connection
from app.routers.ai_explanations import router as ai_explanations_router
from app.routers.analysis_modules import router as analysis_modules_router
from app.routers.auth import router as auth_router
from app.routers.billing import router as billing_router
from app.routers.page_analysis import router as page_analysis_router
from app.routers.personas import router as personas_router
from app.routers.projects import router as projects_router
from app.routers.reports import router as reports_router
from app.routers.settings import router as settings_router
from app.routers.simulations import router as simulations_router
from app.routers.test_wizard import router as test_wizard_router


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    # Havuzlanmis DB baglantilarini kapanista birak (bkz. asagidaki not).
    #
    # Bu olmadan (ozellikle testlerde, her `TestClient(app)` kendi event
    # loop'unu acip kapatirken) havuzdaki asyncpg baglantilari bir sonraki
    # `TestClient` ornegi farkli bir event loop'ta bu baglantilari yeniden
    # kullanmaya calisir ve "attached to a different loop" hatasiyla cokerdi.
    await engine.dispose()


app = FastAPI(title="Synthetix UX API", version="0.1.0", lifespan=_lifespan)

# Yalnizca yapilandirilmis frontend origin'ine izin verilir; cookie tabanli
# kimlik dogrulama kullanildigi icin `allow_credentials=True` zorunludur
# (bu ayarla birlikte `allow_origins=["*"]` tarayicilar tarafindan zaten
# reddedilir, bu yuzden tek bir acik origin kullanilir).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_allowed_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ai_explanations_router)
app.include_router(analysis_modules_router)
app.include_router(auth_router)
app.include_router(billing_router)
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
            "environment": settings.environment,
        },
    )
