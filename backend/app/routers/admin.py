import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.db import get_session
from app.dependencies import Principal, require_platform_admin, verify_csrf
from app.logging_config import get_logger
from app.models.ai_pipeline import AIPipelineRun, AIPipelineStatus
from app.models.audit import AuditLog
from app.models.billing import ChipTopUpRequest, ChipTopUpRequestStatus
from app.models.tenancy import Organization, User
from app.services import chip_ledger

audit_logger = get_logger("audit")

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminSummaryResponse(BaseModel):
    organization_count: int
    user_count: int
    pending_topup_count: int
    failed_ai_pipeline_count: int


class AdminAISettingsResponse(BaseModel):
    enabled: bool
    provider: str
    provider_ready: bool
    model: str | None


class AdminChipSettingsResponse(BaseModel):
    review_mode: Literal["manual_admin_review"]
    approval_credits_once: bool
    rejection_note_required: bool


class AdminSecuritySettingsResponse(BaseModel):
    secure_cookies: bool
    access_token_ttl_minutes: int
    refresh_token_ttl_days: int
    login_rate_limit_max_attempts: int
    login_rate_limit_window_minutes: int


class AdminOperationsSettingsResponse(BaseModel):
    log_level: str
    analyzer_timeout_seconds: int
    report_screenshot_retention_days: int


class AdminPlatformSettingsResponse(BaseModel):
    environment: str
    ai_report: AdminAISettingsResponse
    chip_topups: AdminChipSettingsResponse
    security: AdminSecuritySettingsResponse
    operations: AdminOperationsSettingsResponse


class AdminTopUpRequestResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    organization_name: str
    requested_by_email: str
    requested_by_display_name: str | None
    package_key: str
    chip_amount: int
    status: str
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by_email: str | None
    review_note: str | None


class AdminTopUpReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


