"""Auth cookie'lerinin adlari ve ayarlanmasi/temizlenmesi icin yardimcilar.

Uc cookie kullanilir:
- `access_token`: HttpOnly, kisa omurlu JWT.
- `refresh_token`: HttpOnly, uzun omurlu opak token; yalnizca refresh/logout
  yollariyla sinirlandirilir (`path`) ki diger her istekte gonderilmesin.
- `csrf_token`: HttpOnly DEGIL (JS tarafindan okunabilir); double-submit
  CSRF deseni icin kullanilir (bkz. `app.dependencies.verify_csrf`).

`SameSite`/`Secure` davranisi ortama gore degisir: yerel gelistirmede
(http://localhost) `Secure=False` ve `SameSite=Lax` kullanilir (tarayicilar
Secure olmayan cookie'lerde `SameSite=None`'a izin vermez); gercek bir
uretim/staging ortaminda (`ENVIRONMENT != development`, https arkasinda)
`Secure=True` olur ve frontend/backend farkli origin'lerdeyse `SameSite=None`
gerekebilir - bu durumda `COOKIE_SECURE=true` ortam degiskeni ayarlanmalidir.
"""

from typing import Literal

from fastapi import Response

from app.config import settings

ACCESS_TOKEN_COOKIE = "access_token"
REFRESH_TOKEN_COOKIE = "refresh_token"
CSRF_TOKEN_COOKIE = "csrf_token"

# Anonim ziyaretci analitigi cookie'leri (bkz. app.services.analytics,
# app.routers.analytics). HttpOnly'dir: JS tarafindan okunamaz/degistirilemez -
# visitor kimligi yalnizca sunucu tarafinda uretilir ve dogrulanir. Kimlik
# dogrulama tasimaz; salt-okunur analitik iliskilendirme icindir.
ANALYTICS_VISITOR_COOKIE = "analytics_vid"
ANALYTICS_SESSION_COOKIE = "analytics_vsid"

REFRESH_COOKIE_PATH = "/api/auth"


def _same_site() -> Literal["none", "lax"]:
    return "none" if settings.resolved_cookie_secure else "lax"


def set_access_token_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        secure=settings.resolved_cookie_secure,
        samesite=_same_site(),
        domain=settings.cookie_domain,
        path="/",
    )


def set_refresh_token_cookie(response: Response, token: str, max_age_seconds: int) -> None:
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE,
        value=token,
        max_age=max_age_seconds,
        httponly=True,
        secure=settings.resolved_cookie_secure,
        samesite=_same_site(),
        domain=settings.cookie_domain,
        path=REFRESH_COOKIE_PATH,
    )


def set_csrf_token_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=CSRF_TOKEN_COOKIE,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=False,
        secure=settings.resolved_cookie_secure,
        samesite=_same_site(),
        domain=settings.cookie_domain,
        path="/",
    )


def set_analytics_visitor_cookie(response: Response, visitor_id: str) -> None:
    response.set_cookie(
        key=ANALYTICS_VISITOR_COOKIE,
        value=visitor_id,
        max_age=settings.analytics_cookie_ttl_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.resolved_cookie_secure,
        samesite=_same_site(),
        domain=settings.cookie_domain,
        path="/",
    )


def set_analytics_session_cookie(response: Response, session_id: str) -> None:
    # Oturum (session-scoped) cookie: max_age verilmez, tarayici kapaninca
    # dusmesi beklenir; hareketsizlik penceresi sunucu tarafinda da uygulanir
    # (bkz. app.services.analytics.SESSION_IDLE_MINUTES).
    response.set_cookie(
        key=ANALYTICS_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=settings.resolved_cookie_secure,
        samesite=_same_site(),
        domain=settings.cookie_domain,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(key=ACCESS_TOKEN_COOKIE, path="/", domain=settings.cookie_domain)
    response.delete_cookie(key=REFRESH_TOKEN_COOKIE, path=REFRESH_COOKIE_PATH, domain=settings.cookie_domain)
    response.delete_cookie(key=CSRF_TOKEN_COOKIE, path="/", domain=settings.cookie_domain)
