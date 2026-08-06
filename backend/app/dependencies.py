import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.cookies import ACCESS_TOKEN_COOKIE, CSRF_TOKEN_COOKIE
from app.db import get_session
from app.logging_config import get_logger
from app.logging_utils import log_duration
from app.models.tenancy import User
from app.security import InvalidAccessTokenError, decode_access_token

_auth_logger = get_logger("auth")


@dataclass(frozen=True)
class Principal:
    """Erisim tokeninden cozumlenen, istek boyunca gecerli kimlik baglami."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


async def get_current_principal(request: Request) -> Principal:
    """Access token cookie'sini okur ve dogrulanmis kimlik baglamini dondurur.

    Organizasyon baglami artik istemcinin keyfi bir header/body girdisinden
    degil, yalnizca bu (sunucu tarafinda imzalanmis) token'dan turetilir; bu
    sayede bir istemci baska bir organizasyonun kimligini taklit edemez.
    """

    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Oturum bulunamadi")

    # Gecersiz/suresi dolmus bir token beklenen, sik karsilasilan bir durumdur
    # (ornegin sessiz `/api/auth/refresh` akisi) - bu yuzden `InvalidAccessTokenError`
    # `log_duration`'in ERROR+stack-trace yoluna DUSURULMEZ; `with` blogu
    # icinde yakalanip token dogrulama suresi yine de normal (INFO/WARNING)
    # sekilde olculur, hata `with` blogundan SONRA HTTPException'a cevrilir.
    invalid_token_error: InvalidAccessTokenError | None = None
    payload: dict[str, Any] = {}
    with log_duration(
        _auth_logger, "auth", "verify token", slow_threshold_ms=settings.slow_request_threshold_ms
    ):
        try:
            payload = decode_access_token(token)
        except InvalidAccessTokenError as exc:
            invalid_token_error = exc

    if invalid_token_error is not None:
        raise HTTPException(
            status_code=401, detail="Oturum gecersiz veya suresi dolmus"
        ) from invalid_token_error

    try:
        return Principal(
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            role=payload["role"],
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Oturum gecersiz") from exc


async def get_organization_id(
    principal: Principal = Depends(get_current_principal),
) -> uuid.UUID:
    """Aktif kimlik dogrulama baglamindan organizasyon kimligini dondurur.

    Onceki (auth-oncesi) surumde bu deger `X-Organization-Id` basligindan
    okunuyordu; imza yalnizca dependency zincirinin ic uygulamasini
    degistirdigi icin bu fonksiyonu cagiran router'lar (ornegin
    `app.routers.billing`) degismeden calismaya devam eder.
    """

    return principal.organization_id


# Rol yetki matrisi (bkz. docs/architecture.md#roller-ve-yetkiler). Roller
# artan yetki sirasina gore: viewer < analyst < admin < owner.
ROLE_HIERARCHY = ("viewer", "analyst", "admin", "owner")


def require_roles(*allowed_roles: str):
    """Verilen rollerden birine sahip olmayan istekleri 403 ile reddeden dependency uretir."""

    allowed = set(allowed_roles)

    async def _check(principal: Principal = Depends(get_current_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status_code=403, detail="Bu islem icin yetkiniz yok")
        return principal

    return _check


async def require_platform_admin(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """Platform yoneticisi (site-genelinde, organizasyon-BAGIMSIZ) yetkisini zorlar.

    `Membership.role` (owner/admin/analyst/viewer) organizasyon-ICI bir roldur
    ve bu kontrolle KARISTIRILMAZ - bir organizasyonun `owner`'i bu dependency'den
    GECMEZ. Platform yoneticiligi tek kaynagi `users.is_platform_admin`
    kolonudur; JWT icinde boyle bir claim OLMADIGI ve OLMAYACAGI icin (bkz.
    `app.security.create_access_token`) bu deger her cagrida DOGRUDAN
    veritabanindan okunur - boylece bir kullanicinin yetkisi geri alindiginda,
    halen gecerli/suresi dolmamis eski bir access token ile bile ERISIM ANINDA
    KESILIR (token TTL'i boyunca degil).
    """

    user = await session.get(User, principal.user_id)
    if user is None or not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Bu islem icin yetkiniz yok")
    return principal


async def verify_csrf(request: Request, x_csrf_token: str | None = Header(default=None)) -> None:
    """Cift-gonderim (double-submit) CSRF kontrolu.

    Cookie tabanli kimlik dogrulama, tarayicinin ayni siteye ait olmayan
    istemlerde de cookie'leri otomatik gondermesi nedeniyle CSRF'e acik
    olabilir. Bu kontrol, `csrf_token` cookie'sindeki degerin istekte ayrica
    bir header olarak da gonderilmesini zorunlu kilar; saldirgan bir site
    cross-origin bir istekte bu cookie'nin degerini JavaScript ile okuyup
    header'a ekleyemez (ayni-orijin politikasi).
    """

    cookie_value = request.cookies.get(CSRF_TOKEN_COOKIE)
    if not cookie_value or not x_csrf_token or cookie_value != x_csrf_token:
        raise HTTPException(status_code=403, detail="CSRF dogrulamasi basarisiz")
