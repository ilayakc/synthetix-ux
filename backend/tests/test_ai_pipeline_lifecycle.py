"""Faz 3C.2B2: AI pipeline lifecycle (initialization + AI reservation
settlement) reconciliation cycle testleri.

Fixture deseni `tests/test_ai_pipeline_worker.py`/`test_ai_pipeline_worker_scheduling.py`
ile AYNIDIR (`maker` fixture'i - test engine uzerine kurulu, GERCEK commit
eden bir `async_sessionmaker`; test sonunda TUM tablolar TRUNCATE ile
temizlenir): lifecycle cycle'lari kendi ayri transaction'larini/advisory
lock'larini yonetir, bu nedenle `conftest.session` fixture'inin rollback eden
tek-transaction modeli ile UYUMSUZDUR (bkz. test_ai_pipeline_worker.py modul
dokstring'i - ayni gerekce).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    AIPipelineRun,
    AIPipelineStage,
    ChipLedgerEntry,
    ChipLedgerEntryType,
    ChipReservation,
    ChipReservationStatus,
    Organization,
    Persona,
    Project,
    Report,
    SimulationRun,
    TestDefinition,
    TestVariant,
)
from app.models.ai_pipeline import AIPipelineStageStatus, AIPipelineStageType, AIPipelineStatus
from app.models.simulations import SimulationStatus
from app.services import chip_ledger
from app.services.ai_pipeline import lifecycle, orchestration
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables

pytestmark = pytest.mark.integration


# =================================================================================
# Fixtures / yardimcilar
# =================================================================================


@pytest.fixture
async def maker(test_engine):
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


async def _make_org_and_variant(
    session, *, org: Organization | None = None
) -> tuple[Organization, TestVariant]:
    if org is None:
        org = Organization(name="Lifecycle Org", slug=f"lifecycle-org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()
    project = Project(organization_id=org.id, name=f"Lifecycle Project {uuid.uuid4().hex[:8]}")
    session.add(project)
    await session.flush()
    test_definition = TestDefinition(
        organization_id=org.id, project_id=project.id, name=f"Lifecycle Test {uuid.uuid4().hex[:8]}"
    )
    session.add(test_definition)
    await session.flush()
    variant = TestVariant(
        organization_id=org.id,
        test_definition_id=test_definition.id,
        name=f"Variant {uuid.uuid4().hex[:6]}",
        config={},
    )
    session.add(variant)
    await session.flush()
    return org, variant


async def _seed_group(
    session_maker: async_sessionmaker,
    *,
    count: int = 1,
    persona_count: int = 30,
    num_personas: int = 3,
    ai_chips: int = 50,
    statuses: list[SimulationStatus] | None = None,
    with_report: list[bool] | None = None,
    with_personas: list[bool] | None = None,
    modules: list[str] | None = None,
    reservation_ids: list[uuid.UUID | None] | None = None,
    org: Organization | None = None,
) -> dict:
    """Bir launch grubu (N SimulationRun, paylasilan `launch_run_id` + varsayilan
    olarak paylasilan `ai_chip_reservation_id`) olusturur ve GERCEK commit eder.

    Donen sozluk: `organization_id`, `launch_run_id`, `ai_reservation_id`
    (varsayilan/ilk rezervasyon), `run_ids` (liste)."""

    statuses = statuses or [SimulationStatus.SUCCEEDED] * count
    with_report = with_report if with_report is not None else [True] * count
    with_personas = with_personas if with_personas is not None else [True] * count
    modules = modules if modules is not None else ["ai_report"]

    async with session_maker() as session:
        org_obj, _ = await _make_org_and_variant(session, org=org)
        await chip_ledger.credit(session, org_obj.id, 100_000, "seed")
        launch_run_id = uuid.uuid4()

        default_reservation = await chip_ledger.reserve_chips(
            session, org_obj.id, ai_chips, "ai seed", run_id=launch_run_id
        )

        run_ids: list[uuid.UUID] = []
        for i in range(count):
            _, variant = await _make_org_and_variant(session, org=org_obj)
            if reservation_ids is not None:
                res_id = reservation_ids[i]
            else:
                res_id = default_reservation.id

            weights: list[int]
            base = persona_count // num_personas
            weights = [base] * num_personas
            weights[-1] += persona_count - sum(weights)

            run = SimulationRun(
                organization_id=org_obj.id,
                test_variant_id=variant.id,
                status=statuses[i],
                deterministic_seed=42 + i,
                model_version="v0",
                input_snapshot={
                    "persona_count": persona_count,
                    "source_type": "url",
                    "modules": modules,
                },
                result={
                    "metrics": {"task_completion_probability": {"point_estimate": 0.8}},
                    "page_feature_snapshot": {"nav_depth": 2, "primary_cta_count": 1},
                },
                launch_run_id=launch_run_id,
                ai_chip_reservation_id=res_id,
            )
            session.add(run)
            await session.flush()

            if with_report[i]:
                session.add(
                    Report(organization_id=org_obj.id, simulation_run_id=run.id, title="Rapor", content={})
                )
                await session.flush()

            if with_personas[i]:
                for index, weight in enumerate(weights):
                    session.add(
                        Persona(
                            simulation_run_id=run.id,
                            index=index,
                            label=f"Persona {index}",
                            attributes={"age_range": "25_34"},
                            population_weight=weight,
                        )
                    )
                await session.flush()

            run_ids.append(run.id)

        await session.commit()

    return {
        "organization_id": org_obj.id,
        "launch_run_id": launch_run_id,
        "ai_reservation_id": default_reservation.id,
        "run_ids": run_ids,
    }


async def _pipeline_count(session_maker: async_sessionmaker) -> int:
    async with session_maker() as session:
        return (await session.execute(select(func.count()).select_from(AIPipelineRun))).scalar_one()


async def _stage_count(session_maker: async_sessionmaker) -> int:
    async with session_maker() as session:
        return (await session.execute(select(func.count()).select_from(AIPipelineStage))).scalar_one()


async def _get_reservation(session_maker: async_sessionmaker, reservation_id: uuid.UUID) -> ChipReservation:
    async with session_maker() as session:
        reservation = await session.get(ChipReservation, reservation_id)
        assert reservation is not None
        return reservation


async def _get_run(session_maker: async_sessionmaker, run_id: uuid.UUID) -> SimulationRun:
    async with session_maker() as session:
        run = await session.get(SimulationRun, run_id)
        assert run is not None
        return run


async def _release_count(session_maker: async_sessionmaker, reservation_id: uuid.UUID) -> int:
    async with session_maker() as session:
        result = await session.execute(
            select(ChipLedgerEntry).where(
                ChipLedgerEntry.reference_id == reservation_id,
                ChipLedgerEntry.entry_type == ChipLedgerEntryType.RELEASE,
            )
        )
        return len(result.scalars().all())


async def _consume_count(session_maker: async_sessionmaker, reservation_id: uuid.UUID) -> int:
    async with session_maker() as session:
        result = await session.execute(
            select(ChipLedgerEntry).where(
                ChipLedgerEntry.reference_id == reservation_id,
                ChipLedgerEntry.entry_type == ChipLedgerEntryType.CONSUME,
            )
        )
        return len(result.scalars().all())


async def _set_pipeline_status_direct(
    session_maker: async_sessionmaker, *, organization_id: uuid.UUID, simulation_run_id: uuid.UUID, status
) -> AIPipelineRun:
    """Settlement testleri icin worker'i CALISTIRMADAN, dogrudan istenen
    terminal statude bir `AIPipelineRun` olusturur (settlement YALNIZCA
    `AIPipelineRun.status`a bakar, stage'lere DEGIL)."""

    async with session_maker() as session:
        pipeline = AIPipelineRun(
            organization_id=organization_id, simulation_run_id=simulation_run_id, status=status
        )
        session.add(pipeline)
        await session.commit()
        return pipeline


# =================================================================================
# INITIALIZATION (spec 1-20)
# =================================================================================


async def test_single_run_creates_one_pipeline(maker):
    await _seed_group(maker, count=1)
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_initialized == 1
    assert result.pipelines_initialized == 1
    assert await _pipeline_count(maker) == 1


async def test_first_stage_is_queued_evidence(maker):
    seed = await _seed_group(maker, count=1)
    await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    async with maker() as session:
        pipeline = (
            await session.execute(
                select(AIPipelineRun).where(AIPipelineRun.simulation_run_id == seed["run_ids"][0])
            )
        ).scalar_one()
        assert pipeline.status == AIPipelineStatus.QUEUED
        stage = (
            await session.execute(
                select(AIPipelineStage).where(AIPipelineStage.ai_pipeline_run_id == pipeline.id)
            )
        ).scalar_one()
        assert stage.stage_type == AIPipelineStageType.EVIDENCE_PREPARATION
        assert stage.status == AIPipelineStageStatus.QUEUED
        assert stage.provider is None


async def test_ab_creates_two_separate_pipelines(maker):
    seed = await _seed_group(maker, count=2)
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_initialized == 1
    assert result.pipelines_initialized == 2
    async with maker() as session:
        rows = (
            (
                await session.execute(
                    select(AIPipelineRun.simulation_run_id).where(
                        AIPipelineRun.simulation_run_id.in_(seed["run_ids"])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(rows) == set(seed["run_ids"])


async def test_ab_variants_created_in_one_transaction(maker, monkeypatch):
    """Ikinci varyantin initialization'i basarisiz olursa BIRINCI varyantin
    yeni pipeline kaydi da rollback edilmelidir (spec test 4/6)."""

    seed = await _seed_group(maker, count=2)
    real_init = orchestration.initialize_ai_pipeline
    calls = {"n": 0}

    async def _flaky(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise orchestration.MissingReportError(orchestration.ERROR_MISSING_REPORT, "kasitli hata")
        return await real_init(session, **kwargs)

    monkeypatch.setattr(lifecycle.orchestration, "initialize_ai_pipeline", _flaky)

    await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert await _pipeline_count(maker) == 0
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RELEASED


async def test_variant_not_ready_creates_no_pipeline(maker):
    seed = await _seed_group(maker, count=2, statuses=[SimulationStatus.SUCCEEDED, SimulationStatus.RUNNING])
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_initialized == 0
    assert await _pipeline_count(maker) == 0
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RESERVED


async def test_variant_failed_creates_no_pipeline(maker):
    await _seed_group(maker, count=2, statuses=[SimulationStatus.SUCCEEDED, SimulationStatus.FAILED])
    await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert await _pipeline_count(maker) == 0
    # Baseline basarisiz -> bu FAZDA rezervasyona dokunulmaz (o
    # `simulation_worker._resolve_launch_group`in isidir); yalnizca init
    # yapilmadigini dogruluyoruz.


async def test_missing_report_is_permanent_error_and_releases_reservation(maker):
    seed = await _seed_group(maker, count=1, with_report=[False])
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_permanent_error == 1
    assert await _pipeline_count(maker) == 0
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RELEASED
    run = await _get_run(maker, seed["run_ids"][0])
    assert run.status == SimulationStatus.SUCCEEDED  # baseline run DEGISMEDI


async def test_missing_personas_is_permanent_error_and_releases_reservation(maker):
    seed = await _seed_group(maker, count=1, with_personas=[False])
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_permanent_error == 1
    assert await _pipeline_count(maker) == 0
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RELEASED


async def test_missing_ai_reservation_on_one_run_is_permanent_error(maker):
    seed = await _seed_group(maker, count=2, reservation_ids=None)
    # Ikinci run'in rezervasyonunu manuel olarak temizle (veri tutarsizligi).
    async with maker() as session:
        run = await session.get(SimulationRun, seed["run_ids"][1])
        assert run is not None
        run.ai_chip_reservation_id = None
        await session.commit()

    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_permanent_error == 1
    assert await _pipeline_count(maker) == 0
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RELEASED


async def test_different_reservation_ids_across_variants_rejected(maker):
    async with maker() as session:
        org, _ = await _make_org_and_variant(session)
        await chip_ledger.credit(session, org.id, 10_000, "seed")
        launch_run_id = uuid.uuid4()
        res_a = await chip_ledger.reserve_chips(session, org.id, 50, "ai a", run_id=launch_run_id)
        res_b = await chip_ledger.reserve_chips(session, org.id, 50, "ai b", run_id=uuid.uuid4())
        await session.commit()

    await _seed_group(maker, count=2, reservation_ids=[res_a.id, res_b.id], org=None)
    # NOT: yukaridaki `_seed_group` cagrisi kendi organizasyonunu kurar; bu
    # testte esas amac farkli rezervasyon id'lerinin reddedildigini
    # dogrulamaktir - `reservation_ids` parametresi iki FARKLI (ayni org'a
    # ait olmasi gerekmeyen, yalnizca deger olarak farkli) uuid tasir.
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_permanent_error == 1
    assert await _pipeline_count(maker) == 0


async def test_reservation_not_reserved_skips_initialization(maker):
    seed = await _seed_group(maker, count=1)
    async with maker() as session:
        reservation = await session.get(ChipReservation, seed["ai_reservation_id"])
        assert reservation is not None
        await chip_ledger.consume_reservation(
            session, seed["organization_id"], reservation.id, "onceden tuketildi"
        )
        await session.commit()

    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_initialized == 0
    assert await _pipeline_count(maker) == 0


async def test_ai_report_not_selected_is_noop(maker):
    seed = await _seed_group(maker, count=1, modules=["network_device_test"])
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result.groups_initialized == 0
    assert await _pipeline_count(maker) == 0
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RESERVED


async def test_second_cycle_does_not_duplicate(maker):
    await _seed_group(maker, count=2)
    await lifecycle.run_ai_pipeline_initialization_cycle(maker)
    result2 = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    assert result2.groups_initialized == 0
    assert await _pipeline_count(maker) == 2
    assert await _stage_count(maker) == 2


async def test_concurrent_cycles_create_single_pipeline_set(maker):
    await _seed_group(maker, count=2)

    # Her iki cycle de (advisory lock/idempotency sayesinde) hatasiz sonuclanir
    # - biri GERCEKTEN olusturur, digeri (lock'u sonradan alan) idempotent
    # olarak MEVCUT pipeline'lari gorup basariyla "tamamlar" (hata FIRLATMAZ).
    # Asil garanti: DB'de HICBIR duplicate satir olusmamasidir.
    await asyncio.gather(
        lifecycle.run_ai_pipeline_initialization_cycle(maker),
        lifecycle.run_ai_pipeline_initialization_cycle(maker),
    )
    assert await _pipeline_count(maker) == 2
    assert await _stage_count(maker) == 2


async def test_transient_error_leaves_reservation_reserved(maker, monkeypatch):
    seed = await _seed_group(maker, count=1)

    async def _boom(*args, **kwargs):
        raise RuntimeError("gecici altyapi hatasi")

    monkeypatch.setattr(lifecycle.orchestration, "initialize_ai_pipeline", _boom)

    with pytest.raises(RuntimeError):
        await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RESERVED
    assert await _pipeline_count(maker) == 0


async def test_permanent_error_releases_reservation(maker):
    seed = await _seed_group(maker, count=1, with_report=[False])
    await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RELEASED
    assert await _release_count(maker, seed["ai_reservation_id"]) == 1


async def test_baseline_run_status_unaffected_by_permanent_error(maker):
    seed = await _seed_group(maker, count=1, with_personas=[False])
    await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    run = await _get_run(maker, seed["run_ids"][0])
    assert run.status == SimulationStatus.SUCCEEDED


async def test_initialization_works_without_provider_context(maker):
    """`run_ai_pipeline_initialization_cycle` HICBIR provider parametresi
    ALMAZ - imzada provider yoktur, bu da provider'siz calisabilirligin
    dogrudan kanitidir."""

    import inspect

    params = list(inspect.signature(lifecycle.run_ai_pipeline_initialization_cycle).parameters)
    assert "provider" not in params
    assert "ctx" not in params

    await _seed_group(maker, count=1)
    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)
    assert result.groups_initialized == 1


async def test_initialization_module_makes_no_network_call():
    import inspect

    source = inspect.getsource(lifecycle)
    for forbidden in ("httpx", "requests", "AIProvider(", "MockAIProvider("):
        assert forbidden not in source


async def test_cross_tenant_groups_do_not_mix(maker):
    seed_a = await _seed_group(maker, count=1)
    seed_b = await _seed_group(maker, count=1)
    assert seed_a["organization_id"] != seed_b["organization_id"]

    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker, limit=10)
    assert result.groups_initialized == 2

    async with maker() as session:
        pipeline_a = (
            await session.execute(
                select(AIPipelineRun).where(AIPipelineRun.simulation_run_id == seed_a["run_ids"][0])
            )
        ).scalar_one()
        pipeline_b = (
            await session.execute(
                select(AIPipelineRun).where(AIPipelineRun.simulation_run_id == seed_b["run_ids"][0])
            )
        ).scalar_one()
        assert pipeline_a.organization_id == seed_a["organization_id"]
        assert pipeline_b.organization_id == seed_b["organization_id"]


# =================================================================================
# SETTLEMENT (spec 21-36)
# =================================================================================


async def test_single_pipeline_succeeded_consumes_reservation(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )

    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert result.groups_consumed == 1
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.CONSUMED


async def test_ab_both_succeeded_single_consume_entry(maker):
    seed = await _seed_group(maker, count=2)
    for run_id in seed["run_ids"]:
        await _set_pipeline_status_direct(
            maker,
            organization_id=seed["organization_id"],
            simulation_run_id=run_id,
            status=AIPipelineStatus.SUCCEEDED,
        )

    await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert await _consume_count(maker, seed["ai_reservation_id"]) == 1


async def test_one_succeeded_one_running_stays_reserved(maker):
    seed = await _seed_group(maker, count=2)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][1],
        status=AIPipelineStatus.RUNNING,
    )

    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert result.groups_waiting == 1
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RESERVED


@pytest.mark.parametrize(
    "failing_status", [AIPipelineStatus.FAILED, AIPipelineStatus.CANCELLED, AIPipelineStatus.PARTIAL]
)
async def test_one_terminal_failure_releases_reservation(maker, failing_status):
    seed = await _seed_group(maker, count=2)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][1],
        status=failing_status,
    )

    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert result.groups_released == 1
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RELEASED


