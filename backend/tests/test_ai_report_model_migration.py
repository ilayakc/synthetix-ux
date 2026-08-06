"""Faz 3C.2B1: `SimulationRun.ai_chip_reservation_id` model/migration testleri
(MODEL/MIGRATION 27-31).
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_alembic_has_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"tek head bekleniyordu, bulundu: {heads}"


async def test_ai_chip_reservation_id_column_is_nullable(test_engine):
    def _cols(sync_conn):
        return {c["name"]: c for c in inspect(sync_conn).get_columns("simulation_runs")}

    async with test_engine.connect() as conn:
        cols = await conn.run_sync(_cols)

    assert "ai_chip_reservation_id" in cols
    assert cols["ai_chip_reservation_id"]["nullable"] is True


async def test_ai_chip_reservation_id_has_no_fk_and_no_index(test_engine):
    """`chip_reservation_id` gibi plain bir UUID: FK yok, index yok."""

    def _introspect(sync_conn):
        insp = inspect(sync_conn)
        fks = insp.get_foreign_keys("simulation_runs")
        indexes = insp.get_indexes("simulation_runs")
        return fks, indexes

    async with test_engine.connect() as conn:
        fks, indexes = await conn.run_sync(_introspect)

    assert all("ai_chip_reservation_id" not in fk["constrained_columns"] for fk in fks)
    assert all("ai_chip_reservation_id" not in (idx.get("column_names") or []) for idx in indexes)
    # `chip_reservation_id` de ayni sekilde FK'siz/indekssiz (mirror dogrulamasi).
    assert all("chip_reservation_id" not in fk["constrained_columns"] for fk in fks)


async def test_simulation_run_accepts_null_ai_reservation(session, organization: Organization):
    """AI secilmemis (eski/normal) run NULL kabul eder."""

    from app.models.projects import Project
    from app.models.tests import TestDefinition, TestVariant

    project = Project(organization_id=organization.id, name="P")
    session.add(project)
    await session.flush()
    definition = TestDefinition(organization_id=organization.id, project_id=project.id, name="D")
    session.add(definition)
    await session.flush()
    variant = TestVariant(
        organization_id=organization.id, test_definition_id=definition.id, name="V", config={}
    )
    session.add(variant)
    await session.flush()

    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.QUEUED,
        deterministic_seed=1,
        model_version="v",
        input_snapshot={},
    )
    session.add(run)
    await session.flush()
    assert run.ai_chip_reservation_id is None


async def test_simulation_run_can_carry_ai_reservation_id(session, organization: Organization):
    from app.models.projects import Project
    from app.models.tests import TestDefinition, TestVariant

    project = Project(organization_id=organization.id, name="P")
    session.add(project)
    await session.flush()
    definition = TestDefinition(organization_id=organization.id, project_id=project.id, name="D")
    session.add(definition)
    await session.flush()
    variant = TestVariant(
        organization_id=organization.id, test_definition_id=definition.id, name="V", config={}
    )
    session.add(variant)
    await session.flush()

    reservation_id = uuid.uuid4()
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.QUEUED,
        deterministic_seed=1,
        model_version="v",
        input_snapshot={},
        ai_chip_reservation_id=reservation_id,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    assert run.ai_chip_reservation_id == reservation_id
