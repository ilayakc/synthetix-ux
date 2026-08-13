"""Ziyaretci/trafik analitigi API'si.

Iki bolum vardir:

1. **Public ingestion** (`POST /api/analytics/events`) — kimlik dogrulamasi
   GEREKTIRMEZ; anonim ziyaretcilerin sayfa goruntuleme (page_view) olaylarini
   kaydeder. GUVENLIK: yalnizca izin verilen olay turu (page_view) kabul edilir;
   payload boyutu ve IP basina hiz siniri uygulanir; visitor/session kimligi
   sunucunun urettigi HttpOnly cookie'lerden turetilir (istemci zorlayamaz);
   kullanici/organizasyon bilgisi YALNIZCA sunucu tarafindaki access token'dan
   dogrulanir (istemcinin gonderdigi degerlere GUVENILMEZ); keyfi metadata
   kabul edilmez (`extra="forbid"`).

2. **Admin okuma + takip baglantisi CRUD** (`/api/admin/analytics/*`) — YALNIZCA
   platform yoneticisi (`require_platform_admin`). Normal organizasyon
   kullanicilari (owner/admin/analyst/viewer) bu verileri goremez (403). Sirket/
   kullanici gosterim adlari istemciden DEGIL, dogrulanmis `memberships`/
   `organizations` join'lerinden gelir.

3. **Takip baglantisi yonlendirmesi** (`GET /api/analytics/track/{code}`) —
   yalnizca izin verilen bir DAHILI yola 302 yapar (open redirect'e karsi).
"""

import csv
import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.cookies import (
    ACCESS_TOKEN_COOKIE,
    ANALYTICS_SESSION_COOKIE,
    ANALYTICS_VISITOR_COOKIE,
    set_analytics_session_cookie,
    set_analytics_visitor_cookie,
)
from app.db import get_session
from app.dependencies import Principal, require_platform_admin
from app.logging_config import get_logger
from app.models.analytics import (
    AnalyticsEvent,
    AnalyticsEventType,
    OrganizationAcquisitionAttribution,
    TrackingLink,
    UserAcquisitionAttribution,
)
from app.models.projects import Project
from app.models.reports import Report
from app.models.tenancy import Membership, Organization, User
from app.redis_client import redis_client
from app.security import InvalidAccessTokenError, decode_access_token
from app.services import analytics as analytics_service
from app.services import rate_limit

logger = get_logger("analytics")

# Public ingestion ucunun kabul ettigi TEK olay turu. Diger tum olaylar
# (signup/login/organizasyon/first_*) YALNIZCA sunucu tarafinda, ilgili is
# olayinin gerceklestigi transaction icinde uretilir (bkz. app.routers.auth,
# app.routers.projects, app.services.test_wizard).
ALLOWED_CLIENT_EVENT_TYPES = frozenset({AnalyticsEventType.PAGE_VIEW})

# Public ingestion route (demo salt-okunur middleware allowlist'i ile uyumlu).
INGEST_PATH = "/api/analytics/events"

public_router = APIRouter(prefix="/api/analytics", tags=["analytics"])
admin_router = APIRouter(prefix="/api/admin/analytics", tags=["admin-analytics"])


# --------------------------------------------------------------------------- #
# Public ingestion                                                            #
# --------------------------------------------------------------------------- #


class PageViewEventRequest(BaseModel):
    # Keyfi metadata / mass-assignment'i reddet: bilinmeyen alanlar 422.
    model_config = ConfigDict(extra="forbid")

    event_type: Literal["page_view"] = "page_view"
    path: str | None = Field(default=None, max_length=2048)
    referrer: str | None = Field(default=None, max_length=2048)
    utm_source: str | None = Field(default=None, max_length=300)
    utm_medium: str | None = Field(default=None, max_length=300)
    utm_campaign: str | None = Field(default=None, max_length=300)
    utm_content: str | None = Field(default=None, max_length=300)
    utm_term: str | None = Field(default=None, max_length=300)
    ref: str | None = Field(default=None, max_length=200)
    # Istemcinin analitik izni (consent). `ANALYTICS_REQUIRE_CONSENT=true` iken
    # False/None ise pazarlama analitigi islenmez.
    consent: bool = False
    # Frontend yeniden denemesinde ayni olayin iki kez kaydedilmesini engelleyen
    # istemci uretimi idempotency anahtari (dedup_key olarak kullanilir).
    event_id: uuid.UUID | None = None


def _optional_principal(request: Request) -> Principal | None:
    """Access token cookie'sinden (varsa) dogrulanmis kimligi cozer; yoksa None.

    Ingestion ucu kimlik dogrulamasi ZORUNLU KILMAZ - kullanici giris yapmissa
    olayi ona/organizasyonuna baglar, aksi halde anonim birakir."""

    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        return Principal(
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            role=payload["role"],
            is_demo=bool(payload.get("demo", False)),
        )
    except (InvalidAccessTokenError, KeyError, ValueError):
        return None