async def test_queued_pipeline_no_movement(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.QUEUED,
    )

    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert result.groups_waiting == 1
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RESERVED


async def test_missing_pipelines_settlement_waits(maker):
    seed = await _seed_group(maker, count=2)
    # Yalnizca birinci varyant icin pipeline var; ikincisi HENUZ olusturulmadi.
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )

    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert result.groups_waiting == 1
    reservation = await _get_reservation(maker, seed["ai_reservation_id"])
    assert reservation.status == ChipReservationStatus.RESERVED


async def test_previously_released_reservation_not_released_again(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.FAILED,
    )
    await lifecycle.run_ai_reservation_settlement_cycle(maker)
    await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert await _release_count(maker, seed["ai_reservation_id"]) == 1


async def test_previously_consumed_reservation_not_consumed_again(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )
    await lifecycle.run_ai_reservation_settlement_cycle(maker)
    result2 = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    assert result2.groups_consumed == 0
    assert await _consume_count(maker, seed["ai_reservation_id"]) == 1


async def test_concurrent_settlement_produces_single_ledger_result(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )

    await asyncio.gather(
        lifecycle.run_ai_reservation_settlement_cycle(maker),
        lifecycle.run_ai_reservation_settlement_cycle(maker),
    )

    assert await _consume_count(maker, seed["ai_reservation_id"]) == 1