@router.get("/summary", response_model=AdminSummaryResponse)
async def get_admin_summary(
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminSummaryResponse:
    organization_count = await session.scalar(select(func.count()).select_from(Organization))
    user_count = await session.scalar(select(func.count()).select_from(User))
    pending_topup_count = await session.scalar(
        select(func.count())
        .select_from(ChipTopUpRequest)
        .where(ChipTopUpRequest.status == ChipTopUpRequestStatus.PENDING)
    )
    failed_ai_pipeline_count = await session.scalar(
        select(func.count()).select_from(AIPipelineRun).where(AIPipelineRun.status == AIPipelineStatus.FAILED)
    )
    return AdminSummaryResponse(
        organization_count=int(organization_count or 0),
        user_count=int(user_count or 0),
        pending_topup_count=int(pending_topup_count or 0),
        failed_ai_pipeline_count=int(failed_ai_pipeline_count or 0),
    )


@router.get("/settings", response_model=AdminPlatformSettingsResponse)
async def get_admin_settings(
    _principal: Principal = Depends(require_platform_admin),
) -> AdminPlatformSettingsResponse:
    model: str | None = None
    if settings.ai_report_provider == "openai":
        model = settings.openai_model
    elif settings.ai_report_provider == "ollama":
        model = settings.ollama_model

    return AdminPlatformSettingsResponse(
        environment=settings.environment,
        ai_report=AdminAISettingsResponse(
            enabled=settings.ai_report_enabled,
            provider=settings.ai_report_provider,
            provider_ready=settings.ai_report_provider_ready,
            model=model,
        ),
        chip_topups=AdminChipSettingsResponse(
            review_mode="manual_admin_review",
            approval_credits_once=True,
            rejection_note_required=True,
        ),
        security=AdminSecuritySettingsResponse(
            secure_cookies=settings.resolved_cookie_secure,
            access_token_ttl_minutes=settings.access_token_ttl_seconds // 60,
            refresh_token_ttl_days=settings.refresh_token_ttl_seconds // (24 * 60 * 60),
            login_rate_limit_max_attempts=settings.login_rate_limit_max_attempts,
            login_rate_limit_window_minutes=settings.login_rate_limit_window_seconds // 60,
        ),
        operations=AdminOperationsSettingsResponse(
            log_level=settings.log_level,
            analyzer_timeout_seconds=settings.analyzer_request_timeout_seconds,
            report_screenshot_retention_days=settings.report_linked_screenshot_retention_seconds
            // (24 * 60 * 60),
        ),
    )
def _topup_query():
    reviewer = aliased(User)
    return (
        select(ChipTopUpRequest, Organization, User, reviewer)
        .join(Organization, Organization.id == ChipTopUpRequest.organization_id)
        .join(User, User.id == ChipTopUpRequest.requested_by_user_id)
        .outerjoin(reviewer, reviewer.id == ChipTopUpRequest.reviewed_by_user_id)
        .order_by(ChipTopUpRequest.created_at.desc())
    )


def _serialize_topup(row) -> AdminTopUpRequestResponse:
    request, organization, requester, reviewer = row
    return AdminTopUpRequestResponse(
        id=request.id,
        organization_id=request.organization_id,
        organization_name=organization.name,
        requested_by_email=requester.email,
        requested_by_display_name=requester.display_name,
        package_key=request.package_key,
        chip_amount=request.chip_amount,
        status=request.status.value,
        created_at=request.created_at,
        reviewed_at=request.reviewed_at,
        reviewed_by_email=reviewer.email if reviewer else None,
        review_note=request.review_note,
    )


@router.get("/topup-requests", response_model=list[AdminTopUpRequestResponse])
async def list_admin_topup_requests(
    status: ChipTopUpRequestStatus | None = Query(default=None),
    _principal: Principal = Depends(require_platform_admin),
    session: AsyncSession = Depends(get_session),
) -> list[AdminTopUpRequestResponse]:
    query = _topup_query()
    if status is not None:
        query = query.where(ChipTopUpRequest.status == status)
    rows = (await session.execute(query)).all()
    return [_serialize_topup(row) for row in rows]


@router.post("/topup-requests/{request_id}/review", response_model=AdminTopUpRequestResponse)
async def review_topup_request(
    request_id: uuid.UUID,
    body: AdminTopUpReviewRequest,
    principal: Principal = Depends(require_platform_admin),
    _csrf: None = Depends(verify_csrf),
    session: AsyncSession = Depends(get_session),
) -> AdminTopUpRequestResponse:
    note = body.note.strip() if body.note else None
    if body.action == "reject" and not note:
        raise HTTPException(status_code=400, detail="Red nedeni zorunludur")

    request = (
        await session.execute(
            select(ChipTopUpRequest).where(ChipTopUpRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="Chip yükleme talebi bulunamadı")

    target_status = (
        ChipTopUpRequestStatus.APPROVED if body.action == "approve" else ChipTopUpRequestStatus.REJECTED
    )
    if request.status != ChipTopUpRequestStatus.PENDING:
        if request.status != target_status:
            raise HTTPException(status_code=409, detail="Talep daha önce farklı bir kararla işlendi")
    else:
        if target_status == ChipTopUpRequestStatus.APPROVED:
            await chip_ledger.credit(
                session,
                request.organization_id,
                request.chip_amount,
                "Yönetici onaylı Chip yükleme talebi",
                idempotency_key=f"topup-request:{request.id}:approved",
                reference_type="chip_topup_request",
                reference_id=request.id,
            )
        action = (
            "chip_topup_approved"
            if target_status == ChipTopUpRequestStatus.APPROVED
            else "chip_topup_rejected"
        )
        request.status = target_status
        request.reviewed_by_user_id = principal.user_id
        request.reviewed_at = datetime.now(UTC)
        request.review_note = note
        session.add(
            AuditLog(
                organization_id=request.organization_id,
                actor_user_id=principal.user_id,
                action=action,
                entity_type="chip_topup_request",
                entity_id=request.id,
                entry_metadata={
                    "status": target_status.value,
                    "chip_amount": request.chip_amount,
                    "package_key": request.package_key,
                    "has_review_note": note is not None,
                },
            )
        )
        await session.commit()
        audit_logger.info(
            "Chip yukleme talebi sonuclandirildi",
            extra={
                "action": action,
                "entity_id": str(request.id),
                "organization_id": str(request.organization_id),
                "actor_user_id": str(principal.user_id),
                "status": target_status.value,
                "chip_amount": request.chip_amount,
            },
        )

    row = (await session.execute(_topup_query().where(ChipTopUpRequest.id == request.id))).one()
    return _serialize_topup(row)
