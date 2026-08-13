"""Ziyaretci/trafik analitigi is mantigi (gizlilik dostu, KVKK acisindan olculu).

Bu modul, olay kaydinin GUVENLI ve GIZLILIK-DOSTU kalmasini saglayan saf
yardimcilar (path/UA/referrer sanitasyonu, dedup, cihaz siniflandirma, open-
redirect korumasi, CSV formula-injection escape) ile DB yardimcilarini
(ziyaretci/oturum get-or-create, olay kaydi, edinim iliskilendirme, retention
temizligi) icerir.

GIZLILIK KURALLARI (tek yer):
- HAM IP saklanmaz; tam user-agent saklanmaz (yalnizca kaba device/browser/os
  aileleri turetilir). Fingerprinting (canvas/font/donanim) YAPILMAZ.
- Hassas query parametreleri (`access_token`, `refresh_token`, `token`, `code`,
  `password`, `email` ...) HICBIR ZAMAN saklanmaz - `normalize_path` query'yi
  tamamen atar, yalnizca izin verilen UTM/`ref` alanlari AYRI kolonlarda tutulur.
- Kullanici/organizasyon kimligi istemciden gelen degerlere DEGIL, sunucu
  tarafinda dogrulanmis oturuma (access token) baglanir.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.analytics import (
    AnalyticsEvent,
    AnalyticsEventType,
    AnalyticsSession,
    AnalyticsVisitor,
    OrganizationAcquisitionAttribution,
    UserAcquisitionAttribution,
)

# Bir oturumun (visit) hareketsizlik penceresi: son olaydan bu kadar dakika
# sonra gelen bir olay YENI bir oturum baslatir (ve `visitor_session_started`
# olayi uretir).
SESSION_IDLE_MINUTES = 30

# Alan uzunlugu ust sinirlari (DB kolonlariyla uyumlu, savunma amacli kirpma).
_MAX_UTM_LEN = 200
_MAX_REFERRAL_LEN = 100
_MAX_REFERRER_DOMAIN_LEN = 255
_MAX_PATH_LEN = 1024

# ASLA saklanmayacak query parametreleri (kismi/substring eslesme de dahil).
# Not: `normalize_path` zaten TUM query'yi attigi icin bu liste ek bir savunma
# katmanidir (ornegin ileride query'nin bir kismi tutulmak istenirse).
SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "refresh_token", "token", "code", "password", "email", "otp", "secret", "api_key"}
)


# --------------------------------------------------------------------------- #
# Saf sanitasyon / siniflandirma yardimcilari                                 #
# --------------------------------------------------------------------------- #


def _clip(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:max_len]


def clip_text(value: str | None, max_len: int) -> str | None:
    """`_clip`in public sarmalayicisi: bos/whitespace -> None, aksi halde kirpar."""

    return _clip(value, max_len)


def normalize_path(raw_path: str | None) -> str | None:
    """Bir sayfa yolunu normalize eder: query/fragment ATILIR, yalnizca yol kalir.

    - Yalnizca dahili yol tutulur; mutlak/harici bir URL verilirse yalnizca
      `path` bileseni alinir (host/scheme ATILIR - PII/harici bilgi sizmaz).
    - Yol `/` ile baslamiyorsa basina `/` eklenir.
    - Sondaki `/` (kok haric) kaldirilir; boylece `/a/` ve `/a` ayni sayilir.
    """

    if not raw_path:
        return None
    raw_path = raw_path.strip()
    if not raw_path:
        return None
    # Mutlak URL verilse bile yalnizca path bilesenini al (query/host at).
    path = urlsplit(raw_path).path or "/"
    if not path.startswith("/"):
        path = "/" + path
    # Kontrol karakterlerini ve backslash'i temizle.
    path = re.sub(r"[\x00-\x1f\\]", "", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return path[:_MAX_PATH_LEN]


def referrer_to_domain(referrer: str | None) -> str | None:
    """Referrer URL'inden YALNIZCA host (domain) bilesenini alir (yol/query ATILIR)."""

    if not referrer:
        return None
    host = urlsplit(referrer.strip()).hostname
    if not host:
        return None
    return _clip(host.lower(), _MAX_REFERRER_DOMAIN_LEN)


@dataclass(frozen=True)
class AttributionInput:
    """Bir olayin/oturumun edinim (acquisition) alanlari - istemciden gelir,
    burada sanitize edilir."""

    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_content: str | None = None
    utm_term: str | None = None
    referral_code: str | None = None
    referrer_domain: str | None = None