async def test_duplicate_cycle_does_not_change_balance(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.FAILED,
    )
    await lifecycle.run_ai_reservation_settlement_cycle(maker)
    async with maker() as session:
        balance_after_first = await chip_ledger.get_chip_balance(session, seed["organization_id"])
    await lifecycle.run_ai_reservation_settlement_cycle(maker)
    async with maker() as session:
        balance_after_second = await chip_ledger.get_chip_balance(session, seed["organization_id"])

    assert balance_after_first == balance_after_second


async def test_ab_release_is_exactly_fifty_not_hundred(maker):
    seed = await _seed_group(maker, count=2, ai_chips=50)
    async with maker() as session:
        balance_before = await chip_ledger.get_chip_balance(session, seed["organization_id"])

    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.FAILED,
    )
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][1],
        status=AIPipelineStatus.SUCCEEDED,
    )
    await lifecycle.run_ai_reservation_settlement_cycle(maker)

    async with maker() as session:
        balance_after = await chip_ledger.get_chip_balance(session, seed["organization_id"])
    assert balance_after - balance_before == 50


async def test_settlement_does_not_touch_baseline_reservation(maker):
    async with maker() as session:
        org, variant = await _make_org_and_variant(session)
        await chip_ledger.credit(session, org.id, 10_000, "seed")
        launch_run_id = uuid.uuid4()
        baseline = await chip_ledger.reserve_chips(session, org.id, 35, "baseline", run_id=launch_run_id)
        ai = await chip_ledger.reserve_chips(session, org.id, 50, "ai", run_id=launch_run_id)
        run = SimulationRun(
            organization_id=org.id,
            test_variant_id=variant.id,
            status=SimulationStatus.SUCCEEDED,
            deterministic_seed=1,
            model_version="v0",
            input_snapshot={"persona_count": 10, "modules": ["ai_report"]},
            launch_run_id=launch_run_id,
            chip_reservation_id=baseline.id,
            ai_chip_reservation_id=ai.id,
        )
        session.add(run)
        await session.commit()

    await _set_pipeline_status_direct(
        maker, organization_id=org.id, simulation_run_id=run.id, status=AIPipelineStatus.SUCCEEDED
    )
    await lifecycle.run_ai_reservation_settlement_cycle(maker)

    baseline_after = await _get_reservation(maker, baseline.id)
    assert baseline_after.status == ChipReservationStatus.RESERVED