@public_router.post("/events", status_code=204)
async def ingest_event(
    body: PageViewEventRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Anonim bir page_view olayini kaydeder (gizlilik dostu, salt-okunur analitik).

    Basari/gizli-red durumunda daima 204 doner (istemci akisini bloklamaz)."""

    response = Response(status_code=204)

    # 1) Ana anahtar kapali -> hicbir sey kaydetme, cookie set etme (no-op).
    if not settings.analytics_enabled:
        return response

    # 2) Yalnizca izin verilen olay turu.
    if body.event_type != AnalyticsEventType.PAGE_VIEW.value:
        raise HTTPException(status_code=422, detail="Desteklenmeyen olay turu")

    # 3) IP basina hiz siniri (kayit tablosunu sel etmeye karsi).
    client_host = request.client.host if request.client else "unknown"
    if await rate_limit.is_analytics_ingest_rate_limited(redis_client, client_host):
        # Sessizce reddet (204) - istemciye ayrinti sizdirma, akisi bozma.
        return response

    # 4) Consent kapisi: izin gerekliyse ve verilmemisse pazarlama analitigi yok.
    if settings.analytics_require_consent and not body.consent:
        return response

    now = datetime.now(UTC)
    attribution = analytics_service.sanitize_attribution(
        utm_source=body.utm_source,
        utm_medium=body.utm_medium,
        utm_campaign=body.utm_campaign,
        utm_content=body.utm_content,
        utm_term=body.utm_term,
        referral_code=body.ref,
        referrer=body.referrer,
    )
    path = analytics_service.normalize_path(body.path)
    user_agent = request.headers.get("user-agent")
    device_category = analytics_service.classify_device(user_agent)
    browser = analytics_service.browser_family(user_agent)
    os_name = analytics_service.os_family(user_agent)
    country = None
    if settings.analytics_country_header:
        raw_country = request.headers.get(settings.analytics_country_header)
        if raw_country and len(raw_country.strip()) == 2 and raw_country.strip().isalpha():
            country = raw_country.strip().upper()

    principal = _optional_principal(request)
    user_id = principal.user_id if principal else None
    organization_id = principal.organization_id if principal else None

    visitor_cookie = _parse_uuid(request.cookies.get(ANALYTICS_VISITOR_COOKIE))
    session_cookie = _parse_uuid(request.cookies.get(ANALYTICS_SESSION_COOKIE))

    visitor = await analytics_service.get_or_create_visitor(
        session,
        visitor_id=visitor_cookie,
        attribution=attribution,
        landing_path=path,
        device_category=device_category,
        browser=browser,
        os_name=os_name,
        now=now,
    )
    visit, session_created = await analytics_service.get_or_create_session(
        session,
        visitor=visitor,
        session_id=session_cookie,
        attribution=attribution,
        landing_path=path,
        device_category=device_category,
        browser=browser,
        os_name=os_name,
        user_id=user_id,
        organization_id=organization_id,
        now=now,
    )

    if session_created:
        await analytics_service.insert_event(
            session,
            event_type=AnalyticsEventType.VISITOR_SESSION_STARTED,
            visitor_id=visitor.id,
            session_id=visit.id,
            path=path,
            attribution=attribution,
            device_category=device_category,
            browser=browser,
            os_name=os_name,
            country=country,
            user_id=user_id,
            organization_id=organization_id,
        )

    dedup_key = f"page_view:{body.event_id}" if body.event_id else None
    await analytics_service.insert_event(
        session,
        event_type=AnalyticsEventType.PAGE_VIEW,
        visitor_id=visitor.id,
        session_id=visit.id,
        path=path,
        attribution=attribution,
        device_category=device_category,
        browser=browser,
        os_name=os_name,
        country=country,
        user_id=user_id,
        organization_id=organization_id,
        dedup_key=dedup_key,
    )
    await session.commit()

    # Sunucu tarafinda uretilen/dogrulanan kimlikleri cookie'lere yaz.
    set_analytics_visitor_cookie(response, str(visitor.id))
    set_analytics_session_cookie(response, str(visit.id))
    return response


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Takip baglantisi yonlendirmesi (open-redirect korumali)                     #
# --------------------------------------------------------------------------- #


@public_router.get("/track/{code}")
async def track_link_redirect(
    code: str,
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Aktif bir takip baglantisini YALNIZCA izin verilen dahili bir yola 302'ler.

    Yonlendirme hedefi HER ZAMAN dogrulanmis, goreli bir dahili yoldur (open
    redirect YOK); UTM/`ref` parametreleri sonrasi sayfa page_view olayina
    yansir ve boylece link istatistikleri olusur."""

    link = (
        await session.execute(
            select(TrackingLink).where(TrackingLink.referral_code == code, TrackingLink.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Takip baglantisi bulunamadi")

    try:
        dest = analytics_service.validate_internal_path(link.destination_path)
    except analytics_service.InvalidInternalPathError as exc:
        # Kayitli hedef bir sekilde gecersizse (savunma amacli) yonlendirme yapma.
        raise HTTPException(status_code=400, detail="Gecersiz hedef yol") from exc

    params: list[tuple[str, str]] = []
    for key, value in (
        ("utm_source", link.utm_source),
        ("utm_medium", link.utm_medium),
        ("utm_campaign", link.utm_campaign),
        ("utm_content", link.utm_content),
        ("ref", link.referral_code),
    ):
        if value:
            params.append((key, value))
    query = "&".join(f"{k}={_url_quote(v)}" for k, v in params)
    base = settings.cors_allowed_origin.rstrip("/")
    target = f"{base}{dest}"
    if query:
        target = f"{target}{'&' if '?' in target else '?'}{query}"
    return RedirectResponse(url=target, status_code=302)


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


# --------------------------------------------------------------------------- #
# Admin: ortak filtre / tarih araligi yardimcilari                            #
# --------------------------------------------------------------------------- #


def _day_bounds(start: date | None, end: date | None) -> tuple[datetime, datetime]:
    """Tarih araligini UTC yaris-acik [start_of_day, end_of_next_day) olarak dondurur.

    Varsayilan: son 30 gun. `end` dahil sayilir (bitis gunun sonuna kadar)."""

    today = datetime.now(UTC).date()
    resolved_end = end or today
    resolved_start = start or (resolved_end - timedelta(days=29))
    start_dt = datetime.combine(resolved_start, time.min, tzinfo=UTC)
    # Bitis sinirini bir sonraki gunun basi yaparak `end` gununu tam kapsariz.
    end_dt = datetime.combine(resolved_end + timedelta(days=1), time.min, tzinfo=UTC)
    return start_dt, end_dt


def _page_view_filters(
    start_dt: datetime,
    end_dt: datetime,
    *,
    source: str | None,
    campaign: str | None,
    organization_id: uuid.UUID | None,
):
    """page_view olaylari icin ortak WHERE kosullari (tarih + kaynak + kampanya + org)."""

    conditions = [
        AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW,
        AnalyticsEvent.created_at >= start_dt,
        AnalyticsEvent.created_at < end_dt,
    ]
    if source:
        conditions.append(
            or_(
                AnalyticsEvent.utm_source == source,
                AnalyticsEvent.referrer_domain == source,
            )
        )
    if campaign:
        conditions.append(AnalyticsEvent.utm_campaign == campaign)
    if organization_id:
        conditions.append(AnalyticsEvent.organization_id == organization_id)
    return conditions


async def _scalar_int(session: AsyncSession, stmt: Select) -> int:
    return int((await session.scalar(stmt)) or 0)


# --------------------------------------------------------------------------- #
# Admin: Genel Bakis (metrikler + zaman serisi + top listeler + huni)         #
# --------------------------------------------------------------------------- #


class AnalyticsMetrics(BaseModel):
    total_page_views: int
    total_unique_visitors: int
    unique_visitors_today: int
    unique_visitors_7d: int
    unique_visitors_30d: int
    total_users: int
    total_organizations: int
    new_users_in_range: int
    new_organizations_in_range: int
    successful_logins_in_range: int
    unique_login_users_in_range: int
    visitor_to_signup_rate: float
    signup_to_first_login_rate: float
    campaign_referred_visitors: int
    campaign_referred_signups: int


class TimeSeriesPoint(BaseModel):
    day: date
    visits: int
    signups: int
    logins: int


class LabeledCount(BaseModel):
    label: str
    visitors: int
    events: int


class CampaignStat(BaseModel):
    campaign: str
    visitors: int
    signups: int


class FunnelResponse(BaseModel):
    visitors: int
    signups: int
    organizations: int
    first_tests: int


class AnalyticsOverviewResponse(BaseModel):
    range_start: date
    range_end: date
    metrics: AnalyticsMetrics
    timeseries: list[TimeSeriesPoint]
    top_pages: list[LabeledCount]
    top_sources: list[LabeledCount]
    top_campaigns: list[CampaignStat]
    funnel: FunnelResponse


def _distinct_visitors(conditions) -> Select:
    return select(func.count(func.distinct(AnalyticsEvent.visitor_id))).where(and_(*conditions))


@admin_router.get("/overview", response_model=AnalyticsOverviewResponse)
async def get_overview(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    source: str | None = Query(default=None, max_length=200),
    campaign: str | None = Query(default=None, max_length=200),
    organization_id: uuid.UUID | None = Query(default=None),
) -> AnalyticsOverviewResponse:
    start_dt, end_dt = _day_bounds(start, end)
    now = datetime.now(UTC)
    today_start = datetime.combine(now.date(), time.min, tzinfo=UTC)

    pv = _page_view_filters(
        start_dt, end_dt, source=source, campaign=campaign, organization_id=organization_id
    )

    # --- Metrikler ---
    total_page_views = await _scalar_int(session, select(func.count()).where(and_(*pv)))
    total_unique_visitors = await _scalar_int(
        session,
        _distinct_visitors(
            [AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW]
            + ([AnalyticsEvent.utm_source == source] if source else [])
            + ([AnalyticsEvent.utm_campaign == campaign] if campaign else [])
        ),
    )
    unique_visitors_today = await _scalar_int(
        session,
        _distinct_visitors(
            [
                AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW,
                AnalyticsEvent.created_at >= today_start,
            ]
        ),
    )
    unique_visitors_7d = await _scalar_int(
        session,
        _distinct_visitors(
            [
                AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW,
                AnalyticsEvent.created_at >= now - timedelta(days=7),
            ]
        ),
    )
    unique_visitors_30d = await _scalar_int(
        session,
        _distinct_visitors(
            [
                AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW,
                AnalyticsEvent.created_at >= now - timedelta(days=30),
            ]
        ),
    )
    total_users = await _scalar_int(session, select(func.count()).select_from(User))
    total_organizations = await _scalar_int(session, select(func.count()).select_from(Organization))
    new_users_in_range = await _scalar_int(
        session,
        select(func.count()).select_from(User).where(User.created_at >= start_dt, User.created_at < end_dt),
    )
    new_organizations_in_range = await _scalar_int(
        session,
        select(func.count())
        .select_from(Organization)
        .where(Organization.created_at >= start_dt, Organization.created_at < end_dt),
    )
    login_conditions = [
        AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED,
        AnalyticsEvent.created_at >= start_dt,
        AnalyticsEvent.created_at < end_dt,
    ]
    successful_logins_in_range = await _scalar_int(
        session, select(func.count()).where(and_(*login_conditions))
    )
    unique_login_users_in_range = await _scalar_int(
        session,
        select(func.count(func.distinct(AnalyticsEvent.user_id))).where(and_(*login_conditions)),
    )
    campaign_referred_visitors = await _scalar_int(
        session,
        _distinct_visitors(
            pv
            + [
                or_(
                    AnalyticsEvent.referral_code.is_not(None),
                    AnalyticsEvent.utm_campaign.is_not(None),
                )
            ]
        ),
    )
    campaign_referred_signups = await _scalar_int(
        session,
        select(func.count())
        .select_from(UserAcquisitionAttribution)
        .where(
            UserAcquisitionAttribution.created_at >= start_dt,
            UserAcquisitionAttribution.created_at < end_dt,
            or_(
                UserAcquisitionAttribution.first_referral_code.is_not(None),
                UserAcquisitionAttribution.first_utm_campaign.is_not(None),
            ),
        ),
    )

    visitor_to_signup_rate = (
        round(new_users_in_range / total_unique_visitors, 4) if total_unique_visitors else 0.0
    )
    users_with_login = await _scalar_int(
        session,
        select(func.count(func.distinct(AnalyticsEvent.user_id))).where(
            AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED
        ),
    )
    signup_to_first_login_rate = round(users_with_login / total_users, 4) if total_users else 0.0

    metrics = AnalyticsMetrics(
        total_page_views=total_page_views,
        total_unique_visitors=total_unique_visitors,
        unique_visitors_today=unique_visitors_today,
        unique_visitors_7d=unique_visitors_7d,
        unique_visitors_30d=unique_visitors_30d,
        total_users=total_users,
        total_organizations=total_organizations,
        new_users_in_range=new_users_in_range,
        new_organizations_in_range=new_organizations_in_range,
        successful_logins_in_range=successful_logins_in_range,
        unique_login_users_in_range=unique_login_users_in_range,
        visitor_to_signup_rate=visitor_to_signup_rate,
        signup_to_first_login_rate=signup_to_first_login_rate,
        campaign_referred_visitors=campaign_referred_visitors,
        campaign_referred_signups=campaign_referred_signups,
    )

    timeseries = await _build_timeseries(session, start_dt, end_dt, pv)
    top_pages = await _top_pages(session, pv)
    top_sources = await _top_sources(session, start_dt, end_dt, organization_id)
    top_campaigns = await _top_campaigns(session, start_dt, end_dt)
    funnel = await _funnel(session, start_dt, end_dt, pv)

    return AnalyticsOverviewResponse(
        range_start=start_dt.date(),
        range_end=(end_dt - timedelta(days=1)).date(),
        metrics=metrics,
        timeseries=timeseries,
        top_pages=top_pages,
        top_sources=top_sources,
        top_campaigns=top_campaigns,
        funnel=funnel,
    )


async def _build_timeseries(session, start_dt, end_dt, pv_conditions) -> list[TimeSeriesPoint]:
    day = func.date_trunc("day", AnalyticsEvent.created_at)

    async def _daily(conditions) -> dict[date, int]:
        rows = (
            await session.execute(
                select(day.label("d"), func.count().label("c")).where(and_(*conditions)).group_by(day)
            )
        ).all()
        return {r.d.date(): int(r.c) for r in rows}

    visits = await _daily(pv_conditions)
    signups = await _daily(
        [
            AnalyticsEvent.event_type == AnalyticsEventType.SIGNUP_COMPLETED,
            AnalyticsEvent.created_at >= start_dt,
            AnalyticsEvent.created_at < end_dt,
        ]
    )
    logins = await _daily(
        [
            AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED,
            AnalyticsEvent.created_at >= start_dt,
            AnalyticsEvent.created_at < end_dt,
        ]
    )

    points: list[TimeSeriesPoint] = []
    cursor = start_dt.date()
    last = (end_dt - timedelta(days=1)).date()
    # Cok genis araliklarda seriyi sinirla (guvenlik/performans): en fazla 366 gun.
    guard = 0
    while cursor <= last and guard < 366:
        points.append(
            TimeSeriesPoint(
                day=cursor,
                visits=visits.get(cursor, 0),
                signups=signups.get(cursor, 0),
                logins=logins.get(cursor, 0),
            )
        )
        cursor += timedelta(days=1)
        guard += 1
    return points


async def _top_pages(session, pv_conditions) -> list[LabeledCount]:
    rows = (
        await session.execute(
            select(
                func.coalesce(AnalyticsEvent.path, "(bilinmiyor)").label("label"),
                func.count(func.distinct(AnalyticsEvent.visitor_id)).label("visitors"),
                func.count().label("events"),
            )
            .where(and_(*pv_conditions))
            .group_by(AnalyticsEvent.path)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()
    return [LabeledCount(label=r.label, visitors=int(r.visitors), events=int(r.events)) for r in rows]


async def _top_sources(session, start_dt, end_dt, organization_id) -> list[LabeledCount]:
    label = func.coalesce(AnalyticsEvent.utm_source, AnalyticsEvent.referrer_domain, "dogrudan")
    conditions = [
        AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW,
        AnalyticsEvent.created_at >= start_dt,
        AnalyticsEvent.created_at < end_dt,
    ]
    if organization_id:
        conditions.append(AnalyticsEvent.organization_id == organization_id)
    rows = (
        await session.execute(
            select(
                label.label("label"),
                func.count(func.distinct(AnalyticsEvent.visitor_id)).label("visitors"),
                func.count().label("events"),
            )
            .where(and_(*conditions))
            .group_by(label)
            .order_by(func.count(func.distinct(AnalyticsEvent.visitor_id)).desc())
            .limit(10)
        )
    ).all()
    return [LabeledCount(label=r.label, visitors=int(r.visitors), events=int(r.events)) for r in rows]


async def _top_campaigns(session, start_dt, end_dt) -> list[CampaignStat]:
    visitor_rows = (
        await session.execute(
            select(
                AnalyticsEvent.utm_campaign.label("campaign"),
                func.count(func.distinct(AnalyticsEvent.visitor_id)).label("visitors"),
            )
            .where(
                AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW,
                AnalyticsEvent.created_at >= start_dt,
                AnalyticsEvent.created_at < end_dt,
                AnalyticsEvent.utm_campaign.is_not(None),
            )
            .group_by(AnalyticsEvent.utm_campaign)
            .order_by(func.count(func.distinct(AnalyticsEvent.visitor_id)).desc())
            .limit(10)
        )
    ).all()
    signup_rows = (
        await session.execute(
            select(
                UserAcquisitionAttribution.first_utm_campaign.label("campaign"),
                func.count().label("signups"),
            )
            .where(UserAcquisitionAttribution.first_utm_campaign.is_not(None))
            .group_by(UserAcquisitionAttribution.first_utm_campaign)
        )
    ).all()
    signups_by_campaign = {r.campaign: int(r.signups) for r in signup_rows}
    return [
        CampaignStat(
            campaign=r.campaign,
            visitors=int(r.visitors),
            signups=signups_by_campaign.get(r.campaign, 0),
        )
        for r in visitor_rows
    ]


async def _funnel(session, start_dt, end_dt, pv_conditions) -> FunnelResponse:
    visitors = await _scalar_int(session, _distinct_visitors(pv_conditions))

    async def _count(event_type) -> int:
        return await _scalar_int(
            session,
            select(func.count()).where(
                AnalyticsEvent.event_type == event_type,
                AnalyticsEvent.created_at >= start_dt,
                AnalyticsEvent.created_at < end_dt,
            ),
        )

    return FunnelResponse(
        visitors=visitors,
        signups=await _count(AnalyticsEventType.SIGNUP_COMPLETED),
        organizations=await _count(AnalyticsEventType.ORGANIZATION_CREATED),
        first_tests=await _count(AnalyticsEventType.FIRST_TEST_STARTED),
    )


# --------------------------------------------------------------------------- #
# Admin: Ziyaretler (olay listesi)                                            #
# --------------------------------------------------------------------------- #


class VisitEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    occurred_at: datetime
    path: str | None
    referrer_domain: str | None
    utm_source: str | None
    utm_campaign: str | None
    referral_code: str | None
    device_category: str | None
    browser_family: str | None
    os_family: str | None
    country: str | None


class VisitListResponse(BaseModel):
    total: int
    events: list[VisitEventResponse]


@admin_router.get("/visits", response_model=VisitListResponse)
async def list_visits(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    event_type: AnalyticsEventType | None = Query(default=None),
    source: str | None = Query(default=None, max_length=200),
    campaign: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> VisitListResponse:
    start_dt, end_dt = _day_bounds(start, end)
    conditions = [AnalyticsEvent.created_at >= start_dt, AnalyticsEvent.created_at < end_dt]
    if event_type is not None:
        conditions.append(AnalyticsEvent.event_type == event_type)
    if source:
        conditions.append(or_(AnalyticsEvent.utm_source == source, AnalyticsEvent.referrer_domain == source))
    if campaign:
        conditions.append(AnalyticsEvent.utm_campaign == campaign)

    total = await _scalar_int(session, select(func.count()).where(and_(*conditions)))
    rows = (
        (
            await session.execute(
                select(AnalyticsEvent)
                .where(and_(*conditions))
                .order_by(AnalyticsEvent.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return VisitListResponse(
        total=total,
        events=[
            VisitEventResponse(
                id=r.id,
                event_type=r.event_type.value,
                occurred_at=r.created_at,
                path=r.path,
                referrer_domain=r.referrer_domain,
                utm_source=r.utm_source,
                utm_campaign=r.utm_campaign,
                referral_code=r.referral_code,
                device_category=r.device_category,
                browser_family=r.browser_family,
                os_family=r.os_family,
                country=r.country,
            )
            for r in rows
        ],
    )


# --------------------------------------------------------------------------- #
# Admin: Kullanici Girisleri (dogrulanmis join + turetilmis giris istatistigi) #
# --------------------------------------------------------------------------- #


class UserLoginStatsResponse(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    email: str
    organization_name: str | None
    role: str | None
    registered_at: datetime
    first_login_at: datetime | None
    last_login_at: datetime | None
    total_logins: int
    logins_7d: int
    logins_30d: int
    last_activity_at: datetime | None
    account_status: str
    first_source: str | None
    first_campaign: str | None
    last_source: str | None
    last_campaign: str | None


class UserLoginStatsListResponse(BaseModel):
    total: int
    users: list[UserLoginStatsResponse]


def _primary_membership_subquery():
    """Her kullanici icin (varsa) EN ERKEN uyeligi: DISTINCT ON (user_id)."""

    return (
        select(
            Membership.user_id.label("user_id"),
            Membership.role.label("role"),
            Organization.name.label("org_name"),
        )
        .join(Organization, Organization.id == Membership.organization_id)
        .order_by(Membership.user_id, Membership.created_at.asc())
        .distinct(Membership.user_id)
        .subquery()
    )


def _login_agg_subquery(now: datetime):
    return (
        select(
            AnalyticsEvent.user_id.label("user_id"),
            func.count().label("total_logins"),
            func.min(AnalyticsEvent.created_at).label("first_login"),
            func.max(AnalyticsEvent.created_at).label("last_login"),
            func.count().filter(AnalyticsEvent.created_at >= now - timedelta(days=7)).label("logins_7d"),
            func.count().filter(AnalyticsEvent.created_at >= now - timedelta(days=30)).label("logins_30d"),
        )
        .where(
            AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED,
            AnalyticsEvent.user_id.is_not(None),
        )
        .group_by(AnalyticsEvent.user_id)
        .subquery()
    )


def _account_status(user: User) -> str:
    if user.is_platform_admin:
        return "admin"
    demo_email = settings.demo_account_email
    if demo_email and user.email_normalized == demo_email.strip().lower():
        return "demo"
    return "active"


def _build_users_query(now: datetime, *, search: str | None, status: str | None):
    membership = _primary_membership_subquery()
    login_agg = _login_agg_subquery(now)
    attribution = UserAcquisitionAttribution

    query = (
        select(
            User,
            membership.c.org_name,
            membership.c.role,
            login_agg.c.total_logins,
            login_agg.c.first_login,
            login_agg.c.last_login,
            login_agg.c.logins_7d,
            login_agg.c.logins_30d,
            attribution.first_utm_source,
            attribution.first_utm_campaign,
            attribution.last_utm_source,
            attribution.last_utm_campaign,
        )
        .outerjoin(membership, membership.c.user_id == User.id)
        .outerjoin(login_agg, login_agg.c.user_id == User.id)
        .outerjoin(attribution, attribution.user_id == User.id)
    )
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                User.email.ilike(pattern),
                User.display_name.ilike(pattern),
                membership.c.org_name.ilike(pattern),
            )
        )
    if status in {"active", "admin", "demo"}:
        # Durum turetilmis oldugu icin SQL'de yaklasik filtre uygulanir; kesin
        # ayrim serialize sirasinda `_account_status` ile de dogrulanir.
        if status == "admin":
            query = query.where(User.is_platform_admin.is_(True))
        elif status == "active":
            query = query.where(User.is_platform_admin.is_(False))
    return query, login_agg


@admin_router.get("/users", response_model=UserLoginStatsListResponse)
async def list_user_login_stats(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
    search: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None),
    sort: Literal["last_login", "registered", "total_logins"] = Query(default="last_login"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserLoginStatsListResponse:
    now = datetime.now(UTC)
    query, login_agg = _build_users_query(now, search=search, status=status)

    count_query = select(func.count()).select_from(query.order_by(None).subquery())
    total = await _scalar_int(session, count_query)

    if sort == "registered":
        query = query.order_by(User.created_at.desc())
    elif sort == "total_logins":
        query = query.order_by(login_agg.c.total_logins.desc().nullslast(), User.created_at.desc())
    else:
        query = query.order_by(login_agg.c.last_login.desc().nullslast(), User.created_at.desc())

    rows = (await session.execute(query.limit(limit).offset(offset))).all()
    return UserLoginStatsListResponse(total=total, users=[_serialize_user_row(r) for r in rows])


def _serialize_user_row(row) -> UserLoginStatsResponse:
    (
        user,
        org_name,
        role,
        total_logins,
        first_login,
        last_login,
        logins_7d,
        logins_30d,
        first_source,
        first_campaign,
        last_source,
        last_campaign,
    ) = row
    return UserLoginStatsResponse(
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        organization_name=org_name,
        role=role,
        registered_at=user.created_at,
        first_login_at=first_login,
        last_login_at=last_login,
        total_logins=int(total_logins or 0),
        logins_7d=int(logins_7d or 0),
        logins_30d=int(logins_30d or 0),
        last_activity_at=last_login,
        account_status=_account_status(user),
        first_source=first_source,
        first_campaign=first_campaign,
        last_source=last_source,
        last_campaign=last_campaign,
    )


_USER_CSV_MAX_ROWS = 5000
_USER_CSV_HEADERS = [
    "Kullanici",
    "E-posta",
    "Sirket",
    "Rol",
    "Kayit tarihi",
    "Ilk giris",
    "Son giris",
    "Toplam giris",
    "Son 7 gun giris",
    "Son 30 gun giris",
    "Hesap durumu",
    "Ilk kaynak",
    "Ilk kampanya",
    "Son kaynak",
    "Son kampanya",
]


@admin_router.get("/users/export.csv")
async def export_user_login_stats_csv(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
    search: str | None = Query(default=None, max_length=200),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """Kullanici giris istatistiklerini CSV olarak disa aktarir (yalnizca platform admin).

    Formula injection'a karsi `=`, `+`, `-`, `@` ile baslayan hucreler
    `escape_csv_cell` ile guvenli hale getirilir."""

    now = datetime.now(UTC)
    query, login_agg = _build_users_query(now, search=search, status=status)
    query = query.order_by(login_agg.c.last_login.desc().nullslast(), User.created_at.desc()).limit(
        _USER_CSV_MAX_ROWS
    )
    rows = (await session.execute(query)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_USER_CSV_HEADERS)
    esc = analytics_service.escape_csv_cell
    for row in rows:
        r = _serialize_user_row(row)
        writer.writerow(
            [
                esc(r.display_name or ""),
                esc(r.email),
                esc(r.organization_name or ""),
                esc(r.role or ""),
                esc(r.registered_at.isoformat()),
                esc(r.first_login_at.isoformat() if r.first_login_at else ""),
                esc(r.last_login_at.isoformat() if r.last_login_at else ""),
                esc(r.total_logins),
                esc(r.logins_7d),
                esc(r.logins_30d),
                esc(r.account_status),
                esc(r.first_source or ""),
                esc(r.first_campaign or ""),
                esc(r.last_source or ""),
                esc(r.last_campaign or ""),
            ]
        )
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="kullanici-giris-istatistikleri.csv"'},
    )


# --------------------------------------------------------------------------- #
# Admin: Sirketler                                                            #
# --------------------------------------------------------------------------- #


class OrganizationStatsResponse(BaseModel):
    organization_id: uuid.UUID
    name: str
    created_at: datetime
    member_count: int
    active_users_30d: int
    first_login_at: datetime | None
    last_activity_at: datetime | None
    total_logins: int
    project_count: int
    completed_tests: int
    first_source: str | None
    first_campaign: str | None
    last_source: str | None
    last_campaign: str | None


class OrganizationStatsListResponse(BaseModel):
    total: int
    organizations: list[OrganizationStatsResponse]


def _org_login_agg_subquery(now: datetime):
    return (
        select(
            AnalyticsEvent.organization_id.label("organization_id"),
            func.count().label("total_logins"),
            func.min(AnalyticsEvent.created_at).label("first_login"),
            func.max(AnalyticsEvent.created_at).label("last_login"),
            func.count(func.distinct(AnalyticsEvent.user_id))
            .filter(AnalyticsEvent.created_at >= now - timedelta(days=30))
            .label("active_users_30d"),
        )
        .where(
            AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED,
            AnalyticsEvent.organization_id.is_not(None),
        )
        .group_by(AnalyticsEvent.organization_id)
        .subquery()
    )


def _org_scalar_count_subquery(model):
    return (
        select(model.organization_id.label("organization_id"), func.count().label("c"))
        .group_by(model.organization_id)
        .subquery()
    )


@admin_router.get("/organizations", response_model=OrganizationStatsListResponse)
async def list_organization_stats(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
    search: str | None = Query(default=None, max_length=200),
    sort: Literal["last_activity", "created", "members"] = Query(default="last_activity"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> OrganizationStatsListResponse:
    now = datetime.now(UTC)
    members = _org_scalar_count_subquery(Membership)
    projects = _org_scalar_count_subquery(Project)
    reports = _org_scalar_count_subquery(Report)
    login_agg = _org_login_agg_subquery(now)
    attribution = OrganizationAcquisitionAttribution

    query = (
        select(
            Organization,
            func.coalesce(members.c.c, 0).label("member_count"),
            func.coalesce(projects.c.c, 0).label("project_count"),
            func.coalesce(reports.c.c, 0).label("report_count"),
            login_agg.c.total_logins,
            login_agg.c.first_login,
            login_agg.c.last_login,
            login_agg.c.active_users_30d,
            attribution.first_utm_source,
            attribution.first_utm_campaign,
            attribution.last_utm_source,
            attribution.last_utm_campaign,
        )
        .outerjoin(members, members.c.organization_id == Organization.id)
        .outerjoin(projects, projects.c.organization_id == Organization.id)
        .outerjoin(reports, reports.c.organization_id == Organization.id)
        .outerjoin(login_agg, login_agg.c.organization_id == Organization.id)
        .outerjoin(attribution, attribution.organization_id == Organization.id)
    )
    if search:
        query = query.where(Organization.name.ilike(f"%{search.strip()}%"))

    total = await _scalar_int(session, select(func.count()).select_from(query.order_by(None).subquery()))

    if sort == "created":
        query = query.order_by(Organization.created_at.desc())
    elif sort == "members":
        query = query.order_by(func.coalesce(members.c.c, 0).desc())
    else:
        query = query.order_by(login_agg.c.last_login.desc().nullslast(), Organization.created_at.desc())

    rows = (await session.execute(query.limit(limit).offset(offset))).all()
    return OrganizationStatsListResponse(
        total=total,
        organizations=[
            OrganizationStatsResponse(
                organization_id=org.id,
                name=org.name,
                created_at=org.created_at,
                member_count=int(member_count or 0),
                active_users_30d=int(active_users_30d or 0),
                first_login_at=first_login,
                last_activity_at=last_login,
                total_logins=int(total_logins or 0),
                project_count=int(project_count or 0),
                completed_tests=int(report_count or 0),
                first_source=first_source,
                first_campaign=first_campaign,
                last_source=last_source,
                last_campaign=last_campaign,
            )
            for (
                org,
                member_count,
                project_count,
                report_count,
                total_logins,
                first_login,
                last_login,
                active_users_30d,
                first_source,
                first_campaign,
                last_source,
                last_campaign,
            ) in rows
        ],
    )


class OrganizationMemberSummary(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    email: str
    role: str
    last_login_at: datetime | None
    total_logins: int


class OrganizationDetailResponse(BaseModel):
    organization_id: uuid.UUID
    name: str
    created_at: datetime
    member_count: int
    project_count: int
    completed_tests: int
    total_logins: int
    first_source: str | None
    first_campaign: str | None
    last_source: str | None
    last_campaign: str | None
    members: list[OrganizationMemberSummary]


@admin_router.get("/organizations/{org_id}", response_model=OrganizationDetailResponse)
async def get_organization_detail(
    org_id: uuid.UUID,
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> OrganizationDetailResponse:
    org = await session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Sirket bulunamadi")

    login_agg = (
        select(
            AnalyticsEvent.user_id.label("user_id"),
            func.count().label("total_logins"),
            func.max(AnalyticsEvent.created_at).label("last_login"),
        )
        .where(
            AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED,
            AnalyticsEvent.organization_id == org_id,
            AnalyticsEvent.user_id.is_not(None),
        )
        .group_by(AnalyticsEvent.user_id)
        .subquery()
    )
    member_rows = (
        await session.execute(
            select(User, Membership.role, login_agg.c.total_logins, login_agg.c.last_login)
            .join(Membership, Membership.user_id == User.id)
            .outerjoin(login_agg, login_agg.c.user_id == User.id)
            .where(Membership.organization_id == org_id)
            .order_by(login_agg.c.last_login.desc().nullslast(), User.created_at.asc())
        )
    ).all()

    project_count = await _scalar_int(
        session, select(func.count()).select_from(Project).where(Project.organization_id == org_id)
    )
    report_count = await _scalar_int(
        session, select(func.count()).select_from(Report).where(Report.organization_id == org_id)
    )
    total_logins = await _scalar_int(
        session,
        select(func.count()).where(
            AnalyticsEvent.event_type == AnalyticsEventType.LOGIN_SUCCEEDED,
            AnalyticsEvent.organization_id == org_id,
        ),
    )
    attribution = (
        await session.execute(
            select(OrganizationAcquisitionAttribution).where(
                OrganizationAcquisitionAttribution.organization_id == org_id
            )
        )
    ).scalar_one_or_none()

    return OrganizationDetailResponse(
        organization_id=org.id,
        name=org.name,
        created_at=org.created_at,
        member_count=len(member_rows),
        project_count=project_count,
        completed_tests=report_count,
        total_logins=total_logins,
        first_source=attribution.first_utm_source if attribution else None,
        first_campaign=attribution.first_utm_campaign if attribution else None,
        last_source=attribution.last_utm_source if attribution else None,
        last_campaign=attribution.last_utm_campaign if attribution else None,
        members=[
            OrganizationMemberSummary(
                user_id=user.id,
                display_name=user.display_name,
                email=user.email,
                role=role,
                last_login_at=last_login,
                total_logins=int(total_logins_u or 0),
            )
            for (user, role, total_logins_u, last_login) in member_rows
        ],
    )


# --------------------------------------------------------------------------- #
# Admin: Baglantilar / Kampanyalar (takip baglantisi CRUD + istatistik)       #
# --------------------------------------------------------------------------- #


class TrackingLinkStats(BaseModel):
    total_visits: int
    unique_visitors: int
    signups: int
    organizations: int
    first_tests: int
    conversion_rate: float
    first_visit_at: datetime | None
    last_visit_at: datetime | None


class TrackingLinkResponse(BaseModel):
    id: uuid.UUID
    name: str
    destination_path: str
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None
    utm_content: str | None
    referral_code: str
    description: str | None
    is_active: bool
    created_at: datetime
    tracking_url: str
    stats: TrackingLinkStats


class TrackingLinkCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    destination_path: str = Field(default="/", max_length=1024)
    utm_source: str | None = Field(default=None, max_length=200)
    utm_medium: str | None = Field(default=None, max_length=200)
    utm_campaign: str | None = Field(default=None, max_length=200)
    utm_content: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = True


class TrackingLinkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    destination_path: str | None = Field(default=None, max_length=1024)
    utm_source: str | None = Field(default=None, max_length=200)
    utm_medium: str | None = Field(default=None, max_length=200)
    utm_campaign: str | None = Field(default=None, max_length=200)
    utm_content: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


def _tracking_url(code: str) -> str:
    return f"/api/analytics/track/{code}"


async def _tracking_link_stats(session: AsyncSession, link: TrackingLink) -> TrackingLinkStats:
    # Bir baglantinin istatistigi, referral_code VEYA (varsa) kampanya eslesen
    # page_view olaylarindan turetilir.
    match = [AnalyticsEvent.referral_code == link.referral_code]
    if link.utm_campaign:
        match.append(AnalyticsEvent.utm_campaign == link.utm_campaign)
    match_clause = or_(*match) if len(match) > 1 else match[0]

    pv_clause = and_(AnalyticsEvent.event_type == AnalyticsEventType.PAGE_VIEW, match_clause)
    total_visits = await _scalar_int(session, select(func.count()).where(pv_clause))
    unique_visitors = await _scalar_int(
        session, select(func.count(func.distinct(AnalyticsEvent.visitor_id))).where(pv_clause)
    )
    first_last = (
        await session.execute(
            select(func.min(AnalyticsEvent.created_at), func.max(AnalyticsEvent.created_at)).where(pv_clause)
        )
    ).one()

    def _attr_clause():
        clauses = [UserAcquisitionAttribution.first_referral_code == link.referral_code]
        if link.utm_campaign:
            clauses.append(UserAcquisitionAttribution.first_utm_campaign == link.utm_campaign)
        return or_(*clauses) if len(clauses) > 1 else clauses[0]

    signups = await _scalar_int(
        session, select(func.count()).select_from(UserAcquisitionAttribution).where(_attr_clause())
    )

    def _org_attr_clause():
        clauses = [OrganizationAcquisitionAttribution.first_referral_code == link.referral_code]
        if link.utm_campaign:
            clauses.append(OrganizationAcquisitionAttribution.first_utm_campaign == link.utm_campaign)
        return or_(*clauses) if len(clauses) > 1 else clauses[0]

    organizations = await _scalar_int(
        session,
        select(func.count()).select_from(OrganizationAcquisitionAttribution).where(_org_attr_clause()),
    )
    # Ilk testler: bu baglantinin edinim kaynagiyla eslesen organizasyonlardan,
    # en az bir `first_test_started` olayi olanlarin sayisi (edinim tablosu ile
    # join - milestone olayina attribution damgalamaya gerek yok).
    first_tests = await _scalar_int(
        session,
        select(func.count(func.distinct(AnalyticsEvent.organization_id)))
        .select_from(AnalyticsEvent)
        .join(
            OrganizationAcquisitionAttribution,
            OrganizationAcquisitionAttribution.organization_id == AnalyticsEvent.organization_id,
        )
        .where(AnalyticsEvent.event_type == AnalyticsEventType.FIRST_TEST_STARTED, _org_attr_clause()),
    )
    conversion_rate = round(signups / unique_visitors, 4) if unique_visitors else 0.0
    return TrackingLinkStats(
        total_visits=total_visits,
        unique_visitors=unique_visitors,
        signups=signups,
        organizations=organizations,
        first_tests=first_tests,
        conversion_rate=conversion_rate,
        first_visit_at=first_last[0],
        last_visit_at=first_last[1],
    )


async def _serialize_tracking_link(session: AsyncSession, link: TrackingLink) -> TrackingLinkResponse:
    return TrackingLinkResponse(
        id=link.id,
        name=link.name,
        destination_path=link.destination_path,
        utm_source=link.utm_source,
        utm_medium=link.utm_medium,
        utm_campaign=link.utm_campaign,
        utm_content=link.utm_content,
        referral_code=link.referral_code,
        description=link.description,
        is_active=link.is_active,
        created_at=link.created_at,
        tracking_url=_tracking_url(link.referral_code),
        stats=await _tracking_link_stats(session, link),
    )


@admin_router.get("/tracking-links", response_model=list[TrackingLinkResponse])
async def list_tracking_links(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> list[TrackingLinkResponse]:
    links = (
        (await session.execute(select(TrackingLink).order_by(TrackingLink.created_at.desc()))).scalars().all()
    )
    return [await _serialize_tracking_link(session, link) for link in links]


@admin_router.post("/tracking-links", response_model=TrackingLinkResponse, status_code=201)
async def create_tracking_link(
    body: TrackingLinkCreateRequest,
    principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> TrackingLinkResponse:
    try:
        destination = analytics_service.validate_internal_path(body.destination_path)
    except analytics_service.InvalidInternalPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Cakisma ihtimali ihmal edilebilir; yine de birkac kez dene.
    referral_code = analytics_service.generate_referral_code()
    for _ in range(5):
        exists = await session.scalar(
            select(func.count()).select_from(TrackingLink).where(TrackingLink.referral_code == referral_code)
        )
        if not exists:
            break
        referral_code = analytics_service.generate_referral_code()

    link = TrackingLink(
        name=body.name.strip(),
        destination_path=destination,
        utm_source=analytics_service.clip_text(body.utm_source, 200),
        utm_medium=analytics_service.clip_text(body.utm_medium, 200),
        utm_campaign=analytics_service.clip_text(body.utm_campaign, 200),
        utm_content=analytics_service.clip_text(body.utm_content, 200),
        referral_code=referral_code,
        description=body.description,
        is_active=body.is_active,
        created_by_user_id=principal.user_id,
    )
    session.add(link)
    await session.commit()
    await session.refresh(link)
    logger.info(
        "Takip baglantisi olusturuldu",
        extra={"tracking_link_id": str(link.id), "actor_user_id": str(principal.user_id)},
    )
    return await _serialize_tracking_link(session, link)


@admin_router.patch("/tracking-links/{link_id}", response_model=TrackingLinkResponse)
async def update_tracking_link(
    link_id: uuid.UUID,
    body: TrackingLinkUpdateRequest,
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> TrackingLinkResponse:
    link = await session.get(TrackingLink, link_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Takip baglantisi bulunamadi")

    if body.destination_path is not None:
        try:
            link.destination_path = analytics_service.validate_internal_path(body.destination_path)
        except analytics_service.InvalidInternalPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.name is not None:
        link.name = body.name.strip()
    if body.utm_source is not None:
        link.utm_source = analytics_service.clip_text(body.utm_source, 200)
    if body.utm_medium is not None:
        link.utm_medium = analytics_service.clip_text(body.utm_medium, 200)
    if body.utm_campaign is not None:
        link.utm_campaign = analytics_service.clip_text(body.utm_campaign, 200)
    if body.utm_content is not None:
        link.utm_content = analytics_service.clip_text(body.utm_content, 200)
    if body.description is not None:
        link.description = body.description
    if body.is_active is not None:
        link.is_active = body.is_active

    await session.commit()
    await session.refresh(link)
    return await _serialize_tracking_link(session, link)
