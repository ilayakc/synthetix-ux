import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import Field

from app.db import get_session
from app.dependencies import Principal, get_organization_id, require_roles
from app.engine.baseline import compare_baseline_results
from app.models.simulations import CalibrationObservation, SimulationRun, SimulationStatus
from app.models.tests import TestDefinition, TestVariant
from app.services import calibration as calibration_service
from app.services import simulation_progress
from app.services import simulation_worker as worker_service
from app.services.exceptions import (
    CalibrationConsentRequiredError,
    CalibrationObservationRequiresMetricError,
    CalibrationRunNotSucceededError,
    ChipReservationNotFoundError,
    EntitlementNotFoundError,
    EntitlementUnavailableError,
    InsufficientChipBalanceError,
    InvalidChipReservationStateError,
    InvalidEntitlementStateError,
    InvalidSimulationStateError,
    SimulationRunNotFoundError,
)

router = APIRouter(prefix="/api/simulations", tags=["simulations"])

# Iptal/yeniden deneme: viewer haric tum roller (bkz.
# docs/architecture.md#roller-ve-yetkiler).
WRITE_ROLES = ("analyst", "admin", "owner")

# Bu deger, ilgili SimulationRun'un `result` alaninda zaten yer alir
# (bkz. app.engine.baseline.RESULT_DISCLAIMER); burada ayrica UI'nin her
# durumda gosterebilecegi sabit bir kisa etiket olarak da tasinir.
NOT_REAL_USER_DATA_LABEL = "Gerçek kullanıcı verisi değildir"


class SimulationRunResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    test_variant_id: uuid.UUID
    status: str
    progress_percent: int
    progress_message: str | None
    calibration_status: str
    deterministic_seed: int
    model_version: str
    rules_version: str | None
    fixture_version: str | None
    error: str | None
    result: dict | None
    retryable: bool
    failure_code: str | None
    not_real_user_data_label: str
    methodology_reference: str
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