async def test_baseline_failed_release_stays_idempotent(maker):
    """Regresyon (Faz 3C.2B1 -> B2): baseline grubu basarisiz oldugunda AI
    rezervasyonu zaten `simulation_worker._resolve_launch_group` tarafindan
    RELEASED edilmis olur; settlement cycle'i bunun UZERINE tekrar bir
    ledger hareketi URETMEMELIDIR (spec Part 7.E)."""

    from app.services import simulation_worker

    async with maker() as session:
        org, variant_a = await _make_org_and_variant(session)
        _, variant_b = await _make_org_and_variant(session, org=org)
        await chip_ledger.credit(session, org.id, 10_000, "seed")
        launch_run_id = uuid.uuid4()
        baseline = await chip_ledger.reserve_chips(session, org.id, 35, "baseline", run_id=launch_run_id)
        ai = await chip_ledger.reserve_chips(session, org.id, 50, "ai", run_id=launch_run_id)
        run_a = SimulationRun(
            organization_id=org.id,
            test_variant_id=variant_a.id,
            status=SimulationStatus.RUNNING,
            deterministic_seed=1,
            model_version="v0",
            input_snapshot={"persona_count": 10, "modules": ["ai_report"]},
            launch_run_id=launch_run_id,
            chip_reservation_id=baseline.id,
            ai_chip_reservation_id=ai.id,
        )
        run_b = SimulationRun(
            organization_id=org.id,
            test_variant_id=variant_b.id,
            status=SimulationStatus.RUNNING,
            deterministic_seed=2,
            model_version="v0",
            input_snapshot={"persona_count": 10, "modules": ["ai_report"]},
            launch_run_id=launch_run_id,
            chip_reservation_id=baseline.id,
            ai_chip_reservation_id=ai.id,
        )
        session.add_all([run_a, run_b])
        await session.flush()

        run_a.status = SimulationStatus.SUCCEEDED
        await session.flush()
        await simulation_worker._resolve_launch_group(session, run_a)
        run_b.status = SimulationStatus.FAILED
        await session.flush()
        await simulation_worker._resolve_launch_group(session, run_b)
        await session.commit()

    ai_after_baseline = await _get_reservation(maker, ai.id)
    assert ai_after_baseline.status == ChipReservationStatus.RELEASED

    # Settlement cycle bu grubu ARTIK aday olarak SECMEZ (RESERVED degil);
    # ekstra release ledger kaydi olusmaz.
    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)
    assert result.groups_scanned == 0
    assert await _release_count(maker, ai.id) == 1


