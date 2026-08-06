"""Kayit, giris ve refresh token rotasyonu icin kimlik dogrulama is mantigi.

Refresh token rotasyonu: her `rotate_refresh_token` cagrisi, sunulan tokeni
`revoked_at`+`replaced_by_id` ile iptal eder ve ayni `session_id` altinda
yeni bir satir olusturur. Zaten iptal edilmis (dolayisiyla daha once
rotasyona ugramis) bir token tekrar sunulursa bu, bir refresh token
calinmasi/tekrar kullanimi (reuse) belirtisi sayilir ve o oturumun tum
zinciri (`session_id`) guvenlik onlemi olarak derhal iptal edilir.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.auth import PasswordResetToken, RefreshToken
from app.models.tenancy import Membership, Organization, User
from app.security import (
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.services.entitlements import list_free_entitlements

DEFAULT_ROLE = "owner"


class InvalidCredentialsError(Exception):
    """Giris basarisiz (e-posta yok VEYA parola yanlis - kasitli olarak ayirt edilmez)."""


class EmailAlreadyRegisteredError(Exception):
    pass


class RefreshTokenInvalidError(Exception):
    pass


class PasswordResetTokenInvalidError(Exception):
    """Token bulunamadi, suresi dolmus veya zaten kullanilmis."""


class RefreshTokenReuseError(Exception):
    """Iptal edilmis/rotasyona ugramis bir refresh token tekrar sunuldu."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_RE.sub("-", ascii_value.lower()).strip("-")
    return slug or "organization"


async def _unique_organization_slug(session: AsyncSession, base_name: str) -> str:
    base_slug = slugify(base_name)
    candidate = base_slug
    suffix = 0
    while True:
        existing = await session.execute(select(Organization.id).where(Organization.slug == candidate))
        if existing.scalar_one_or_none() is None:
            return candidate
        suffix += 1
        candidate = f"{base_slug}-{suffix}"


@dataclass
class RegistrationResult:
    user: User
    organization: Organization
    membership: Membership


async def register_organization_and_owner(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None,
    organization_name: str,
    organization_website: str | None = None,
) -> RegistrationResult:
    """Yeni bir kullanici, organizasyon ve owner uyeligi olusturur.

    Yeni organizasyon 0 Chip bakiyesiyle baslar (hicbir kredi eklenmez) ve
    iki ucretsiz hakki `list_free_entitlements` uzerinden idempotent olarak
    tanimlanir (bkz. docs/product-rules.md).
    """

    email_normalized = normalize_email(email)
    existing = await session.execute(select(User.id).where(User.email_normalized == email_normalized))
    if existing.scalar_one_or_none() is not None:
        raise EmailAlreadyRegisteredError("Bu e-posta adresi zaten kayitli")

    organization = Organization(
        name=organization_name,
        slug=await _unique_organization_slug(session, organization_name),
        website_url=organization_website,
    )
    session.add(organization)
    await session.flush()

    user = User(
        email=email,
        email_normalized=email_normalized,
        display_name=display_name,
        password_hash=hash_password(password),
    )
    session.add(user)
    await session.flush()

    membership = Membership(organization_id=organization.id, user_id=user.id, role=DEFAULT_ROLE)
    session.add(membership)
    await session.flush()

    # Ilk erisimde ("get or create") iki ucretsiz hakki tanimla: 0 Chip +
    # 1 temel UX testi + 1 erisilebilirlik on kontrolu.
    await list_free_entitlements(session, organization.id)

    return RegistrationResult(user=user, organization=organization, membership=membership)


async def authenticate_user(session: AsyncSession, *, email: str, password: str) -> User:
    """Kimlik dogrular; basarisizsa (kullanici yok VEYA parola yanlis) ayni hatayi firlatir.

    Kullanicinin var olup olmadigini disariya sizdirmamak icin her iki
    durumda da ayni `InvalidCredentialsError` mesaji kullanilir.
    """

    email_normalized = normalize_email(email)
    result = await session.execute(select(User).where(User.email_normalized == email_normalized))
    user = result.scalar_one_or_none()

    if user is None:
        # Zamanlama yan kanali (timing side-channel) ile kullanici varligini
        # sizdirmamak icin, kullanici bulunamasa bile bir hash dogrulamasi
        # calistirilir (sabit maliyetli basarisiz karsilastirma).
        verify_password(password, hash_password("dummy-password-for-timing-parity"))
        raise InvalidCredentialsError("E-posta veya parola hatali")

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError("E-posta veya parola hatali")

    return user


async def get_active_membership(session: AsyncSession, user_id: uuid.UUID) -> Membership:
    """Kullanicinin (bu asamada tekil olan) aktif organizasyon uyeligini dondurur."""

    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at.asc()).limit(1)
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise InvalidCredentialsError("Kullanicinin aktif bir organizasyon uyeligi yok")
    return membership