async def _to_response(run: SimulationRun) -> SimulationRunResponse:
    live_progress = await simulation_progress.read_progress(run.id)
    percent = live_progress["percent"] if live_progress else run.progress_percent
    message = live_progress["message"] if live_progress else run.progress_message
    retryable, failure_code = worker_service.classify_run_failure(run)

    return SimulationRunResponse(
        id=run.id,
        organization_id=run.organization_id,
        test_variant_id=run.test_variant_id,
        status=run.status.value,
        progress_percent=percent,
        progress_message=message,
        calibration_status=run.calibration_status.value,
        deterministic_seed=run.deterministic_seed,
        model_version=run.model_version,
        rules_version=run.rules_version,
        fixture_version=run.fixture_version,
        error=run.error,
        result=run.result,
        retryable=retryable,
        failure_code=failure_code,
        not_real_user_data_label=NOT_REAL_USER_DATA_LABEL,
        methodology_reference="docs/methodology.md",
        attempt_count=run.attempt_count,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


async def _get_owned_run(
    session: AsyncSession, organization_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun:
    run = await session.get(SimulationRun, run_id)
    if run is None or run.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Calistirma bulunamadi")
    return run


@router.get("/runs", response_model=list[SimulationRunResponse])
async def list_runs(
    test_definition_id: uuid.UUID | None = None,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[SimulationRunResponse]:
    """Organizasyonun simulasyon calistirmalarini listeler (istege bagli test tanimi filtresi)."""

    query = select(SimulationRun).where(SimulationRun.organization_id == organization_id)
    if test_definition_id is not None:
        query = query.join(TestVariant, TestVariant.id == SimulationRun.test_variant_id).where(
            TestVariant.test_definition_id == test_definition_id
        )
    query = query.order_by(SimulationRun.created_at.desc())

    result = await session.execute(query)
    runs = result.scalars().all()
    await session.commit()

    return [await _to_response(run) for run in runs]


@router.get("/runs/{run_id}", response_model=SimulationRunResponse)
async def get_run(
    run_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> SimulationRunResponse:
    run = await _get_owned_run(session, organization_id, run_id)
    await session.commit()
    return await _to_response(run)


@router.post("/runs/{run_id}/cancel", response_model=SimulationRunResponse)
async def cancel_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> SimulationRunResponse:
    """Calistirmayi iptal eder. Zaten terminal durumdaysa (idempotent) hicbir sey degismez."""

    try:
        run = await worker_service.cancel_run(session, principal.organization_id, run_id)
    except SimulationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await session.commit()
    return await _to_response(run)


@router.post("/runs/{run_id}/retry", response_model=SimulationRunResponse)
async def retry_run(
    run_id: uuid.UUID,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> SimulationRunResponse:
    """Basarisiz bir calistirmayi rezervasyonu yeniden yaparak kuyruga alir."""

    try:
        run = await worker_service.retry_run(session, principal.organization_id, run_id)
    except SimulationRunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidSimulationStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EntitlementUnavailableError as exc:
        raise HTTPException(status_code=409, detail=f"Ucretsiz hak artik uygun degil: {exc}") from exc
    except InsufficientChipBalanceError as exc:
        raise HTTPException(status_code=402, detail=f"Yetersiz Chip bakiyesi: {exc}") from exc
    except (
        EntitlementNotFoundError,
        ChipReservationNotFoundError,
        InvalidEntitlementStateError,
        InvalidChipReservationStateError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    return await _to_response(run)


class CalibrationObservationCreateRequest(BaseModel):
    # Acik riza onayi (bkz. app.services.calibration.record_observation) -
    # `authorization_confirmed` (app.routers.page_analysis) ile ayni desen:
    # varsayilan False, istemci acikca True gondermeden kayit reddedilir.
    consent_confirmed: bool = False
    real_task_completion_rate: float | None = Field(default=None, ge=0, le=1)
    real_median_task_duration_seconds: float | None = Field(default=None, ge=0)
    real_misclick_rate: float | None = Field(default=None, ge=0, le=1)
    real_abandonment_rate: float | None = Field(default=None, ge=0, le=1)
    sample_size: int | None = Field(default=None, ge=1)
    source_note: str | None = Field(default=None, max_length=2000)


class CalibrationObservationResponse(BaseModel):
    id: uuid.UUID
    simulation_run_id: uuid.UUID
    recorded_by_user_id: uuid.UUID | None
    real_task_completion_rate: float | None
    real_median_task_duration_seconds: float | None
    real_misclick_rate: float | None
    real_abandonment_rate: float | None
    sample_size: int | None
    source_note: str | None
    created_at: datetime


def _to_observation_response(observation: CalibrationObservation) -> CalibrationObservationResponse:
    return CalibrationObservationResponse(
        id=observation.id,
        simulation_run_id=observation.simulation_run_id,
        recorded_by_user_id=observation.recorded_by_user_id,
        real_task_completion_rate=observation.real_task_completion_rate,
        real_median_task_duration_seconds=observation.real_median_task_duration_seconds,
        real_misclick_rate=observation.real_misclick_rate,
        real_abandonment_rate=observation.real_abandonment_rate,
        sample_size=observation.sample_size,
        source_note=observation.source_note,
        created_at=observation.created_at,
    )


@router.post(
    "/runs/{run_id}/calibration-observations",
    response_model=CalibrationObservationResponse,
    status_code=201,
)
async def create_calibration_observation(
    run_id: uuid.UUID,
    body: CalibrationObservationCreateRequest,
    principal: Principal = Depends(require_roles(*WRITE_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> CalibrationObservationResponse:
    """Bu calistirmaya gonullu, acik rizali bir GERCEK kullanilabilirlik testi
    sonucu ekler (bkz. docs/methodology.md "Kalibrasyon plani").

    Bu, motorun agirliklarini/`calibration_status`unu OTOMATIK OLARAK
    degistirmez - yalnizca ileride bagimsiz bir surecte gozden gecirilecek
    ham gercek-veri gozlemini saklar.
    """

    run = await _get_owned_run(session, principal.organization_id, run_id)

    try:
        observation = await calibration_service.record_observation(
            session,
            run=run,
            recorded_by_user_id=principal.user_id,
            consent_confirmed=body.consent_confirmed,
            real_task_completion_rate=body.real_task_completion_rate,
            real_median_task_duration_seconds=body.real_median_task_duration_seconds,
            real_misclick_rate=body.real_misclick_rate,
            real_abandonment_rate=body.real_abandonment_rate,
            sample_size=body.sample_size,
            source_note=body.source_note,
        )
    except CalibrationConsentRequiredError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CalibrationObservationRequiresMetricError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CalibrationRunNotSucceededError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    return _to_observation_response(observation)


@router.get(
    "/runs/{run_id}/calibration-observations",
    response_model=list[CalibrationObservationResponse],
)
async def list_calibration_observations(
    run_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[CalibrationObservationResponse]:
    await _get_owned_run(session, organization_id, run_id)
    observations = await calibration_service.list_observations_for_run(
        session, organization_id=organization_id, run_id=run_id
    )
    await session.commit()
    return [_to_observation_response(observation) for observation in observations]


class ComparisonResponse(BaseModel):
    test_definition_id: uuid.UUID
    variant_a_run: SimulationRunResponse
    variant_b_run: SimulationRunResponse
    comparison: dict
    not_real_user_data_label: str
    methodology_reference: str


@router.get("/comparisons/{test_definition_id}", response_model=ComparisonResponse)
async def get_comparison(
    test_definition_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> ComparisonResponse:
    """Bir A/B test tanimindaki iki varyantin sentetik sonuclarini karsilastirir.

    Istatistiksel anlamlilik iddia edilmez; yalnizca mutlak sonuclar, fark,
    belirsizlik ve orneklenen sentetik persona sayisi dondurulur (bkz.
    docs/scientific-integrity.md).
    """

    definition = await session.get(TestDefinition, test_definition_id)
    if definition is None or definition.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Test tanimi bulunamadi")

    variants_result = await session.execute(
        select(TestVariant)
        .where(TestVariant.test_definition_id == test_definition_id)
        .order_by(TestVariant.created_at)
    )
    variants = list(variants_result.scalars().all())
    if len(variants) != 2:
        raise HTTPException(
            status_code=400, detail="Karsilastirma yalnizca iki varyantli (A/B) testler icindir"
        )

    variants.sort(key=lambda v: 0 if v.config.get("role") == "existing" else 1)
    variant_a, variant_b = variants[0], variants[1]

    async def _latest_run(variant: TestVariant) -> SimulationRun | None:
        result = await session.execute(
            select(SimulationRun)
            .where(SimulationRun.test_variant_id == variant.id)
            .order_by(SimulationRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    run_a = await _latest_run(variant_a)
    run_b = await _latest_run(variant_b)
    await session.commit()

    if run_a is None or run_b is None:
        raise HTTPException(status_code=404, detail="Varyantlar icin calistirma bulunamadi")
    if run_a.status != SimulationStatus.SUCCEEDED or run_b.status != SimulationStatus.SUCCEEDED:
        raise HTTPException(
            status_code=409,
            detail="Karsilastirma icin her iki varyantin da basariyla tamamlanmis olmasi gerekir",
        )

    assert run_a.result is not None
    assert run_b.result is not None
    comparison = compare_baseline_results(
        run_a.result,
        run_b.result,
        persona_count_a=run_a.input_snapshot.get("persona_count", 0),
        persona_count_b=run_b.input_snapshot.get("persona_count", 0),
    )

    return ComparisonResponse(
        test_definition_id=test_definition_id,
        variant_a_run=await _to_response(run_a),
        variant_b_run=await _to_response(run_b),
        comparison=comparison,
        not_real_user_data_label=NOT_REAL_USER_DATA_LABEL,
        methodology_reference="docs/methodology.md",
    )
