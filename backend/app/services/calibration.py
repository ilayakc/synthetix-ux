"""Gercek kullanilabilirlik testi sonuclarini SimulationRun'lara baglayan
kalibrasyon veri toplama servisi (bkz. docs/methodology.md "Kalibrasyon
plani", adim 1).

Bu modul motorun agirliklarini (`app.engine.rules_config`) OTOMATIK OLARAK
degistirmez / kalibre ETMEZ - yalnizca gonullu, acik rizali gercek test
sonuclarini (gorev tamamlama, sure, hata orani) ilgili `SimulationRun`'a
bagli olarak SAKLAR. `SimulationRun.calibration_status` bu kayittan
etkilenmez ve `uncalibrated` kalmaya devam eder; "calibrating"/"calibrated"
gecisi (adim 3-4) kasitli olarak bu modulun kapsami disindadir - bkz.
docs/methodology.md.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.simulations import CalibrationObservation, SimulationRun, SimulationStatus
from app.services.exceptions import (
    CalibrationConsentRequiredError,
    CalibrationObservationRequiresMetricError,
    CalibrationRunNotSucceededError,
)


async def record_observation(
    session: AsyncSession,
    *,
    run: SimulationRun,
    recorded_by_user_id: uuid.UUID,
    consent_confirmed: bool,
    real_task_completion_rate: float | None,
    real_median_task_duration_seconds: float | None,
    real_misclick_rate: float | None,
    real_abandonment_rate: float | None,
    sample_size: int | None,
    source_note: str | None,
) -> CalibrationObservation:
    """Verilen (zaten sahiplik kontrolunden gecmis) `run`'a yeni bir gercek
    test gozlemi ekler.

    `consent_confirmed=False` veya dort metrikten (tamamlama/sure/yanlis
    tiklama/terk) hicbiri verilmemisse hicbir satir OLUSTURULMAZ - cagiran
    taraf (bkz. app.routers.simulations) bu istisnalari HTTP hatasina cevirir.
    """

    if not consent_confirmed:
        raise CalibrationConsentRequiredError(
            "Gercek kullanilabilirlik testi verisi kaydetmek icin acik riza "
            "onayi (consent_confirmed=true) zorunludur"
        )

    if run.status != SimulationStatus.SUCCEEDED:
        raise CalibrationRunNotSucceededError(
            "Kalibrasyon gozlemi yalnizca basariyla tamamlanmis (succeeded) "
            "bir calistirmaya eklenebilir"
        )

    metrics = (
        real_task_completion_rate,
        real_median_task_duration_seconds,
        real_misclick_rate,
        real_abandonment_rate,
    )
    if all(metric is None for metric in metrics):
        raise CalibrationObservationRequiresMetricError(
            "En az bir gercek metrik (real_task_completion_rate/"
            "real_median_task_duration_seconds/real_misclick_rate/"
            "real_abandonment_rate) verilmelidir"
        )

    observation = CalibrationObservation(
        organization_id=run.organization_id,
        simulation_run_id=run.id,
        recorded_by_user_id=recorded_by_user_id,
        consent_confirmed=True,
        real_task_completion_rate=real_task_completion_rate,
        real_median_task_duration_seconds=real_median_task_duration_seconds,
        real_misclick_rate=real_misclick_rate,
        real_abandonment_rate=real_abandonment_rate,
        sample_size=sample_size,
        source_note=source_note,
    )
    session.add(observation)
    await session.flush()
    return observation


async def list_observations_for_run(
    session: AsyncSession, *, organization_id: uuid.UUID, run_id: uuid.UUID
) -> list[CalibrationObservation]:
    """Bir run'a bagli tum gozlemleri, en yeniden en eskiye siralayarak dondurur."""

    query = (
        select(CalibrationObservation)
        .where(
            CalibrationObservation.organization_id == organization_id,
            CalibrationObservation.simulation_run_id == run_id,
        )
        .order_by(CalibrationObservation.created_at.desc())
    )
    result = await session.execute(query)
    return list(result.scalars().all())


__all__ = ["record_observation", "list_observations_for_run"]
