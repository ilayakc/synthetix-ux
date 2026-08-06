"""Faz 3C.2B1: `ai_report` launch akisi (readiness gating, ayri/paylasilan AI
rezervasyonu, atomik bakiye, idempotency, context snapshot) uctan uca testleri
(MODULE/READINESS 4-7, QUOTE/RESERVATION 13-18, A/B 19-22, CONTEXT 32-33).
"""

import uuid

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.billing import ChipLedgerEntry, ChipLedgerEntryType, ChipReservation
from app.models.personas import Persona
from app.models.simulations import SimulationRun
from app.models.tests import TestDefinition
from app.services import personas as personas_service
from app.services import test_wizard as wizard_service
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


# --- Yardimcilar (tests/test_persona_launch_persistence.py deseni) ----------


def _csrf_headers(client: TestClient) -> dict:
    token = next((c.value for c in client.cookies.jar if c.name == "csrf_token"), None)
    assert token
    return {"X-CSRF-Token": token}


def _register(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": f"user-{uuid.uuid4().hex[:12]}@example.com",
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects", json={"name": f"Proje {uuid.uuid4().hex[:8]}"}, headers=_csrf_headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def _patch_draft(client: TestClient, draft_id: str, payload: dict) -> None:
    response = client.patch(
        f"/api/tests/drafts/{draft_id}", json={"payload": payload}, headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text


def _launch(client: TestClient, draft_id: str):
    return client.post(f"/api/tests/drafts/{draft_id}/launch", headers=_csrf_headers(client))


def _payload(project_id: str, *, modules, ab=False, with_persona=True) -> dict:
    payload = {
        "project_id": project_id,
        "name": f"AI report testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": 1000,
        "target_audience": "Genel kullanicilar",
        "authorization_confirmed": True,
        "modules": modules,
    }
    # `ai_report` artik en az bir persona secimi gerektirir (bkz. app.services.
    # test_wizard.validate_ai_report_persona_requirement); bu yuzden rezervasyon/
    # atomiklik mekanigini olcen mevcut testlerin gecerli bir persona secimi
    # tasimasi gerekir. Persona-suz ("Belirtme") senaryolari acikca
    # `with_persona=False` gecer.
    if with_persona:
        payload["persona_preset_id"] = personas_service.builtin_preset_id("general_web_users")
    if ab:
        payload["test_type"] = "ab_comparison"
        payload["new_url"] = "https://example.com/variant-b"
    return payload


async def _load_personas(run_id: str) -> list[Persona]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            result = await s.execute(
                select(Persona)
                .where(Persona.simulation_run_id == uuid.UUID(run_id))
                .order_by(Persona.index)
            )
            return list(result.scalars().all())
    finally:
        await engine.dispose()


async def _balance(org_id: str) -> int:
    from app.services import chip_ledger

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            return await chip_ledger.get_chip_balance(s, uuid.UUID(org_id))
    finally:
        await engine.dispose()


async def _credit(org_id: str, amount: int) -> None:
    from app.services import chip_ledger

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            await chip_ledger.credit(s, uuid.UUID(org_id), amount, "test credit")
            await s.commit()
    finally:
        await engine.dispose()


async def _reservations(org_id: str) -> list[ChipReservation]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            result = await s.execute(
                select(ChipReservation).where(ChipReservation.organization_id == uuid.UUID(org_id))
            )
            return list(result.scalars().all())
    finally:
        await engine.dispose()


async def _reserve_ledger_count(org_id: str, signed_amount: int) -> int:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            result = await s.execute(
                select(func.count())
                .select_from(ChipLedgerEntry)
                .where(
                    ChipLedgerEntry.organization_id == uuid.UUID(org_id),
                    ChipLedgerEntry.entry_type == ChipLedgerEntryType.RESERVE,
                    ChipLedgerEntry.amount == signed_amount,
                )
            )
            return int(result.scalar_one())
    finally:
        await engine.dispose()


async def _counts(org_id: str) -> tuple[int, int, int]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            org = uuid.UUID(org_id)
            defs = (
                await s.execute(
                    select(func.count())
                    .select_from(TestDefinition)
                    .where(TestDefinition.organization_id == org)
                )
            ).scalar_one()
            runs = (
                await s.execute(
                    select(func.count())
                    .select_from(SimulationRun)
                    .where(SimulationRun.organization_id == org)
                )
            ).scalar_one()
            reservations = (
                await s.execute(
                    select(func.count())
                    .select_from(ChipReservation)
                    .where(ChipReservation.organization_id == org)
                )
            ).scalar_one()
            return defs, runs, reservations
    finally:
        await engine.dispose()


async def _load_run(run_id: str) -> SimulationRun:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            result = await s.execute(select(SimulationRun).where(SimulationRun.id == uuid.UUID(run_id)))
            return result.scalar_one()
    finally:
        await engine.dispose()


@pytest.fixture
def ai_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ai_report_enabled", True)
    yield


# --- READINESS -------------------------------------------------------------


def test_catalog_hides_ai_report_when_disabled(client: TestClient):
    _register(client)
    response = client.get("/api/analysis-modules/catalog", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    keys = {m["key"] for m in response.json()["modules"]}
    assert "ai_report" not in keys


def test_catalog_shows_ai_report_when_enabled(client: TestClient, ai_enabled):
    _register(client)
    response = client.get("/api/analysis-modules/catalog", headers=_csrf_headers(client))
    keys = {m["key"] for m in response.json()["modules"]}
    assert "ai_report" in keys


def test_launch_rejected_when_ai_report_disabled(client: TestClient):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 400, response.text

    # Hicbir yan etki olusmamali: TestDefinition/SimulationRun/rezervasyon yok.
    defs, runs, reservations = anyio.run(_counts, info["organization_id"])
    assert (defs, runs, reservations) == (0, 0, 0)


def test_launch_allowed_when_ai_report_enabled(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text


# --- Ayri / paylasilan rezervasyon -----------------------------------------


def test_separate_baseline_and_ai_reservations(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    # campaign_cta_test (35 baseline) + ai_report (50 ayri).
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["campaign_cta_test", "ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run_id = response.json()["simulation_run_ids"][0]

    run = anyio.run(_load_run, run_id)
    assert run.chip_reservation_id is not None
    assert run.ai_chip_reservation_id is not None
    assert run.chip_reservation_id != run.ai_chip_reservation_id

    reservations = {r.id: r.amount for r in anyio.run(_reservations, info["organization_id"])}
    assert reservations[run.chip_reservation_id] == 35
    assert reservations[run.ai_chip_reservation_id] == 50


def test_only_ai_scenario_creates_no_baseline_reservation(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    # persona 1000 + ucretsiz hak -> baseline 0; yalnizca AI 50.
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run = anyio.run(_load_run, response.json()["simulation_run_ids"][0])
    assert run.chip_reservation_id is None  # baseline gereksiz -> olusmadi
    assert run.ai_chip_reservation_id is not None

    reservations = anyio.run(_reservations, info["organization_id"])
    assert len(reservations) == 1
    assert reservations[0].amount == 50


def test_ab_shares_single_ai_reservation(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"], ab=True))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run_ids = response.json()["simulation_run_ids"]
    assert len(run_ids) == 2

    run_a = anyio.run(_load_run, run_ids[0])
    run_b = anyio.run(_load_run, run_ids[1])
    assert run_a.ai_chip_reservation_id is not None
    assert run_a.ai_chip_reservation_id == run_b.ai_chip_reservation_id

    # AI icin YALNIZCA bir -50 RESERVE ledger kaydi (100 degil, tek ucret).
    assert anyio.run(_reserve_ledger_count, info["organization_id"], -50) == 1
    reservations = [r for r in anyio.run(_reservations, info["organization_id"]) if r.amount == 50]
    assert len(reservations) == 1


def test_context_snapshot_written_with_ai_report_intent(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    run = anyio.run(_load_run, response.json()["simulation_run_ids"][0])
    snap = run.input_snapshot
    assert snap["target_task"] == "Anasayfadan urun bul"
    assert snap["test_name"] == run.input_snapshot["test_name"]  # gercek test adi yazildi
    assert snap["test_name"]  # bos degil
    assert snap["methodology_context"]  # server-controlled sabit
    assert "test_description" in snap  # alan mevcut (gercek kaynak yoksa None)
    assert "ai_report" in snap["modules"]  # niyet kalici


# --- Atomik bakiye ----------------------------------------------------------


def test_balance_enough_for_baseline_only_rejects_atomically(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["campaign_cta_test", "ai_report"]))
    # Yalnizca baseline'a (35) yeter, AI (50) ile toplama (85) yetmez.
    anyio.run(_credit, info["organization_id"], 35)

    response = _launch(client, draft["id"])
    assert response.status_code == 402, response.text

    # Yarim rezervasyon kalmamali (ne baseline ne AI), run/def olusmamali.
    defs, runs, reservations = anyio.run(_counts, info["organization_id"])
    assert (defs, runs, reservations) == (0, 0, 0)


def test_balance_enough_for_ai_but_not_total_rejects_atomically(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["campaign_cta_test", "ai_report"]))
    # 50, AI tek basina yeter ama toplam 85'e yetmez.
    anyio.run(_credit, info["organization_id"], 50)

    response = _launch(client, draft["id"])
    assert response.status_code == 402, response.text
    _, _, reservations = anyio.run(_counts, info["organization_id"])
    assert reservations == 0


def test_idempotent_relaunch_does_not_duplicate_ai_reservation(client: TestClient, ai_enabled):
    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    first = _launch(client, draft["id"])
    assert first.status_code == 200
    second = _launch(client, draft["id"])
    assert second.status_code == 200
    assert second.json()["simulation_run_ids"] == first.json()["simulation_run_ids"]

    # AI rezervasyonu tam olarak bir kez olusmus olmali.
    reservations = [r for r in anyio.run(_reservations, info["organization_id"]) if r.amount == 50]
    assert len(reservations) == 1


# --- Persona zorunlulugu (ai_report en az bir temsili persona gerektirir) ----
#
# Regresyon: onceden `ai_report` "Belirtme" (persona secimi yok) ile launch
# edilebiliyordu; baseline calisip 50 AI Chip rezerve edildikten SONRA pipeline
# init `invalid_persona_set` ile basarisiz oluyor, AI ucreti gec iade
# ediliyordu. Artik gecersiz kombinasyon launch'ta, hicbir yan etki uretilmeden
# reddedilir (bkz. app.services.test_wizard.validate_ai_report_persona_requirement).


def test_ai_report_with_preset_persists_representative_personas(client: TestClient, ai_enabled):
    """(1) ai_report + persona preset -> launch basarili, Persona satirlari olusur."""

    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"]))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run_id = response.json()["simulation_run_ids"][0]

    persona_rows = anyio.run(_load_personas, run_id)
    assert 1 <= len(persona_rows) <= personas_service.MAX_REPRESENTATIVE_PERSONAS
    assert all(p.simulation_run_id == uuid.UUID(run_id) for p in persona_rows)
    assert sorted(p.index for p in persona_rows) == list(range(len(persona_rows)))
    assert sum(p.population_weight for p in persona_rows) == 1000


def test_ai_report_with_custom_distribution_persists_personas(client: TestClient, ai_enabled):
    """(2) ai_report + ozel persona dagilimi -> launch basarili, Persona satirlari olusur."""

    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    payload = _payload(project["id"], modules=["ai_report"], with_persona=False)
    payload["persona_distribution"] = {
        personas_service.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 50},
            {"key": "desktop", "label": "Masaustu", "weight": 50},
        ]
    }
    _patch_draft(client, draft["id"], payload)
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run_id = response.json()["simulation_run_ids"][0]

    persona_rows = anyio.run(_load_personas, run_id)
    assert persona_rows
    assert sum(p.population_weight for p in persona_rows) == 1000


def test_ai_report_without_persona_selection_rejected_before_side_effects(
    client: TestClient, ai_enabled
):
    """(3)+(6) ai_report + persona secimi yok -> launch ONCESI aciklayici
    validation hatasi; hicbir run/persona/rezervasyon/ledger yan etkisi kalmaz."""

    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"], with_persona=False))
    anyio.run(_credit, info["organization_id"], 500)
    balance_before = anyio.run(_balance, info["organization_id"])
    assert balance_before == 500

    response = _launch(client, draft["id"])
    assert response.status_code == 400, response.text
    # Tek kaynak mesaj: kullaniciyi persona adimina yonlendiren aciklama.
    assert response.json()["detail"] == wizard_service.AI_REPORT_PERSONA_REQUIRED_MESSAGE

    # Hicbir yan etki: TestDefinition/SimulationRun/rezervasyon olusmamali.
    defs, runs, reservations = anyio.run(_counts, info["organization_id"])
    assert (defs, runs, reservations) == (0, 0, 0)
    # Ledger'da hicbir RESERVE kaydi (baseline VEYA AI) olusmamali; bakiye aynen kalmali.
    assert anyio.run(_reserve_ledger_count, info["organization_id"], -50) == 0
    assert anyio.run(_balance, info["organization_id"]) == 500


def test_baseline_without_ai_report_and_no_persona_still_launches(client: TestClient):
    """(4) ai_report OLMAYAN temel test + "Belirtme" -> mevcut davranis bozulmaz:
    launch basarili ve (persona secimi olmadigi icin) hic Persona satiri olusmaz."""

    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=[], with_persona=False))

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run_id = response.json()["simulation_run_ids"][0]

    run = anyio.run(_load_run, run_id)
    assert "persona_sample" not in run.input_snapshot
    assert anyio.run(_load_personas, run_id) == []
    # `info` yalnizca organizasyonu kanitlamak icin kullanildi.
    assert info["organization_id"]


def test_ab_ai_report_both_runs_get_identical_representative_projection(
    client: TestClient, ai_enabled
):
    """(5) A/B + ai_report -> iki run da AYNI temsili persona projeksiyonunu alir
    (paylasilan cohort; yalnizca simulation_run_id/PK farklidir)."""

    info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    _patch_draft(client, draft["id"], _payload(project["id"], modules=["ai_report"], ab=True))
    anyio.run(_credit, info["organization_id"], 500)

    response = _launch(client, draft["id"])
    assert response.status_code == 200, response.text
    run_ids = response.json()["simulation_run_ids"]
    assert len(run_ids) == 2

    rows_a = anyio.run(_load_personas, run_ids[0])
    rows_b = anyio.run(_load_personas, run_ids[1])
    assert rows_a and rows_b

    def _content(rows: list[Persona]) -> list[tuple]:
        return [(p.index, p.label, tuple(sorted(p.attributes.items())), p.population_weight) for p in rows]

    # Ayni cohort projeksiyonu: index/label/attributes/agirlik birebir ayni.
    assert _content(rows_a) == _content(rows_b)
    # Ama satirlar (PK) ve run baglantisi ayri.
    assert {p.id for p in rows_a}.isdisjoint({p.id for p in rows_b})
    assert {p.simulation_run_id for p in rows_a} == {uuid.UUID(run_ids[0])}
    assert {p.simulation_run_id for p in rows_b} == {uuid.UUID(run_ids[1])}