@dataclass
class IssuedRefreshToken:
    raw_token: str
    session_id: uuid.UUID
    expires_at: datetime


async def issue_refresh_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None = None,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> IssuedRefreshToken:
    raw_token = generate_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    resolved_session_id = session_id or uuid.uuid4()

    session.add(
        RefreshToken(
            user_id=user_id,
            session_id=resolved_session_id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
    )
    await session.flush()

    return IssuedRefreshToken(raw_token=raw_token, session_id=resolved_session_id, expires_at=expires_at)


async def revoke_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    """Verilen rotasyon zincirindeki (session_id) tum aktif tokenlari iptal eder."""

    now = datetime.now(UTC)
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    for token in result.scalars().all():
        token.revoked_at = now
    await session.flush()


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    raw_token: str,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[User, IssuedRefreshToken]:
    """Sunulan refresh tokeni dondurur (rotate); reuse tespit edilirse oturumu iptal eder."""

    token_hash = hash_token(raw_token)
    result = await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    existing = result.scalar_one_or_none()
    if existing is None:
        raise RefreshTokenInvalidError("Refresh token bulunamadi")

    now = datetime.now(UTC)

    if existing.revoked_at is not None or existing.expires_at < now:
        # Zaten iptal edilmis (rotasyona ugramis ya da suresi gecmis) bir
        # token tekrar sunuldu: bu, calinmis bir refresh token'in tekrar
        # kullanilmaya calisilmasi (reuse) olabilecegi icin tum zincir
        # guvenlik onlemi olarak iptal edilir.
        if existing.revoked_at is not None:
            await revoke_session(session, existing.session_id)
            raise RefreshTokenReuseError("Refresh token tekrar kullanildi (reuse tespit edildi)")
        raise RefreshTokenInvalidError("Refresh token suresi dolmus")

    user = await session.get(User, existing.user_id)
    if user is None:
        raise RefreshTokenInvalidError("Kullanici bulunamadi")

    issued = await issue_refresh_token(
        session,
        user_id=existing.user_id,
        session_id=existing.session_id,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    existing.revoked_at = now
    new_token_result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(issued.raw_token))
    )
    new_token_row = new_token_result.scalar_one()
    existing.replaced_by_id = new_token_row.id
    await session.flush()

    return user, issued


async def revoke_refresh_token_session(session: AsyncSession, *, raw_token: str) -> None:
    """Logout: tokenin ait oldugu tum rotasyon zincirini (session_id) iptal eder.

    Token bulunamazsa (ornegin zaten iptal edilmisse) sessizce hicbir sey
    yapmaz; logout, oturum zaten gecersizse de basarili sayilir.
    """

    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token))
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        return
    await revoke_session(session, existing.session_id)


# --- Parola sifirlama --------------------------------------------------------


@dataclass
class IssuedPasswordResetToken:
    raw_token: str
    expires_at: datetime


async def create_password_reset_token(
    session: AsyncSession, *, email: str
) -> IssuedPasswordResetToken | None:
    """Kullanici varsa bir parola sifirlama tokeni olusturur.

    Kullanici bulunamazsa `None` doner; cagiran taraf (router), kullanici
    varligini sizdirmamak icin bu durumda da ayni genel basarili yaniti
    dondurmelidir (bkz. `app.routers.auth.request_password_reset`).
    """

    email_normalized = normalize_email(email)
    result = await session.execute(select(User).where(User.email_normalized == email_normalized))
    user = result.scalar_one_or_none()
    if user is None:
        return None

    raw_token = generate_refresh_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.password_reset_token_ttl_seconds)
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
    )
    await session.flush()

    return IssuedPasswordResetToken(raw_token=raw_token, expires_at=expires_at)


async def reset_password(session: AsyncSession, *, raw_token: str, new_password: str) -> User:
    """Tokeni tuketip parolayi degistirir ve guvenlik icin tum oturumlari iptal eder."""

    result = await session.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token))
    )
    token_row = result.scalar_one_or_none()

    now = datetime.now(UTC)
    if token_row is None or token_row.used_at is not None or token_row.expires_at < now:
        raise PasswordResetTokenInvalidError("Token gecersiz veya suresi dolmus")

    user = await session.get(User, token_row.user_id)
    if user is None:
        raise PasswordResetTokenInvalidError("Kullanici bulunamadi")

    user.password_hash = hash_password(new_password)
    token_row.used_at = now

    # Parola degisikligi sonrasi, calinmis olabilecek mevcut tum refresh
    # oturumlarini guvenlik onlemi olarak iptal ederiz.
    active_sessions = await session.execute(
        select(RefreshToken.session_id).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    )
    for (session_id,) in active_sessions.unique().all():
        await revoke_session(session, session_id)

    await session.flush()
    return user
