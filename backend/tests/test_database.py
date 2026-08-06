import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Organization,
    Persona,
    Project,
    SimulationRun,
    TestDefinition,
    TestVariant,
    User,
)
from app.models.simulations import CalibrationStatus
from tests.conftest import TEST_DATABASE_URL

# `test_engine` ve `session` fixture'lari artik tests/conftest.py'den gelir
# (izole test veritabani + her testte rollback).

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parents[1]

TENANT_TABLES = [
    "projects",
    "test_definitions",
    "test_variants",
    "persona_presets",
    "simulation_runs",
    "reports",
    "entitlements",
    "chip_ledger_entries",
    "chip_reservations",
    "audit_logs",
    "ai_pipeline_runs",
]

ALL_TABLES = TENANT_TABLES + [
    "organizations",
    "users",
    "memberships",
    "refresh_tokens",
    "password_reset_tokens",
    "personas",
    "ai_pipeline_stages",
]

# --- Migration smoke testleri -------------------------------------------------


def _run_alembic(*args: str) -> subprocess.CompletedProcess:
    # DATABASE_URL'i acikca TEST_DATABASE_URL'e yonlendiriyoruz: bu subprocess
    # `app.config.settings` uzerinden degil, kendi ortam degiskenlerinden
    # (.env / container ENV) DATABASE_URL okur. Bu override olmadan
    # `downgrade base` / `upgrade head` gelistirme veritabanini hedef alip
    # tum semayi silebilirdi; artik daima izole test DB'sine karsi calisir.
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        env=env,
    )


def test_alembic_upgrade_head_is_idempotent():
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr

    current = _run_alembic("current")
    heads = _run_alembic("heads")
    assert current.returncode == 0, current.stderr
    assert heads.returncode == 0, heads.stderr

    current_revision = current.stdout.strip().split()[0]
    head_revision = heads.stdout.strip().split()[0]
    assert current_revision == head_revision


def test_alembic_downgrade_and_upgrade_roundtrip():
    """Migration hem asagi hem yukari yonde hatasiz calisir (geri donusturulebilir).

    Destructive (tum semayi `downgrade base` ile siler): DATABASE_URL, `_run_alembic`
    icinde TEST_DATABASE_URL'e yonlendirildigi icin bu daima izole test
    veritabanina karsi calisir, gelistirme veritabanini asla etkilemez.
    Zaten modul-seviyeli `pytestmark = pytest.mark.integration` kapsaminda.
    """
    downgrade_result = _run_alembic("downgrade", "base")
    assert downgrade_result.returncode == 0, downgrade_result.stderr

    upgrade_result = _run_alembic("upgrade", "head")
    assert upgrade_result.returncode == 0, upgrade_result.stderr

    current = _run_alembic("current")
    heads = _run_alembic("heads")
    assert current.returncode == 0, current.stderr
    current_revision = current.stdout.strip().split()[0]
    head_revision = heads.stdout.strip().split()[0]
    assert current_revision == head_revision


async def test_all_expected_tables_exist(test_engine):
    def _get_table_names(sync_conn):
        return inspect(sync_conn).get_table_names()

    async with test_engine.connect() as conn:
        table_names = await conn.run_sync(_get_table_names)

    for table in ALL_TABLES:
        assert table in table_names


# --- Tenant alani (organization_id) testleri -----------------------------------


async def test_tenant_scoped_table_requires_organization_id(session: AsyncSession):
    project = Project(name="Orphan Project")  # organization_id kasitli olarak eksik
    session.add(project)

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_tenant_scoped_tables_have_organization_id_column(test_engine):
    def _get_columns(sync_conn, table_name):
        return {col["name"] for col in inspect(sync_conn).get_columns(table_name)}

    async with test_engine.connect() as conn:
        for table in TENANT_TABLES:
            columns = await conn.run_sync(_get_columns, table)
            assert "organization_id" in columns, f"{table} icin organization_id eksik"


# --- Kisit (constraint) testleri -----------------------------------------------


