import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.models.analytics import AnalyticsEventType
from app.models.test_wizard import TestWizardDraft, TestWizardDraftStatus
from app.services import analysis_url_scope, url_safety
from app.services import analytics as analytics_service
from app.services import settings as settings_service
from app.services import target_task as target_task_service
from app.services import test_wizard as wizard_service
from app.services.exceptions import InsufficientChipBalanceError, UnauthorizedPageAnalysisError

router = APIRouter(prefix="/api/tests/drafts", tags=["test-wizard"])

# Taslak olusturma/guncelleme/baslatma: viewer haric tum roller (bkz.
# docs/architecture.md#roller-ve-yetkiler, "Proje/test/simulasyon olustur").
WRITE_ROLES = ("analyst", "admin", "owner")


def _invalid_target_task_http_exc(
    exc: target_task_service.InvalidTargetTaskError,
) -> HTTPException:
    """Anlamsal olarak gecersiz `target_task` icin urun API standardina uygun,
    ALAN BAZLI 422 yaniti uretir. FastAPI govdesi `{"detail": {...}}` sarmalar;
    frontend `detail.code`/`detail.field` ile hatayi ilgili alanin altinda
    gosterir (genel "bir hata olustu" mesajina indirgemez, bkz.
    frontend/src/api/client.ts)."""

    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "detail": exc.message, "field": exc.field},
    )


def _unsupported_analysis_url_http_exc(
    exc: analysis_url_scope.UnsupportedAnalysisUrlError,
) -> HTTPException:
    return HTTPException(
        status_code=422,
        detail={"code": exc.code, "detail": exc.message, "field": exc.field},
    )


class DraftResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    status: str
    current_step: int
    payload: dict
    missing_fields: list[str]
    created_at: datetime
    updated_at: datetime
    warnings: list[str] = []


def _to_response(draft: TestWizardDraft, *, warnings: list[str] | None = None) -> DraftResponse:
    return DraftResponse(
        id=draft.id,
        organization_id=draft.organization_id,
        status=draft.status.value,
        current_step=draft.current_step,
        payload=draft.payload,
        missing_fields=wizard_service.missing_fields_for_launch(draft.payload),
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        warnings=warnings or [],
    )


async def _get_owned_draft(
    session: AsyncSession, organization_id: uuid.UUID, draft_id: uuid.UUID, *, for_update: bool = False
) -> TestWizardDraft:
    query = select(TestWizardDraft).where(TestWizardDraft.id == draft_id)
    if for_update:
        query = query.with_for_update()
    result = await session.execute(query)
    draft = result.scalar_one_or_none()
    if draft is None or draft.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Taslak bulunamadi")
    return draft