def sanitize_attribution(
    *,
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    utm_content: str | None = None,
    utm_term: str | None = None,
    referral_code: str | None = None,
    referrer: str | None = None,
) -> AttributionInput:
    """Istemciden gelen ham edinim alanlarini kirparak/normalize ederek dondurur."""

    return AttributionInput(
        utm_source=_clip(utm_source, _MAX_UTM_LEN),
        utm_medium=_clip(utm_medium, _MAX_UTM_LEN),
        utm_campaign=_clip(utm_campaign, _MAX_UTM_LEN),
        utm_content=_clip(utm_content, _MAX_UTM_LEN),
        utm_term=_clip(utm_term, _MAX_UTM_LEN),
        referral_code=_clip(referral_code, _MAX_REFERRAL_LEN),
        referrer_domain=referrer_to_domain(referrer),
    )


def classify_device(user_agent: str | None) -> str:
    """Kaba cihaz kategorisi: desktop/mobile/tablet/unknown (fingerprint DEGIL)."""

    if not user_agent:
        return "unknown"
    ua = user_agent.lower()
    if "ipad" in ua or ("tablet" in ua and "mobile" not in ua) or ("android" in ua and "mobile" not in ua):
        return "tablet"
    if "mobi" in ua or "iphone" in ua or "ipod" in ua or ("android" in ua and "mobile" in ua):
        return "mobile"
    if any(token in ua for token in ("windows", "macintosh", "mac os x", "linux", "cros", "x11")):
        return "desktop"
    return "unknown"


def browser_family(user_agent: str | None) -> str | None:
    """Kaba tarayici ailesi (dusuk kardinalite)."""

    if not user_agent:
        return None
    ua = user_agent.lower()
    # Sira onemli: Edge/Opera, Chrome'dan; Chrome, Safari'den ONCE denetlenir.
    if "edg/" in ua or "edga" in ua or "edgios" in ua:
        return "Edge"
    if "opr/" in ua or "opera" in ua:
        return "Opera"
    if "firefox" in ua or "fxios" in ua:
        return "Firefox"
    if "chrome" in ua or "crios" in ua or "chromium" in ua:
        return "Chrome"
    if "safari" in ua:
        return "Safari"
    return "Other"


def os_family(user_agent: str | None) -> str | None:
    """Kaba isletim sistemi ailesi (dusuk kardinalite)."""

    if not user_agent:
        return None
    ua = user_agent.lower()
    if "windows" in ua:
        return "Windows"
    if "iphone" in ua or "ipad" in ua or "ipod" in ua or "ios" in ua:
        return "iOS"
    if "mac os x" in ua or "macintosh" in ua:
        return "macOS"
    if "android" in ua:
        return "Android"
    if "cros" in ua:
        return "ChromeOS"
    if "linux" in ua:
        return "Linux"
    return "Other"


class InvalidInternalPathError(ValueError):
    """`destination_path` gecerli, izin verilen bir DAHILI yol degil (open redirect riski)."""


def validate_internal_path(raw_path: str) -> str:
    """Bir takip baglantisi hedefinin YALNIZCA izin verilen dahili bir yol olmasini zorlar.

    Open redirect'e karsi: mutlak URL (`http://...`), scheme-relative (`//evil`),
    backslash, kontrol karakteri veya scheme iceren hicbir deger kabul edilmez -
    yalnizca tek bir `/` ile baslayan goreli bir yol gecerlidir.
    """

    if raw_path is None:
        raise InvalidInternalPathError("Hedef yol bos olamaz")
    path = raw_path.strip()
    if not path.startswith("/"):
        raise InvalidInternalPathError("Hedef yol '/' ile baslamalidir")
    if path.startswith("//") or "\\" in path:
        raise InvalidInternalPathError("Hedef yol scheme-relative (//) veya backslash iceremez")
    if re.search(r"[\x00-\x1f]", path):
        raise InvalidInternalPathError("Hedef yol kontrol karakteri iceremez")
    if re.match(r"^/[a-zA-Z][a-zA-Z0-9+.-]*:", path):
        # `/javascript:...` gibi kacamaklari da reddet.
        raise InvalidInternalPathError("Hedef yol bir scheme iceremez")
    if len(path) > _MAX_PATH_LEN:
        raise InvalidInternalPathError("Hedef yol cok uzun")
    return path


