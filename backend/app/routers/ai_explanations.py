"""AI destekli açıklama uç noktası (bkz. app.services.ai_explanation).

Bu router hiçbir simülasyonu yeniden çalıştırmaz; yalnızca zaten var olan,
salt-okunur bir raporun (bkz. `app.routers.reports`) yapılandırılmış
metriklerinden isteğe bağlı, sağlayıcıdan bağımsız bir doğal dil açıklaması
üretir. Model, girdi olarak asla ham HTML, cookie, token veya kullanıcı
e-postası görmez (bkz. `app.services.ai_explanation.build_input_from_report`).

Denetim (audit): her üretim, gizli anahtar veya ham hassas içerik
taşımayan bir `AuditLog` kaydı bırakır (prompt sürümü, sağlayıcı/model
adı, oluşturma zamanı, kaynak run/report kimliği).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import Principal, get_current_principal
from app.models.audit import AuditLog
from app.routers.reports import _build_detail_response, _get_owned_report_context
from app.services.ai_explanation import (
    AIExplanationOutput,
    build_input_from_report,
    generate_explanation,
)

router = APIRouter(prefix="/api/reports", tags=["ai-explanations"])


@router.post("/{report_id}/ai-explanation", response_model=AIExplanationOutput)
async def generate_report_ai_explanation(
    report_id: uuid.UUID,
    principal: Principal = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> AIExplanationOutput:
    organization_id = principal.organization_id
    ctx = await _get_owned_report_context(session, organization_id, report_id)
    detail = await _build_detail_response(session, organization_id, ctx)

    ai_input = build_input_from_report(
        report_id=str(detail.id),
        simulation_run_id=str(ctx.run.id),
        model_version=ctx.run.model_version,
        rules_version=detail.info_box.rules_version,
        fixture_version=detail.info_box.fixture_version,
        methodology_reference=detail.methodology_reference,
        metrics=detail.metrics,
        critical_findings=[f.model_dump() for f in detail.critical_findings],
        persona_segments=[s.model_dump() for s in detail.persona_segments],
    )

    output, audit_record = await generate_explanation(ai_input)

    session.add(
        AuditLog(
            organization_id=organization_id,
            actor_user_id=principal.user_id,
            action="ai_explanation_generated",
            entity_type="report",
            entity_id=report_id,
            entry_metadata=audit_record,
        )
    )
    await session.commit()

    return output