@router.post("", response_model=DraftResponse, status_code=201)
async def create_draft(
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Bos bir sihirbaz taslagi olusturur. Adim verileri sonraki PATCH cagrilariyla doldurulur."""

    initial_payload = await settings_service.build_initial_wizard_payload(session, principal.organization_id)
    draft = TestWizardDraft(
        organization_id=principal.organization_id,
        created_by_user_id=principal.user_id,
        status=TestWizardDraftStatus.DRAFT,
        current_step=1,
        payload=initial_payload,
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)

    return _to_response(draft)


@router.get("", response_model=list[DraftResponse])
async def list_drafts(
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[DraftResponse]:
    """Organizasyonun henüz başlatılmamış taslaklarını son güncellenene göre döndürür."""

    result = await session.execute(
        select(TestWizardDraft)
        .where(
            TestWizardDraft.organization_id == organization_id,
            TestWizardDraft.status == TestWizardDraftStatus.DRAFT,
        )
        .order_by(TestWizardDraft.updated_at.desc())
    )
    drafts = result.scalars().all()
    await session.commit()
    return [_to_response(draft) for draft in drafts]


@router.get("/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Taslagi dondurur; sayfa yenilenince veya geri donulunce kaldigi yerden devam edilebilmesi icindir."""

    draft = await _get_owned_draft(session, organization_id, draft_id)
    await session.commit()
    return _to_response(draft)


@router.delete("/{draft_id}", status_code=204)
async def delete_draft(
    draft_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Yalnizca henuz baslatilmamis sihirbaz taslagini kalici olarak siler.

    Baslatilmis testler Chip/entitlement ve sonuc denetim izinin parcasi
    oldugu icin bu uc noktadan silinemez.
    """

    draft = await _get_owned_draft(session, principal.organization_id, draft_id, for_update=True)
    if draft.status != TestWizardDraftStatus.DRAFT:
        raise HTTPException(status_code=409, detail="Baslatilmis bir test silinemez")

    await session.delete(draft)
    await session.commit()


class PatchDraftRequest(BaseModel):
    current_step: int | None = None
    payload: dict = {}


@router.patch("/{draft_id}", response_model=DraftResponse)
async def patch_draft(
    draft_id: uuid.UUID,
    body: PatchDraftRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> DraftResponse:
    """Taslagin adim verilerini kismi olarak gunceller (yalnizca gonderilen alanlar dogrulanip birlestirilir)."""

    draft = await _get_owned_draft(session, principal.organization_id, draft_id, for_update=True)
    if draft.status == TestWizardDraftStatus.LAUNCHED:
        raise HTTPException(status_code=409, detail="Baslatilmis bir taslak degistirilemez")

    try:
        wizard_service.validate_patch_fields(body.payload)
        merged_payload = wizard_service.merge_payload(draft.payload, body.payload)
        merged_payload = wizard_service.invalidate_stale_cta_annotations(
            draft.payload, merged_payload, body.payload
        )
        wizard_service.validate_source_type_for_test_type(merged_payload)
    except target_task_service.InvalidTargetTaskError as exc:
        raise _invalid_target_task_http_exc(exc) from exc
    except analysis_url_scope.UnsupportedAnalysisUrlError as exc:
        raise _unsupported_analysis_url_http_exc(exc) from exc
    except wizard_service.DraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Sekil (UUID bicimi) dogrulamasi `validate_patch_fields` icinde zaten
    # yapildi; burada AYRICA gercek DB sahiplik/kullanilabilirlik dogrulamasi
    # yapilir (bkz. `validate_screenshot_asset_ownership` docstring'i) - bu
    # nedenle `validate_patch_fields` saf/senkron kalir, yalnizca bu adim
    # async'tir. A/B karsilastirmasinda hem "Tasarim A" (current) hem
    # "Tasarim B" (new) tarafi bagimsiz olarak ekran goruntusu kaynagi
    # secebildigi icin ayni kontrol her iki alan icin de calistirilir.
    asset_fields = (
        ("current_design_asset_id", wizard_service.effective_source_type(merged_payload)),
        ("new_design_asset_id", wizard_service.effective_new_source_type(merged_payload)),
    )
    for field_name, source_type in asset_fields:
        asset_id_raw = merged_payload.get(field_name)
        if source_type == wizard_service.SOURCE_TYPE_SCREENSHOT and asset_id_raw:
            try:
                await wizard_service.validate_screenshot_asset_ownership(
                    session, principal.organization_id, uuid.UUID(str(asset_id_raw))
                )
            except wizard_service.DraftValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    # "Tasarim B" (new) tarafi AI ile uretilmisse ("ai_generated"), sahiplik
    # kontrolu farklidir: yalnizca asset'in degil, onu ureten isin (job)
    # GERCEKTEN basarili oldugu VE `new_design_asset_id`nin isin gercek
    # sonucuyla eslestigi de dogrulanir (bkz. validate_ai_generation_ownership
    # docstring'i - kullanicinin kabul etmedigi/hala calisan bir sonucu
    # "sahte kabul" ile baglamasini engeller). Bu, "Tasarim A" (current)
    # tarafi icin GECERLI DEGILDIR - o taraf hicbir zaman AI ile uretilemez.
    if wizard_service.effective_new_source_type(merged_payload) == wizard_service.SOURCE_TYPE_AI_GENERATED:
        new_asset_id_raw = merged_payload.get("new_design_asset_id")
        new_generation_id_raw = merged_payload.get("new_ai_generation_id")
        if new_asset_id_raw and new_generation_id_raw:
            try:
                await wizard_service.validate_ai_generation_ownership(
                    session,
                    principal.organization_id,
                    uuid.UUID(str(new_generation_id_raw)),
                    uuid.UUID(str(new_asset_id_raw)),
                )
            except wizard_service.DraftValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Kullanici CTA onayi (Paket 4C+4D): yalnizca bu PATCH cagrisinda ACIKCA
    # gonderilen slot(lar) islenir - `design_asset_id`, o slot'un GUNCEL
    # (birlestirilmis) degeriyle eslesmeli, sahiplik/kullanilabilirlik burada
    # BAGIMSIZ olarak yeniden dogrulanir, ve `verified_content_sha256` HER
    # ZAMAN sunucuda yeniden hesaplanir (bkz. resolve_cta_annotation_patch
    # docstring'i - client degeri asla kabul edilmez). Buyuk-alan uyarisi
    # (zorla engelleme degil) response'a eklenir.
    patch_warnings: list[str] = []
    for annotation_field, asset_field in wizard_service.CTA_ANNOTATION_FIELD_TO_ASSET_FIELD.items():
        if annotation_field not in body.payload or body.payload[annotation_field] is None:
            continue
        raw_value = body.payload[annotation_field]
        expected_asset_id = merged_payload.get(asset_field)
        if expected_asset_id is None or str(raw_value.get("design_asset_id")) != str(expected_asset_id):
            raise HTTPException(status_code=400, detail=wizard_service.CTA_ANNOTATION_ASSET_MISMATCH_MESSAGE)
        try:
            resolved = await wizard_service.resolve_cta_annotation_patch(
                session, principal.organization_id, raw_value
            )
        except wizard_service.DraftValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        merged_payload[annotation_field] = resolved
        warning = wizard_service.cta_annotation_area_warning(resolved)
        if warning:
            patch_warnings.append(warning)

    draft.payload = merged_payload
    if body.current_step is not None:
        if not (1 <= body.current_step <= 5):
            raise HTTPException(status_code=400, detail="current_step 1 ile 5 arasinda olmalidir")
        draft.current_step = body.current_step

    await session.commit()
    await session.refresh(draft)

    return _to_response(draft, warnings=patch_warnings)


class LaunchWarning(BaseModel):
    code: str
    slot: str
    message: str


class LaunchResponse(BaseModel):
    draft_id: uuid.UUID
    status: str
    test_definition_id: uuid.UUID
    simulation_run_ids: list[uuid.UUID]
    used_free_entitlement: bool
    reserved_chips: int
    engine_status_message: str
    warnings: list[LaunchWarning] = []


@router.post("/{draft_id}/launch", response_model=LaunchResponse)
async def launch_draft(
    draft_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> LaunchResponse:
    """Taslagi baslatir. Idempotenttir: cift tiklama/tekrar deneme ayni sonucu dondurur.

    `SELECT ... FOR UPDATE` ile taslak satiri kilitlenerek yarisan istekler
    serilestirilir; boylece iki es zamanli baslatma denemesi hicbir zaman
    entitlement/Chip'i iki kez rezerve edemez veya iki kez calistirma
    olusturamaz (bkz. `app.services.test_wizard.launch_draft`).
    """

    draft = await _get_owned_draft(session, principal.organization_id, draft_id, for_update=True)

    try:
        result = await wizard_service.launch_draft(
            session,
            organization_id=principal.organization_id,
            requested_by_user_id=principal.user_id,
            draft=draft,
        )
    except target_task_service.InvalidTargetTaskError as exc:
        # Gecersiz hedef gorevle (ör. `.` gibi eski bir taslak) baslatma denemesi:
        # `launch_draft` bu kontrolu TUM yan etkilerden once yapar - hicbir Chip
        # rezerve/harcanmaz, hicbir SimulationRun olusmaz.
        raise _invalid_target_task_http_exc(exc) from exc
    except analysis_url_scope.UnsupportedAnalysisUrlError as exc:
        raise _unsupported_analysis_url_http_exc(exc) from exc
    except wizard_service.DraftValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InsufficientChipBalanceError as exc:
        raise HTTPException(
            status_code=402,
            detail=(
                "Yetersiz Chip bakiyesi: bu testi baslatmak icin gereken Chip miktari "
                f"mevcut bakiyenizi asiyor ({exc})."
            ),
        ) from exc
    except url_safety.UnsafeUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnauthorizedPageAnalysisError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Organizasyonun ILK testini baslatma is olayi (dedup_key ile yalnizca bir
    # kez; launch idempotent oldugu icin tekrar denemeler de tekilligi korur).
    if settings.analytics_enabled:
        await analytics_service.insert_event(
            session,
            event_type=AnalyticsEventType.FIRST_TEST_STARTED,
            user_id=principal.user_id,
            organization_id=principal.organization_id,
            dedup_key=f"first_test_started:{principal.organization_id}",
        )

    await session.commit()

    return LaunchResponse(
        draft_id=draft.id,
        status=draft.status.value,
        test_definition_id=result.test_definition_id,
        simulation_run_ids=list(result.simulation_run_ids),
        used_free_entitlement=result.used_free_entitlement,
        reserved_chips=result.reserved_chips,
        engine_status_message=result.engine_status_message,
        warnings=[LaunchWarning.model_validate(w) for w in result.warnings],
    )