# CSV formula injection: bu karakterlerle BASLAYAN bir hucre, Excel/Sheets
# tarafindan formul olarak yorumlanabilir; guvenli sekilde ` '` ile prefixlenir.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_csv_cell(value: object) -> str:
    """Bir CSV hucresini formula-injection'a karsi guvenli hale getirir.

    `=`, `+`, `-`, `@`, tab veya CR ile baslayan degerler tek tirnakla
    ` '` prefixlenir. Donen deger HAM (tirnaklanmamis) metindir; alanin
    kendisini tirnaklamak/virgul kacisi `csv` modulune birakilir.
    """

    if value is None:
        return ""
    text = str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


def generate_referral_code() -> str:
    """Tahmin edilmesi zor (yuksek entropili) rastgele bir referral code uretir.

    Sirali/ardil bir ID DEGILDIR (bkz. docs/security.md). URL-guvenli, ~12
    karakterlik bir token dondurur.
    """

    return secrets.token_urlsafe(9)


# --------------------------------------------------------------------------- #
# DB yardimcilari                                                             #
# --------------------------------------------------------------------------- #


async def get_or_create_visitor(
    session: AsyncSession,
    *,
    visitor_id: uuid.UUID | None,
    attribution: AttributionInput,
    landing_path: str | None,
    device_category: str | None,
    browser: str | None,
    os_name: str | None,
    now: datetime,
) -> AnalyticsVisitor:
    """Cookie'deki visitor_id'ye karsilik gelen ziyaretciyi bulur ya da olusturur.

    Istemcinin kestigi (DB'de OLMAYAN) bir UUID gonderdigi durumda o deger
    KULLANILMAZ - sunucu TAZE bir visitor uretir (istemcinin visitor kimligini
    zorlamasini engeller). Yeni ziyaretcinin `first_touch_*` alanlari ilk
    edinim kaynagiyla bir kez doldurulur.
    """

    visitor: AnalyticsVisitor | None = None
    if visitor_id is not None:
        visitor = await session.get(AnalyticsVisitor, visitor_id)

    if visitor is None:
        visitor = AnalyticsVisitor(
            last_seen_at=now,
            first_utm_source=attribution.utm_source,
            first_utm_medium=attribution.utm_medium,
            first_utm_campaign=attribution.utm_campaign,
            first_utm_content=attribution.utm_content,
            first_utm_term=attribution.utm_term,
            first_referral_code=attribution.referral_code,
            first_referrer_domain=attribution.referrer_domain,
            first_landing_path=landing_path,
            device_category=device_category,
            browser_family=browser,
            os_family=os_name,
        )
        session.add(visitor)
        await session.flush()
        return visitor

    # Mevcut ziyaretci: yalnizca son gorulme + son bilinen cihaz guncellenir
    # (first_touch DEGISMEZ).
    visitor.last_seen_at = now
    if device_category:
        visitor.device_category = device_category
    if browser:
        visitor.browser_family = browser
    if os_name:
        visitor.os_family = os_name
    return visitor


async def get_or_create_session(
    session: AsyncSession,
    *,
    visitor: AnalyticsVisitor,
    session_id: uuid.UUID | None,
    attribution: AttributionInput,
    landing_path: str | None,
    device_category: str | None,
    browser: str | None,
    os_name: str | None,
    user_id: uuid.UUID | None,
    organization_id: uuid.UUID | None,
    now: datetime,
) -> tuple[AnalyticsSession, bool]:
    """Aktif (hareketsizlik penceresi icindeki) oturumu bulur ya da yeni acar.

    Donen ikinci deger `created` (yeni oturum acildi mi) - cagiran taraf yeni
    oturumda `visitor_session_started` olayi uretmelidir.
    """

    visit: AnalyticsSession | None = None
    if session_id is not None:
        visit = await session.get(AnalyticsSession, session_id)
        if visit is not None and (
            visit.visitor_id != visitor.id
            or visit.last_event_at < now - timedelta(minutes=SESSION_IDLE_MINUTES)
        ):
            # Baska bir ziyaretciye ait ya da suresi gecmis: yeniden kullanma.
            visit = None

    if visit is None:
        visit = AnalyticsSession(
            visitor_id=visitor.id,
            last_event_at=now,
            utm_source=attribution.utm_source,
            utm_medium=attribution.utm_medium,
            utm_campaign=attribution.utm_campaign,
            utm_content=attribution.utm_content,
            utm_term=attribution.utm_term,
            referral_code=attribution.referral_code,
            referrer_domain=attribution.referrer_domain,
            landing_path=landing_path,
            device_category=device_category,
            browser_family=browser,
            os_family=os_name,
            user_id=user_id,
            organization_id=organization_id,
        )
        session.add(visit)
        await session.flush()
        return visit, True

    visit.last_event_at = now
    # Oturum icinde kullanici giris yaptiysa iliskiyi guncelle.
    if user_id is not None:
        visit.user_id = user_id
    if organization_id is not None:
        visit.organization_id = organization_id
    return visit, False