# =================================================================================
# CRASH / RECOVERY (spec 37-40)
# =================================================================================


async def test_settlement_completes_in_a_later_cycle_after_crash(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )
    # "Crash" simulasyonu: bir onceki cycle hicbir zaman calismadi; sonraki
    # (bu) cagri tek basina isi tamamlar.
    result = await lifecycle.run_ai_reservation_settlement_cycle(maker)
    assert result.groups_consumed == 1


async def test_init_response_loss_does_not_duplicate(maker):
    seed = await _seed_group(maker, count=2)
    # "Response kayboldu" simulasyonu: A icin pipeline ONCEDEN (baska bir
    # commit'lenmis cycle/cagri gibi) olusturulmus, ama cagiran bunu hic
    # gormedi - sonraki init cycle A'yi TEKRAR olusturmaz, yalnizca B'yi tamamlar.
    async with maker() as session:
        await orchestration.initialize_ai_pipeline(
            session,
            organization_id=seed["organization_id"],
            simulation_run_id=seed["run_ids"][0],
            ai_requested=True,
        )
        await session.commit()

    result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    # Grup basariyla (idempotent olarak) "tamamlandi" olarak sayilir; asil
    # garanti duplicate SATIR olusmamasidir (A icin yeniden insert denenmez).
    assert result.groups_initialized == 1
    assert await _pipeline_count(maker) == 2
    assert await _stage_count(maker) == 2


