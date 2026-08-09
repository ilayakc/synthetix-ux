import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_current_principal, get_organization_id
from app.logging_config import get_logger
from app.models.audit import AuditLog
from app.models.billing import ChipLedgerEntry, ChipTopUpRequest
from app.services import chip_ledger, quotes
from app.services import entitlements as entitlements_service
from app.services.chip_packages import CURRENT_CHIP_PACKAGE_VERSION, get_chip_package, get_chip_packages
from app.services.pricing import CURRENT_PRICING_VERSION

audit_logger = get_logger("audit")

router = APIRouter(prefix="/api/billing", tags=["billing"])


class EntitlementSummary(BaseModel):
    feature_key: str
    status: str
    quantity: int
    reserved_until: datetime | None = None


class UsageSummaryResponse(BaseModel):
    organization_id: uuid.UUID
    chip_balance: int
    entitlements: list[EntitlementSummary]
    pricing_version: str


@router.get("/usage-summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> UsageSummaryResponse:
    """Organizasyonun Chip bakiyesi ile iki ucretsiz hakkinin ozetini dondurur."""

    balance = await chip_ledger.get_chip_balance(session, organization_id)
    free_entitlements = await entitlements_service.list_free_entitlements(session, organization_id)
    await session.commit()

    return UsageSummaryResponse(
        organization_id=organization_id,
        chip_balance=balance,
        entitlements=[
            EntitlementSummary(
                feature_key=entitlement.feature_key,
                status=entitlement.status.value,
                quantity=entitlement.quantity,
                reserved_until=entitlement.reserved_until,
            )
            for entitlement in free_entitlements
        ],
        pricing_version=CURRENT_PRICING_VERSION,
    )


class QuoteRequest(BaseModel):
    persona_count: int = Field(ge=0)
    test_type: str
    modules: list[str] = Field(default_factory=list)


class QuoteLineItemResponse(BaseModel):
    key: str
    label: str
    quantity: int
    unit_chip_cost: int
    chip_cost: int
    covered_by_free_entitlement: bool


class QuoteResponse(BaseModel):
    pricing_version: str
    test_type: str
    persona_count: int
    modules: list[str]
    free_entitlement_feature_key: str | None
    free_entitlement_applicable: bool
    line_items: list[QuoteLineItemResponse]
    required_chips: int
    # `ai_report` seciliyse 50, degilse 0 - baseline `required_chips`ten AYRI
    # tutulan AI raporu ucreti (bkz. app.services.quotes.QuoteResult).
    ai_report_chips: int = 0
    total_chips: int


@router.post("/quote", response_model=QuoteResponse)
async def post_quote(
    body: QuoteRequest,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> QuoteResponse:
    """Persona sayisi, modul listesi ve test tipine gore fiyat teklifi hesaplar. Yan etkisizdir."""

    try:
        quote = await quotes.build_quote(
            session,
            organization_id,
            persona_count=body.persona_count,
            test_type=body.test_type,
            modules=body.modules,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()

    return QuoteResponse(
        pricing_version=quote.pricing_version,
        test_type=quote.test_type,
        persona_count=quote.persona_count,
        modules=list(quote.modules),
        free_entitlement_feature_key=quote.free_entitlement_feature_key,
        free_entitlement_applicable=quote.free_entitlement_applicable,
        line_items=[
            QuoteLineItemResponse(
                key=item.key,
                label=item.label,
                quantity=item.quantity,
                unit_chip_cost=item.unit_chip_cost,
                chip_cost=item.chip_cost,
                covered_by_free_entitlement=item.covered_by_free_entitlement,
            )
            for item in quote.line_items
        ],
        required_chips=quote.required_chips,
        ai_report_chips=quote.ai_report_chips,
        total_chips=quote.total_chips,
    )


class ChipLedgerEntryResponse(BaseModel):
    id: uuid.UUID
    amount: int
    entry_type: str
    reason: str
    reference_type: str | None
    reference_id: uuid.UUID | None
    created_at: datetime


@router.get("/chip-ledger", response_model=list[ChipLedgerEntryResponse])
async def list_chip_ledger(
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
    offset: int = 0,
) -> list[ChipLedgerEntryResponse]:
    """Organizasyonun Chip defter hareketlerini en yeniden eskiye listeler."""

    bounded_limit = max(1, min(limit, 200))
    result = await session.execute(
        select(ChipLedgerEntry)
        .where(ChipLedgerEntry.organization_id == organization_id)
        .order_by(ChipLedgerEntry.created_at.desc())
        .limit(bounded_limit)
        .offset(max(0, offset))
    )
    entries = result.scalars().all()

    return [
        ChipLedgerEntryResponse(
            id=entry.id,
            amount=entry.amount,
            entry_type=entry.entry_type.value,
            reason=entry.reason,
            reference_type=entry.reference_type,
            reference_id=entry.reference_id,
            created_at=entry.created_at,
        )
        for entry in entries
    ]


class ChipPackageResponse(BaseModel):
    key: str
    name: str
    chip_amount: int


class ChipPackageListResponse(BaseModel):
    package_version: str
    packages: list[ChipPackageResponse]


@router.get("/chip-packages", response_model=ChipPackageListResponse)
async def list_chip_packages() -> ChipPackageListResponse:
    """Chip yukleme icin secilebilecek sabit paketleri dondurur (gercek odeme yoktur)."""

    return ChipPackageListResponse(
        package_version=CURRENT_CHIP_PACKAGE_VERSION,
        packages=[
            ChipPackageResponse(key=pkg.key, name=pkg.name, chip_amount=pkg.chip_amount)
            for pkg in get_chip_packages()
        ],
    )


class TopUpRequestCreate(BaseModel):
    package_key: str


class TopUpRequestResponse(BaseModel):
    id: uuid.UUID
    package_key: str
    chip_amount: int
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    review_note: str | None


@router.post("/topup-requests", response_model=TopUpRequestResponse, status_code=201)
async def create_topup_request(
    body: TopUpRequestCreate,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> TopUpRequestResponse:
    """Bir Chip yukleme TALEBI kaydeder.

    Gercek bir odeme saglayicisi entegre edilmedigi icin bu uc nokta hicbir
    kart/odeme verisi almaz ve Chip bakiyesini DEGISTIRMEZ - yalnizca
    `pending` durumda bir talep satiri olusturur (bkz.
    `app.services.chip_packages` ve `app.models.billing.ChipTopUpRequest`).
    """

    try:
        package = get_chip_package(body.package_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request = ChipTopUpRequest(
        organization_id=principal.organization_id,
        requested_by_user_id=principal.user_id,
        package_key=package.key,
        chip_amount=package.chip_amount,
    )
    session.add(request)
    await session.flush()
    session.add(
        AuditLog(
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            action="chip_topup_requested",
            entity_type="chip_topup_request",
            entity_id=request.id,
            entry_metadata={
                "status": request.status.value,
                "chip_amount": request.chip_amount,
                "package_key": request.package_key,
            },
        )
    )
    await session.commit()
    audit_logger.info(
        "Chip yukleme talebi olusturuldu",
        extra={
            "action": "chip_topup_requested",
            "entity_id": str(request.id),
            "organization_id": str(principal.organization_id),
            "actor_user_id": str(principal.user_id),
            "status": request.status.value,
            "chip_amount": request.chip_amount,
            "package_key": request.package_key,
        },
    )

    return TopUpRequestResponse(
        id=request.id,
        package_key=request.package_key,
        chip_amount=request.chip_amount,
        status=request.status.value,
        created_at=request.created_at,
        reviewed_at=request.reviewed_at,
        review_note=request.review_note,
    )


@router.get("/topup-requests", response_model=list[TopUpRequestResponse])
async def list_topup_requests(
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[TopUpRequestResponse]:
    """Organizasyonun gecmis Chip yukleme taleplerini en yeniden eskiye listeler."""

    result = await session.execute(
        select(ChipTopUpRequest)
        .where(ChipTopUpRequest.organization_id == organization_id)
        .order_by(ChipTopUpRequest.created_at.desc())
    )
    requests = result.scalars().all()

    return [
        TopUpRequestResponse(
            id=req.id,
            package_key=req.package_key,
            chip_amount=req.chip_amount,
            status=req.status.value,
            created_at=req.created_at,
            reviewed_at=req.reviewed_at,
            review_note=req.review_note,
        )
        for req in requests
    ]
