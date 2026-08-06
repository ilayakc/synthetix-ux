"""Adim 3: `GET /api/simulations/runs/{run_id}/personas` salt-okunur
endpoint'i icin testler.

Bu dosya `tests/test_persona_launch_persistence.py`deki launch/persist
testlerini TEKRARLAMAZ - yalnizca zaten kaydedilmis `Persona` satirlarini
okuyan bu endpoint'in sahiplik/tenant izolasyonu, siralama, response sekli
ve run durumundan bagimsizligini kapsar.
"""

import uuid

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.personas import Persona
from app.models.simulations import SimulationRun, SimulationStatus
from app.services import personas
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


# --- Yardimcilar (tests/test_persona_launch_persistence.py ile ayni desen) --


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient) -> dict:
    email = _unique_email()
    response = client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": "CorrectHorse123!",
            "organization_name": f"Org {uuid.uuid4().hex[:8]}",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _csrf_headers(client: TestClient) -> dict:
    token = None
    for cookie in client.cookies.jar:
        if cookie.name == "csrf_token":
            token = cookie.value
    assert token, "csrf_token cookie set olmali"
    return {"X-CSRF-Token": token}


def _snapshot_cookies(client: TestClient) -> list[tuple[str, str, str, str]]:
    return [(cookie.name, cookie.value, cookie.path, cookie.domain) for cookie in client.cookies.jar]


def _restore_cookies(client: TestClient, snapshot: list[tuple[str, str, str, str]]) -> None:
    client.cookies.clear()
    for name, value, path, domain in snapshot:
        client.cookies.set(name, value, domain=domain, path=path)


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "Personas endpoint testi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def _patch_draft(client: TestClient, draft_id: str, payload: dict) -> dict:
    response = client.patch(
        f"/api/tests/drafts/{draft_id}", json={"payload": payload}, headers=_csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _basic_payload(project_id: str, *, persona_count: int = 1000) -> dict:
    return {
        "project_id": project_id,
        "name": f"Personas endpoint testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": persona_count,
        "target_audience": "Genel kullanicilar",
        "persona_preset_id": personas.builtin_preset_id("general_web_users"),
        "authorization_confirmed": True,
        "modules": [],
    }


def _ab_payload(project_id: str, *, persona_count: int = 1000) -> dict:
    payload = _basic_payload(project_id, persona_count=persona_count)
    payload["test_type"] = "ab_comparison"
    payload["new_url"] = "https://example.com/variant-b"
    return payload


def _launch_single(client: TestClient, *, persona_count: int = 1000, with_persona: bool = True) -> dict:
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], persona_count=persona_count)
    if not with_persona:
        del payload["persona_preset_id"]
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    return launch_response.json()


async def _load_personas(run_id: str) -> list[Persona]:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await session.execute(
                select(Persona).where(Persona.simulation_run_id == uuid.UUID(run_id)).order_by(Persona.index)
            )
            return list(result.scalars().all())
    finally:
        await engine.dispose()


async def _load_simulation_run(run_id: str) -> SimulationRun:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await session.execute(select(SimulationRun).where(SimulationRun.id == uuid.UUID(run_id)))
            return result.scalar_one()
    finally:
        await engine.dispose()


async def _set_run_status(run_id: str, status: SimulationStatus) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            run.status = status
            if status in (SimulationStatus.SUCCEEDED, SimulationStatus.FAILED):
                run.result = {"stub": True} if status == SimulationStatus.SUCCEEDED else None
                if status == SimulationStatus.FAILED:
                    run.error = "test induced failure"
            await session.commit()
    finally:
        await engine.dispose()


# --- A. Basarili listeleme ---------------------------------------------------


