import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.cookies import (
    ACCESS_TOKEN_COOKIE,
    ANALYTICS_VISITOR_COOKIE,
    REFRESH_TOKEN_COOKIE,
    clear_auth_cookies,
    set_access_token_cookie,
    set_csrf_token_cookie,
    set_refresh_token_cookie,
)
from app.db import get_session
from app.dependencies import Principal, get_current_principal, verify_csrf
from app.logging_config import get_logger
from app.models.analytics import AnalyticsEventType
from app.models.audit import USER_LOGIN_ACTION, AuditLog
from app.models.tenancy import Organization, User
from app.redis_client import redis_client
from app.security import (
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
    generate_csrf_token,
)
from app.services import analytics as analytics_service
from app.services import auth as auth_service
from app.services import rate_limit

router = APIRouter(prefix="/api/auth", tags=["auth"])

audit_logger = get_logger("audit")

# `user_agent` uzun olabilir; denetim kaydinda makul bir ust sinira kirpilir
# (refresh_tokens.user_agent 500 karakterle sinirli, burada daha da muhafazakar).
_USER_AGENT_AUDIT_MAX_LEN = 400

GENERIC_LOGIN_ERROR = "E-posta veya parola hatali"
LOCKED_OUT_ERROR = "Cok fazla basarisiz deneme yapildi. Lutfen daha sonra tekrar deneyin."
# Kullanici varligini sizdirmamak icin, e-posta kayitli olsun ya da olmasin
# ayni genel yanit dondurulur.
PASSWORD_RESET_REQUESTED_MESSAGE = (
    "E-posta adresi sistemde kayitliysa parola sifirlama talimatlari gonderildi."
)
DEMO_LOGIN_RATE_LIMITED = "Cok fazla demo girisi denendi. Lutfen biraz sonra tekrar deneyin."


class RegisterRequest(BaseModel):
    # `extra="forbid"`: bilinmeyen hicbir alan (ozellikle `is_platform_admin`
    # gibi yetki tasiyan bir alan) sessizce yok sayilmaz - istekte boyle bir
    # alan varsa 422 ile ACIKCA reddedilir. Platform yoneticiligi hicbir
    # public request govdesinden verilemez (bkz. app.models.tenancy.User.
    # is_platform_admin, app.admin_cli).
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    organization_name: str = Field(min_length=1, max_length=255)
    organization_website: AnyHttpUrl | None = None
    display_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SessionResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str | None
    organization_id: uuid.UUID
    organization_name: str
    role: str
    # Organizasyon-ici `role`den TAMAMEN AYRI: platform genelinde yonetici
    # olup olmadigi. Bu alan yalnizca UI amaclidir (ornegin bir yonetici
    # menusunu gostermek/gizlemek icin) - gercek yetkilendirme her zaman
    # `app.dependencies.require_platform_admin` uzerinden, DOGRUDAN
    # veritabanindan yapilir; bu alan hicbir backend yetki kararinin yerine
    # GECMEZ.
    is_platform_admin: bool
    is_demo: bool


class PasswordResetRequestBody(BaseModel):
    email: EmailStr


class PasswordResetRequestResponse(BaseModel):
    message: str
    # Yalnizca `ENVIRONMENT=development` iken doludur: gercek bir e-posta
    # saglayicisi baglanmadigi icin, yerel gelistirmede tokeni manuel test
    # etmeyi mumkun kilar. Uretimde bu alan her zaman `None`'dir.
    dev_reset_token: str | None = None


class PasswordResetConfirmBody(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=256)


class PasswordResetConfirmResponse(BaseModel):
    message: str


def _client_identifier(request: Request, email: str) -> str:
    client_host = request.client.host if request.client else "unknown"
    return f"{client_host}:{auth_service.normalize_email(email)}"


def _normalized_demo_email() -> str | None:
    """Yapilandirilmis herkese acik demo hesabinin normalize e-postasi (yoksa None)."""
    email = settings.demo_account_email
    return auth_service.normalize_email(email) if email else None