async def resolve_existing_visitor_id(
    session: AsyncSession, raw_cookie_value: str | None
) -> uuid.UUID | None:
    """Cookie'deki visitor UUID'si DB'de gercekten VARSA id'sini dondurur, yoksa None.

    Sunucu-tarafli is olaylarina (signup/login) `visitor_id` FK'si eklemeden ONCE
    kullanilir - var olmayan bir visitor id'ye referans veren bir olayin FK ihlali
    firlatmasini onler (ornegin analitik izni verilmediyse visitor satiri hic
    olusmamis olabilir).
    """

    if not raw_cookie_value:
        return None
    try:
        vid = uuid.UUID(raw_cookie_value)
    except ValueError:
        return None
    visitor = await session.get(AnalyticsVisitor, vid)
    return visitor.id if visitor else None


async def insert_event(
    session: AsyncSession,
    *,
    event_type: AnalyticsEventType,
    visitor_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    path: str | None = None,
    attribution: AttributionInput | None = None,
    device_category: str | None = None,
    browser: str | None = None,
    os_name: str | None = None,
    country: str | None = None,
    user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    dedup_key: str | None = None,
) -> None:
    """Bir analitik olayini ekler; `dedup_key` verildiyse idempotent (ON CONFLICT DO NOTHING).

    Ayni `dedup_key` ikinci kez gelirse (frontend yeniden denemesi / cift HTTP
    istegi) satir SESSIZCE eklenmez - mevcut kayit korunur.
    """

    attribution = attribution or AttributionInput()
    values = {
        "id": uuid.uuid4(),
        "event_type": event_type,
        "visitor_id": visitor_id,
        "session_id": session_id,
        "path": path,
        "referrer_domain": attribution.referrer_domain,
        "utm_source": attribution.utm_source,
        "utm_medium": attribution.utm_medium,
        "utm_campaign": attribution.utm_campaign,
        "utm_content": attribution.utm_content,
        "utm_term": attribution.utm_term,
        "referral_code": attribution.referral_code,
        "device_category": device_category,
        "browser_family": browser,
        "os_family": os_name,
        "country": country,
        "user_id": user_id,
        "organization_id": organization_id,
        "dedup_key": dedup_key,
    }
    stmt = pg_insert(AnalyticsEvent).values(**values)
    if dedup_key is not None:
        stmt = stmt.on_conflict_do_nothing(constraint="uq_analytics_events_dedup_key")
    await session.execute(stmt)