def test_list_personas_returns_all_rows_sorted_by_index(client: TestClient):
    body = _launch_single(client, persona_count=1000)
    run_id = body["simulation_run_ids"][0]

    db_rows = anyio.run(_load_personas, run_id)
    run = anyio.run(_load_simulation_run, run_id)
    assert db_rows  # general_web_users preseti persona secer

    response = client.get(f"/api/simulations/runs/{run_id}/personas")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert len(payload) == len(db_rows)

    # Siralama: index ASC.
    indices = [item["index"] for item in payload]
    assert indices == sorted(indices)
    assert indices == [row.index for row in db_rows]

    assert sum(item["population_weight"] for item in payload) == run.input_snapshot["persona_count"] == 1000

    for item, row in zip(payload, db_rows, strict=True):
        assert item["id"] == str(row.id)
        assert item["simulation_run_id"] == str(row.simulation_run_id) == run_id
        assert item["label"] == row.label
        assert item["attributes"] == row.attributes
        assert item["population_weight"] == row.population_weight
        # G. Response dogrulamasi.
        assert item["index"] >= 0
        assert item["population_weight"] > 0
        assert uuid.UUID(item["id"])
        assert uuid.UUID(item["simulation_run_id"])
        assert isinstance(item["created_at"], str) and item["created_at"]

    # Attributes yalnizca bilinen boyutlari icerir (yeni/yasakli alan yok).
    for item in payload:
        assert set(item["attributes"]) <= set(personas.PERSONA_DIMENSIONS)


# --- B. Persona bulunmayan eski run -----------------------------------------


def test_list_personas_returns_empty_list_when_no_persona_selection_was_made(client: TestClient):
    body = _launch_single(client, persona_count=300, with_persona=False)
    run_id = body["simulation_run_ids"][0]

    db_rows = anyio.run(_load_personas, run_id)
    assert db_rows == []

    response = client.get(f"/api/simulations/runs/{run_id}/personas")
    assert response.status_code == 200, response.text
    assert response.json() == []


# --- C. Olmayan run ----------------------------------------------------------


def test_list_personas_for_nonexistent_run_returns_404(client: TestClient):
    _register(client)

    response = client.get(f"/api/simulations/runs/{uuid.uuid4()}/personas")
    assert response.status_code == 404


# --- D. Baska organizasyon ----------------------------------------------------


@pytest.mark.security
def test_list_personas_for_other_organizations_run_returns_404_without_leaking(client: TestClient):
    body = _launch_single(client, persona_count=1000)
    run_id = body["simulation_run_ids"][0]
    snapshot_a = _snapshot_cookies(client)

    _register(client)  # organizasyon B'ye gecer

    cross_org_response = client.get(f"/api/simulations/runs/{run_id}/personas")
    assert cross_org_response.status_code == 404
    # Ayni run icin sahibi olmayan bir organizasyona, run'in mevcut olmadigi
    # durumla AYNI genel mesaj donuyor - "var ama baskasina ait" bilgisi sizmaz.
    not_found_detail = cross_org_response.json()["detail"]
    truly_missing_response = client.get(f"/api/simulations/runs/{uuid.uuid4()}/personas")
    assert truly_missing_response.status_code == 404
    assert truly_missing_response.json()["detail"] == not_found_detail

    _restore_cookies(client, snapshot_a)
    own_response = client.get(f"/api/simulations/runs/{run_id}/personas")
    assert own_response.status_code == 200
    assert len(own_response.json()) > 0


# --- E. A/B varyantlari -------------------------------------------------------


def test_list_personas_ab_variants_have_separate_rows_with_identical_content(client: TestClient):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _ab_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_ids = launch_response.json()["simulation_run_ids"]
    assert len(run_ids) == 2

    response_a = client.get(f"/api/simulations/runs/{run_ids[0]}/personas")
    response_b = client.get(f"/api/simulations/runs/{run_ids[1]}/personas")
    assert response_a.status_code == 200
    assert response_b.status_code == 200
    payload_a = response_a.json()
    payload_b = response_b.json()

    ids_a = {item["id"] for item in payload_a}
    ids_b = {item["id"] for item in payload_b}
    assert ids_a.isdisjoint(ids_b)
    assert {item["simulation_run_id"] for item in payload_a} == {run_ids[0]}
    assert {item["simulation_run_id"] for item in payload_b} == {run_ids[1]}

    def _content(items: list[dict]) -> list[tuple]:
        return [
            (
                item["index"],
                item["label"],
                tuple(sorted(item["attributes"].items())),
                item["population_weight"],
            )
            for item in items
        ]

    assert _content(payload_a) == _content(payload_b)


# --- F. Run durumlari ----------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", "cancelled"])
def test_list_personas_works_regardless_of_run_status(client: TestClient, status: str):
    body = _launch_single(client, persona_count=500)
    run_id = body["simulation_run_ids"][0]

    if status != "queued":
        anyio.run(_set_run_status, run_id, SimulationStatus(status))

    response = client.get(f"/api/simulations/runs/{run_id}/personas")
    assert response.status_code == 200, response.text
    assert len(response.json()) > 0