def _is_demo_account(user: User) -> bool:
    """Kullanici, yapilandirilmis herkese acik demo hesabi mi (demo yoksa False)."""
    demo_email = _normalized_demo_email()
    return demo_email is not None and user.email_normalized == demo_email


async def _issue_session_cookies(
    response: Response,
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role: str,
    is_demo: bool,
    user_agent: str | None,
    ip_address: str | None,
    existing_session_id: uuid.UUID | None = None,
) -> None:
    issued = await auth_service.issue_refresh_token(
        session,
        user_id=user_id,
        session_id=existing_session_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    access_token = create_access_token(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        is_demo=is_demo,
    )

    set_access_token_cookie(response, access_token)
    set_refresh_token_cookie(response, issued.raw_token, max_age_seconds=_refresh_max_age(issued))
    set_csrf_token_cookie(response, generate_csrf_token())


def _refresh_max_age(issued: auth_service.IssuedRefreshToken) -> int:
    return max(0, int((issued.expires_at - datetime.now(UTC)).total_seconds()))


def _record_login_audit(
    session: AsyncSession,
    *,
    user: User,
    organization: Organization,
    login_type: str,
    ip_address: str | None,
    user_agent: str | None,
) -> None:
    """Bir sisteme giris olayini kalici (ekle-sadece) denetim kaydina yazar.

    Kimlik/organizasyon adi gibi gosterim alanlari `entry_metadata` icinde
    denormalize saklanir; boylece kayit, kullanici/organizasyon sonradan
    silinse bile o anki durumu korur (nokta-zaman denetim kaydi). `login_type`:
    `register` (kayit), `password` (parola girisi) veya `demo` (demo giris).
    Ham parola, token veya cookie ASLA saklanmaz.
    """

    session.add(
        AuditLog(
            organization_id=organization.id,
            actor_user_id=user.id,
            action=USER_LOGIN_ACTION,
            entity_type="user",
            entity_id=user.id,
            entry_metadata={
                "email": user.email,
                "display_name": user.display_name,
                "organization_name": organization.name,
                "ip_address": ip_address,
                "user_agent": (user_agent[:_USER_AGENT_AUDIT_MAX_LEN] if user_agent else None),
                "login_type": login_type,
            },
        )
    )
    audit_logger.info(
        "Kullanici sisteme giris yapti",
        extra={
            "action": USER_LOGIN_ACTION,
            "login_type": login_type,
            "actor_user_id": str(user.id),
            "organization_id": str(organization.id),
        },
    )


async def _visitor_id(session: AsyncSession, request: Request) -> uuid.UUID | None:
    """Analitik ziyaretci cerezinden (varsa, DB'de gercekten var olan) visitor id."""
    return await analytics_service.resolve_existing_visitor_id(
        session, request.cookies.get(ANALYTICS_VISITOR_COOKIE)
    )


async def _record_login_analytics(
    session: AsyncSession, request: Request, *, user: User, organization: Organization
) -> None:
    """Basarili giris IS OLAYINI (audit'ten AYRI) analitige guvenilir sekilde yazar.

    audit_logs (guvenlik/denetim) ile KARISTIRILMAZ; bu, toplam trafik/giris
    olcumu icin ayri `analytics_events` tablosuna yazar. Cagiran taraf ayni
    transaction'da commit eder (is olaylari guvenilir kaydedilir)."""

    if not settings.analytics_enabled:
        return
    await analytics_service.insert_event(
        session,
        event_type=AnalyticsEventType.LOGIN_SUCCEEDED,
        user_id=user.id,
        organization_id=organization.id,
        visitor_id=await _visitor_id(session, request),
    )


async def _record_signup_analytics(
    session: AsyncSession, request: Request, *, user: User, organization: Organization
) -> None:
    """Kayit + organizasyon olusturma + ilk giris IS OLAYLARINI ve edinim
    iliskisini analitige yazar (audit'ten AYRI, ayni transaction'da guvenilir)."""

    if not settings.analytics_enabled:
        return
    visitor_id = await _visitor_id(session, request)
    for event_type in (
        AnalyticsEventType.SIGNUP_COMPLETED,
        AnalyticsEventType.ORGANIZATION_CREATED,
        AnalyticsEventType.LOGIN_SUCCEEDED,
    ):
        await analytics_service.insert_event(
            session,
            event_type=event_type,
            user_id=user.id,
            organization_id=organization.id,
            visitor_id=visitor_id,
        )
    # Anonim edinim kaynagini kullaniciya/organizasyona bagla (varsa).
    await analytics_service.link_signup_attribution(
        session, visitor_id=visitor_id, user_id=user.id, organization_id=organization.id
    )


async def _record_login_failed_analytics(session: AsyncSession) -> None:
    """Basarisiz girisi YALNIZCA toplam sayaç olarak, kimlik ICERMEDEN yazar.

    Guvenlik: e-posta/kullanici/parola ASLA yazilmaz - kullanici bazinda ifsa
    yoktur. Best-effort'tur: analitik yazimi 401 yanitini bloklamamalidir."""

    if not settings.analytics_enabled:
        return
    try:
        await analytics_service.insert_event(
            session, event_type=AnalyticsEventType.LOGIN_FAILED_SECURITY_SUMMARY
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - analitik, guvenlik yanitini asla bloklamaz
        await session.rollback()


@router.post("/register", response_model=SessionResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    """Kayit: kullanici + organizasyon + owner uyeligi olusturur ve otomatik oturum acar.

    Yeni organizasyon 0 Chip bakiyesiyle baslar; iki ucretsiz hak
    (`list_free_entitlements` uzerinden) idempotent olarak taniml
    anir.
    """

    try:
        result = await auth_service.register_organization_and_owner(
            session,
            email=body.email,
            password=body.password,
            display_name=body.display_name,
            organization_name=body.organization_name,
            organization_website=(str(body.organization_website) if body.organization_website else None),
        )
    except auth_service.EmailAlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail="Bu e-posta adresi zaten kayitli") from exc

    await _issue_session_cookies(
        response,
        session,
        user_id=result.user.id,
        organization_id=result.organization.id,
        role=result.membership.role,
        is_demo=False,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _record_login_audit(
        session,
        user=result.user,
        organization=result.organization,
        login_type="register",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await _record_signup_analytics(session, request, user=result.user, organization=result.organization)
    await session.commit()

    return SessionResponse(
        user_id=result.user.id,
        email=result.user.email,
        display_name=result.user.display_name,
        organization_id=result.organization.id,
        organization_name=result.organization.name,
        role=result.membership.role,
        is_platform_admin=result.user.is_platform_admin,
        is_demo=False,
    )


@router.post("/login", response_model=SessionResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    identifier = _client_identifier(request, body.email)

    if await rate_limit.is_locked_out(redis_client, identifier):
        raise HTTPException(status_code=429, detail=LOCKED_OUT_ERROR)

    try:
        user = await auth_service.authenticate_user(session, email=body.email, password=body.password)
    except auth_service.InvalidCredentialsError as exc:
        await rate_limit.register_failed_attempt(redis_client, identifier)
        await _record_login_failed_analytics(session)
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR) from exc

    await rate_limit.clear_attempts(redis_client, identifier)

    membership = await auth_service.get_active_membership(session, user.id)
    organization = await session.get(Organization, membership.organization_id)
    if organization is None:
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)

    await _issue_session_cookies(
        response,
        session,
        user_id=user.id,
        organization_id=organization.id,
        role=membership.role,
        is_demo=_is_demo_account(user),
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _record_login_audit(
        session,
        user=user,
        organization=organization,
        login_type="password",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await _record_login_analytics(session, request, user=user, organization=organization)
    await session.commit()

    return SessionResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_id=organization.id,
        organization_name=organization.name,
        role=membership.role,
        is_platform_admin=user.is_platform_admin,
        is_demo=_is_demo_account(user),
    )


@router.post("/demo-login", response_model=SessionResponse)
async def demo_login(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    """Parola gerektirmeden, herkese acik 'Canli demo' hesabina tek-tik oturum acar.

    GUVENLIK:
    - Yalnizca `settings.demo_login_enabled` acikken calisir; aksi halde
      ozelligi sizdirmamak icin 404 doner.
    - HICBIR ZAMAN rastgele bir hesaba degil, YALNIZCA `settings.demo_account_
      email` ile tanimli, gelistiricinin kendi demo hesabindan AYRI, taze demo
      hesabina oturum acar. Hesap bir sekilde platform yoneticisiyse istek
      reddedilir - demo-login hicbir kosulda yonetici oturumu ACMAZ.
    - IP basina hiz-sinirlidir (oturum/refresh-token tablosunu sel etmeye karsi).
    """

    if not settings.demo_login_enabled or not settings.demo_account_email:
        raise HTTPException(status_code=404, detail="Bulunamadi")

    client_host = request.client.host if request.client else None
    if await rate_limit.is_demo_login_rate_limited(redis_client, client_host or "unknown"):
        raise HTTPException(status_code=429, detail=DEMO_LOGIN_RATE_LIMITED)

    demo_email = _normalized_demo_email()
    if demo_email is None:
        # Yukaridaki guard bunu zaten sagliyor; burada acik kontrol hem
        # savunma amacli hem de tip daraltma (mypy) icindir.
        raise HTTPException(status_code=404, detail="Bulunamadi")
    user = (
        await session.execute(select(User).where(User.email_normalized == demo_email))
    ).scalar_one_or_none()
    # Demo hesabi yoksa (yanlis yapilandirma) veya bir sekilde platform
    # yoneticisiyse: 500 yerine 404 - demo-login HICBIR kosulda yonetici
    # oturumu ACMAZ.
    if user is None or user.is_platform_admin:
        raise HTTPException(status_code=404, detail="Bulunamadi")

    membership = await auth_service.get_active_membership(session, user.id)
    organization = await session.get(Organization, membership.organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Bulunamadi")

    await _issue_session_cookies(
        response,
        session,
        user_id=user.id,
        organization_id=organization.id,
        role=membership.role,
        is_demo=True,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_host,
    )
    _record_login_audit(
        session,
        user=user,
        organization=organization,
        login_type="demo",
        ip_address=client_host,
        user_agent=request.headers.get("user-agent"),
    )
    await _record_login_analytics(session, request, user=user, organization=organization)
    await session.commit()

    return SessionResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_id=organization.id,
        organization_name=organization.name,
        role=membership.role,
        is_platform_admin=user.is_platform_admin,
        is_demo=True,
    )


@router.post("/refresh", response_model=SessionResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _csrf: None = Depends(verify_csrf),
) -> SessionResponse:
    """Refresh oturumunu dondurur (rotate). Tekrar kullanim tespit edilirse oturum iptal edilir."""

    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Refresh token bulunamadi")

    try:
        user, issued = await auth_service.rotate_refresh_token(
            session,
            raw_token=raw_token,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    except auth_service.RefreshTokenReuseError as exc:
        await session.commit()
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=401,
            detail="Oturum guvenlik nedeniyle sonlandirildi, lutfen tekrar giris yapin",
        ) from exc
    except auth_service.RefreshTokenInvalidError as exc:
        clear_auth_cookies(response)
        raise HTTPException(status_code=401, detail="Oturum gecersiz veya suresi dolmus") from exc

    membership = await auth_service.get_active_membership(session, user.id)
    organization = await session.get(Organization, membership.organization_id)
    if organization is None:
        raise HTTPException(status_code=401, detail="Organizasyon bulunamadi")

    is_demo = _is_demo_account(user)
    access_token = create_access_token(
        user_id=user.id,
        organization_id=organization.id,
        role=membership.role,
        is_demo=is_demo,
    )
    set_access_token_cookie(response, access_token)
    set_refresh_token_cookie(response, issued.raw_token, max_age_seconds=_refresh_max_age(issued))
    set_csrf_token_cookie(response, generate_csrf_token())

    await session.commit()

    return SessionResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_id=organization.id,
        organization_name=organization.name,
        role=membership.role,
        is_platform_admin=user.is_platform_admin,
        is_demo=is_demo,
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if raw_token:
        await auth_service.revoke_refresh_token_session(session, raw_token=raw_token)
        await session.commit()

    # Logout is olayi (best-effort): access token'dan kullanici/organizasyon
    # cozulebiliyorsa iliskilendirilir; analitik yazimi cikisi asla bloklamaz.
    if settings.analytics_enabled:
        try:
            user_id: uuid.UUID | None = None
            organization_id: uuid.UUID | None = None
            access_token = request.cookies.get(ACCESS_TOKEN_COOKIE)
            if access_token:
                try:
                    payload = decode_access_token(access_token)
                    user_id = uuid.UUID(payload["sub"])
                    organization_id = uuid.UUID(payload["org"])
                except (InvalidAccessTokenError, KeyError, ValueError):
                    user_id = organization_id = None
            await analytics_service.insert_event(
                session,
                event_type=AnalyticsEventType.LOGOUT,
                user_id=user_id,
                organization_id=organization_id,
                visitor_id=await _visitor_id(session, request),
            )
            await session.commit()
        except Exception:  # noqa: BLE001 - analitik cikis akisini asla bozmaz
            await session.rollback()

    clear_auth_cookies(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=SessionResponse)
async def me(
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> SessionResponse:
    user = await session.get(User, principal.user_id)
    organization = await session.get(Organization, principal.organization_id)
    if user is None or organization is None:
        raise HTTPException(status_code=401, detail="Oturum gecersiz")

    return SessionResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        organization_id=organization.id,
        organization_name=organization.name,
        role=principal.role,
        is_platform_admin=user.is_platform_admin,
        is_demo=_is_demo_account(user),
    )


# --- Parola sifirlama --------------------------------------------------------


@router.post("/password-reset/request", response_model=PasswordResetRequestResponse)
async def request_password_reset(
    body: PasswordResetRequestBody,
    session: AsyncSession = Depends(get_session),
) -> PasswordResetRequestResponse:
    """Parola sifirlama tokeni olusturur.

    Kullanicinin var olup olmadigini sizdirmamak icin e-posta kayitli olsun
    ya da olmasin ayni genel mesaj dondurulur. Bu asamada gercek bir
    e-posta saglayicisi baglanmadigindan, uretilen ham token yalnizca
    `ENVIRONMENT=development` iken yanitta dondurulur (yerel gelistirmede
    manuel test icin); gercek gonderim mekanizmasi sonraki bir asamaya
    birakilmistir.
    """

    issued = await auth_service.create_password_reset_token(session, email=body.email)
    await session.commit()

    dev_token = issued.raw_token if (issued is not None and settings.environment == "development") else None

    return PasswordResetRequestResponse(
        message=PASSWORD_RESET_REQUESTED_MESSAGE,
        dev_reset_token=dev_token,
    )


@router.post("/password-reset/confirm", response_model=PasswordResetConfirmResponse)
async def confirm_password_reset(
    body: PasswordResetConfirmBody,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> PasswordResetConfirmResponse:
    """Tokeni tuketip yeni parolayi uygular; guvenlik icin tum oturumlari iptal eder."""

    try:
        await auth_service.reset_password(session, raw_token=body.token, new_password=body.new_password)
    except auth_service.PasswordResetTokenInvalidError as exc:
        raise HTTPException(status_code=400, detail="Token gecersiz veya suresi dolmus") from exc

    await session.commit()

    # Sunucu tarafinda tum refresh oturumlari zaten iptal edildi; bu istegi
    # yapan tarayicida (varsa) kalan auth cookie'lerini de temizleyerek
    # mevcut access token'in gorunur oturumunu de kapatiyoruz.
    clear_auth_cookies(response)

    return PasswordResetConfirmResponse(message="Parolaniz basariyla guncellendi")