async def test_user_email_uniqueness_is_case_insensitive(session: AsyncSession):
    unique_local_part = uuid.uuid4().hex[:8]
    email = f"Case.Test.{unique_local_part}@Example.com"

    session.add(User(email=email, email_normalized=email.lower(), password_hash="hash"))
    await session.flush()

    session.add(User(email=email.upper(), email_normalized=email.lower(), password_hash="hash"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_organization_slug_is_unique(session: AsyncSession):
    slug = f"dup-org-{uuid.uuid4().hex[:8]}"
    session.add(Organization(name="First", slug=slug))
    await session.flush()

    session.add(Organization(name="Second", slug=slug))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_chip_ledger_entries_has_no_balance_column(test_engine):
    def _get_columns(sync_conn):
        return {col["name"] for col in inspect(sync_conn).get_columns("chip_ledger_entries")}

    async with test_engine.connect() as conn:
        columns = await conn.run_sync(_get_columns)

    assert "balance" not in columns
    assert "current_balance" not in columns
    assert "updated_at" not in columns  # append-only: guncelleme zaman damgasi yok


async def test_simulation_run_calibration_status_defaults_to_uncalibrated(session: AsyncSession):
    org = Organization(name="Sim Org", slug=f"sim-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()

    project = Project(organization_id=org.id, name="Sim Project")
    session.add(project)
    await session.flush()

    test_definition = TestDefinition(organization_id=org.id, project_id=project.id, name="Sim Test")
    session.add(test_definition)
    await session.flush()

    variant = TestVariant(
        organization_id=org.id,
        test_definition_id=test_definition.id,
        name="Variant A",
        config={},
    )
    session.add(variant)
    await session.flush()

    run = SimulationRun(
        organization_id=org.id,
        test_variant_id=variant.id,
        deterministic_seed=42,
        model_version="v0",
        input_snapshot={},
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)

    assert run.calibration_status == CalibrationStatus.UNCALIBRATED


# --- Persona tablosu: kisitlar ve CASCADE davranisi -----------------------


async def _make_simulation_run(session: AsyncSession) -> SimulationRun:
    org = Organization(name="Persona Org", slug=f"persona-org-{uuid.uuid4().hex[:8]}")
    session.add(org)
    await session.flush()

    project = Project(organization_id=org.id, name="Persona Project")
    session.add(project)
    await session.flush()

    test_definition = TestDefinition(organization_id=org.id, project_id=project.id, name="Persona Test")
    session.add(test_definition)
    await session.flush()

    variant = TestVariant(
        organization_id=org.id,
        test_definition_id=test_definition.id,
        name="Variant A",
        config={},
    )
    session.add(variant)
    await session.flush()

    run = SimulationRun(
        organization_id=org.id,
        test_variant_id=variant.id,
        deterministic_seed=42,
        model_version="v0",
        input_snapshot={},
    )
    session.add(run)
    await session.flush()
    return run


async def test_persona_deleted_when_simulation_run_is_deleted(session: AsyncSession):
    run = await _make_simulation_run(session)
    persona = Persona(
        simulation_run_id=run.id,
        index=0,
        label="Genel",
        attributes={},
        population_weight=100,
    )
    session.add(persona)
    await session.flush()
    persona_id = persona.id

    await session.delete(run)
    await session.flush()

    result = await session.execute(select(Persona).where(Persona.id == persona_id))
    assert result.scalar_one_or_none() is None


async def test_persona_index_is_unique_per_simulation_run(session: AsyncSession):
    run = await _make_simulation_run(session)
    session.add(Persona(simulation_run_id=run.id, index=0, label="A", attributes={}, population_weight=10))
    await session.flush()

    session.add(Persona(simulation_run_id=run.id, index=0, label="B", attributes={}, population_weight=20))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_persona_population_weight_must_be_positive(session: AsyncSession):
    run = await _make_simulation_run(session)
    session.add(Persona(simulation_run_id=run.id, index=0, label="A", attributes={}, population_weight=0))

    with pytest.raises(IntegrityError):
        await session.flush()