async def _latest_session_attribution(
    session: AsyncSession, visitor_id: uuid.UUID
) -> AttributionInput | None:
    """Ziyaretcinin EN SON oturumunun edinim alanlari (last-touch attribution)."""

    row = (
        await session.execute(
            select(AnalyticsSession)
            .where(AnalyticsSession.visitor_id == visitor_id)
            .order_by(AnalyticsSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return AttributionInput(
        utm_source=row.utm_source,
        utm_medium=row.utm_medium,
        utm_campaign=row.utm_campaign,
        utm_content=row.utm_content,
        utm_term=row.utm_term,
        referral_code=row.referral_code,
        referrer_domain=row.referrer_domain,
    )


async def link_signup_attribution(
    session: AsyncSession,
    *,
    visitor_id: uuid.UUID | None,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> None:
    """Kayit aninda anonim ziyaretcinin edinim kaynagini kullaniciya/organizasyona baglar.

    First-touch (ziyaretcinin ilk kaynagi) ve last-touch (kayit oturumunun
    kaynagi) edinim alanlari `user_acquisition_attribution` /
    `organization_acquisition_attribution` tablolarina DENORMALIZE kopyalanir -
    boylece ziyaretci satiri retention ile silinse bile edinim korunur.

    Ziyaretci yoksa (izin verilmemis/analitik kapali) hicbir attribution
    satiri yazilmaz (guvenli no-op).
    """

    if not settings.analytics_enabled or visitor_id is None:
        return
    visitor = await session.get(AnalyticsVisitor, visitor_id)
    if visitor is None:
        return

    visitor.linked_user_id = user_id
    visitor.linked_organization_id = organization_id

    last = await _latest_session_attribution(session, visitor_id) or AttributionInput(
        utm_source=visitor.first_utm_source,
        utm_medium=visitor.first_utm_medium,
        utm_campaign=visitor.first_utm_campaign,
        utm_content=visitor.first_utm_content,
        utm_term=visitor.first_utm_term,
        referral_code=visitor.first_referral_code,
        referrer_domain=visitor.first_referrer_domain,
    )

    common = {
        "visitor_id": visitor_id,
        "first_utm_source": visitor.first_utm_source,
        "first_utm_medium": visitor.first_utm_medium,
        "first_utm_campaign": visitor.first_utm_campaign,
        "first_utm_content": visitor.first_utm_content,
        "first_utm_term": visitor.first_utm_term,
        "first_referral_code": visitor.first_referral_code,
        "first_referrer_domain": visitor.first_referrer_domain,
        "last_utm_source": last.utm_source,
        "last_utm_medium": last.utm_medium,
        "last_utm_campaign": last.utm_campaign,
        "last_utm_content": last.utm_content,
        "last_utm_term": last.utm_term,
        "last_referral_code": last.referral_code,
        "last_referrer_domain": last.referrer_domain,
    }

    # Idempotent: ayni kullanici/organizasyon icin ikinci kez cagrilirsa (ON
    # CONFLICT DO NOTHING) mevcut edinim kaydi korunur.
    await session.execute(
        pg_insert(UserAcquisitionAttribution)
        .values(id=uuid.uuid4(), user_id=user_id, **common)
        .on_conflict_do_nothing(constraint="uq_user_acquisition_attribution_user_id")
    )
    await session.execute(
        pg_insert(OrganizationAcquisitionAttribution)
        .values(id=uuid.uuid4(), organization_id=organization_id, **common)
        .on_conflict_do_nothing(constraint="uq_organization_acquisition_attribution_organization_id")
    )


@dataclass(frozen=True)
class PurgeCounts:
    events: int
    sessions: int
    visitors: int


async def purge_expired(session: AsyncSession, *, now: datetime | None = None) -> PurgeCounts:
    """`ANALYTICS_RETENTION_DAYS`den eski analitik satirlarini guvenle siler.

    Sadece zaman-esikli (exact WHERE created_at/last_* < cutoff) toplu silme;
    prefix/pattern tabanli hicbir kosul yoktur. Edinim (attribution) tablolari
    KASITLI olarak silinmez - onlar denormalize, kalici edinim ozetidir.
    """

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=settings.analytics_retention_days)

    events_result = await session.execute(delete(AnalyticsEvent).where(AnalyticsEvent.created_at < cutoff))
    sessions_result = await session.execute(
        delete(AnalyticsSession).where(AnalyticsSession.last_event_at < cutoff)
    )
    visitors_result = await session.execute(
        delete(AnalyticsVisitor).where(AnalyticsVisitor.last_seen_at < cutoff)
    )
    await session.commit()
    return PurgeCounts(
        events=events_result.rowcount or 0,
        sessions=sessions_result.rowcount or 0,
        visitors=visitors_result.rowcount or 0,
    )


async def run_purge_cycle() -> PurgeCounts:
    """Worker cron giris noktasi: kendi session'ini acar ve retention temizligini calistirir.

    `analytics_enabled=False` iken bile eski kayitlar (analitik sonradan
    kapatilmis olabilir) guvenle temizlenir - retention her zaman uygulanir."""

    from app.db import async_session_maker

    async with async_session_maker() as session:
        return await purge_expired(session)


__all__ = [
    "SESSION_IDLE_MINUTES",
    "SENSITIVE_QUERY_KEYS",
    "AttributionInput",
    "InvalidInternalPathError",
    "PurgeCounts",
    "normalize_path",
    "referrer_to_domain",
    "sanitize_attribution",
    "classify_device",
    "browser_family",
    "os_family",
    "validate_internal_path",
    "escape_csv_cell",
    "generate_referral_code",
    "get_or_create_visitor",
    "get_or_create_session",
    "resolve_existing_visitor_id",
    "insert_event",
    "link_signup_attribution",
    "purge_expired",
    "run_purge_cycle",
    "clip_text",
]