async def test_late_settlement_result_does_not_double_charge(maker):
    seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=seed["organization_id"],
        simulation_run_id=seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )
    await lifecycle.run_ai_reservation_settlement_cycle(maker)
    async with maker() as session:
        balance_1 = await chip_ledger.get_chip_balance(session, seed["organization_id"])
    # Ayni "sonuc" (SUCCEEDED) tekrar gelse bile (settlement idempotent) -
    # ikinci cycle hicbir finansal etki uretmez.
    await lifecycle.run_ai_reservation_settlement_cycle(maker)
    async with maker() as session:
        balance_2 = await chip_ledger.get_chip_balance(session, seed["organization_id"])
    assert balance_1 == balance_2


def test_worker_module_never_touches_chip_ledger_directly():
    """Worker/reaper (Faz 3B.2B/3B.2C) DOGRUDAN Chip hareketi URETMEZ;
    settlement cycle TEK finansal otoritedir (spec test 40)."""

    import inspect

    from app.services.ai_pipeline import worker as ai_worker_module

    source = inspect.getsource(ai_worker_module)
    assert "chip_ledger" not in source
    assert "ChipReservation" not in source
    assert "ChipLedgerEntry" not in source


# =================================================================================
# Guvenli loglama / sonuc sekli (ek dogrulama)
# =================================================================================


async def test_cycle_results_are_safe_dataclasses_without_snapshot_fields(maker):
    await _seed_group(maker, count=1)
    init_result = await lifecycle.run_ai_pipeline_initialization_cycle(maker)

    settlement_seed = await _seed_group(maker, count=1)
    await _set_pipeline_status_direct(
        maker,
        organization_id=settlement_seed["organization_id"],
        simulation_run_id=settlement_seed["run_ids"][0],
        status=AIPipelineStatus.SUCCEEDED,
    )
    settlement_result = await lifecycle.run_ai_reservation_settlement_cycle(maker)

    for field_name in ("input_snapshot", "target_task", "prompt", "validated_output"):
        assert not hasattr(init_result, field_name)
        assert not hasattr(settlement_result, field_name)
