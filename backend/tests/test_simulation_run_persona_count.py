"""Adim 4.5: `SimulationRunResponse.persona_count` alani icin testler.

Iki katmanda test edilir:
- Saf fonksiyon (`app.routers.simulations._extract_persona_count`): gecerli/
  gecersiz/sinir disi `input_snapshot["persona_count"]` degerleri icin
  savunmacı davranis.
- API katmani: `GET /runs`, `GET /runs/{id}`, cancel, retry response'larinda
  alanin tutarli sekilde bulundugunu ve `input_snapshot`'in geri kalaninin
  (persona_sample, url, seed vb.) sizmadigini dogrular.
"""

import uuid

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.simulations import SimulationRun, SimulationStatus
from app.routers.simulations import MAX_PERSONA_COUNT, MIN_PERSONA_COUNT, _extract_persona_count
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


# --- A. Saf fonksiyon: _extract_persona_count --------------------------------


def test_extract_persona_count_returns_valid_integer():
    assert _extract_persona_count({"persona_count": 1000}) == 1000


def test_extract_persona_count_returns_none_for_missing_snapshot_field():
    assert _extract_persona_count({}) is None


def test_extract_persona_count_returns_none_when_key_absent_among_other_fields():
    assert _extract_persona_count({"url": "https://example.com", "modules": []}) is None


def test_extract_persona_count_returns_none_for_bool():
    # bool, Python'da int alt sinifidir - acikca elenmelidir.
    assert _extract_persona_count({"persona_count": True}) is None
    assert _extract_persona_count({"persona_count": False}) is None


def test_extract_persona_count_returns_none_for_string():
    assert _extract_persona_count({"persona_count": "1000"}) is None


def test_extract_persona_count_returns_none_for_float():
    assert _extract_persona_count({"persona_count": 1000.0}) is None


def test_extract_persona_count_returns_none_for_out_of_range_low():
    assert _extract_persona_count({"persona_count": MIN_PERSONA_COUNT - 1}) is None


def test_extract_persona_count_returns_none_for_out_of_range_high():
    assert _extract_persona_count({"persona_count": MAX_PERSONA_COUNT + 1}) is None


def test_extract_persona_count_accepts_boundary_values():
    assert _extract_persona_count({"persona_count": MIN_PERSONA_COUNT}) == MIN_PERSONA_COUNT
    assert _extract_persona_count({"persona_count": MAX_PERSONA_COUNT}) == MAX_PERSONA_COUNT


def test_extract_persona_count_returns_none_for_non_dict_snapshot():
    assert _extract_persona_count(None) is None  # type: ignore[arg-type]


# --- B. API katmani: yardimcilar (tests/test_persona_launch_persistence.py ile ayni desen) --


def _unique_email() -> str:
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


def _register(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/register",
        json={
            "email": _unique_email(),
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


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "persona_count testi"},
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


def _launch_single(client: TestClient, *, persona_count: int = 1000) -> dict:
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = {
        "project_id": project["id"],
        "name": f"persona_count testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": persona_count,
        "target_audience": "Genel kullanicilar",
        "authorization_confirmed": True,
        "modules": [],
    }
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    return launch_response.json()


async def _corrupt_persona_count(run_id: str, bad_value: object) -> None:
    """Normal launch akisiyla asla uretilemeyecek (dogrulama zaten engeller)
    bozuk bir `persona_count` degerini dogrudan DB'ye yazar - eski/bozulmus
    bir satiri simule eder."""

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            snapshot = dict(run.input_snapshot)
            snapshot["persona_count"] = bad_value
            run.input_snapshot = snapshot
            await session.commit()
    finally:
        await engine.dispose()


# --- C. GET /runs ve GET /runs/{id} -------------------------------------------


def test_get_run_includes_persona_count(client: TestClient):
    # persona_count <= FREE_BASIC_UX_TEST_PERSONA_LIMIT (1000) tutulur - aksi
    # halde launch, kredisiz organizasyonda 402 (yetersiz Chip) ile reddedilir;
    # bu testin odagi Chip akisi degil, response alanidir.
    body = _launch_single(client, persona_count=1000)
    run_id = body["simulation_run_ids"][0]

    response = client.get(f"/api/simulations/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["persona_count"] == 1000


def test_list_runs_includes_persona_count(client: TestClient):
    body = _launch_single(client, persona_count=777)
    run_id = body["simulation_run_ids"][0]

    response = client.get("/api/simulations/runs")
    assert response.status_code == 200, response.text
    matching = [run for run in response.json() if run["id"] == run_id]
    assert len(matching) == 1
    assert matching[0]["persona_count"] == 777


def test_run_response_does_not_leak_other_input_snapshot_fields(client: TestClient):
    body = _launch_single(client, persona_count=500)
    run_id = body["simulation_run_ids"][0]

    response = client.get(f"/api/simulations/runs/{run_id}")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["persona_count"] == 500
    # input_snapshot'in geri kalani (url, source_type, pricing_version, vb.)
    # HICBIR alan adiyla dogrudan sizmamali.
    for leaked_field in ("input_snapshot", "url", "source_type", "pricing_version", "persona_sample"):
        assert leaked_field not in payload


# --- D. Cancel / retry ---------------------------------------------------------


def test_cancel_response_preserves_persona_count(client: TestClient):
    body = _launch_single(client, persona_count=888)
    run_id = body["simulation_run_ids"][0]

    response = client.post(f"/api/simulations/runs/{run_id}/cancel", headers=_csrf_headers(client))
    assert response.status_code == 200, response.text
    assert response.json()["persona_count"] == 888


async def _fail_then_retry_via_service(organization_id: str, run_id: str) -> None:
    """`SimulationRun`u basarisiz isaretleyip `simulation_worker.retry_run`u
    dogrudan (HTTP katmani DISINDA) cagirir - tek bir oturum/baglanti icinde,
    ayni desen `tests/test_atomic_ab_reservation.py` ve
    `tests/test_persona_launch_persistence.py::test_retry_reuses_existing_
    cohort_snapshot_and_persona_rows`de zaten kullanilir. Ayri motorlarla
    (NullPool) yapilan bir DB yazisinin hemen ardindan AYNI satiri HTTP
    katmani uzerinden retry etmek, bu test ortaminda ORM'in `updated_at`
    attribute'unu beklenmedik bicimde expired birakip `MissingGreenlet`
    hatasina yol acabiliyor (bkz. proje notu) - bu yuzden retry burada da
    (launch/GET disinda) servis fonksiyonu dogrudan cagrilarak, HTTP
    katmaninin AYNI (test'e ozgu) yaris durumuna girmesi onlenir."""

    from app.services import simulation_worker

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            run.status = SimulationStatus.FAILED
            run.error = "persona_count testi: kasitli basarisizlik"
            await session.flush()
            await simulation_worker.retry_run(session, uuid.UUID(organization_id), uuid.UUID(run_id))
            await session.commit()
    finally:
        await engine.dispose()


def test_retry_response_preserves_persona_count(client: TestClient):
    org = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)
    payload = {
        "project_id": project["id"],
        "name": f"persona_count retry testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": 999,
        "target_audience": "Genel kullanicilar",
        "authorization_confirmed": True,
        "modules": [],
    }
    _patch_draft(client, draft["id"], payload)
    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_id = launch_response.json()["simulation_run_ids"][0]

    anyio.run(_fail_then_retry_via_service, org["organization_id"], run_id)

    response = client.get(f"/api/simulations/runs/{run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["persona_count"] == 999


# --- E. Eski / bozuk snapshot: response'u asla 500'e dusurmez -----------------


@pytest.mark.parametrize(
    "bad_value",
    [True, False, "1000", 1000.0, MIN_PERSONA_COUNT - 1, MAX_PERSONA_COUNT + 1],
)
def test_get_run_returns_null_for_corrupted_persona_count_without_500(client: TestClient, bad_value):
    body = _launch_single(client, persona_count=500)
    run_id = body["simulation_run_ids"][0]
    anyio.run(_corrupt_persona_count, run_id, bad_value)

    response = client.get(f"/api/simulations/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["persona_count"] is None


def test_get_run_returns_null_when_persona_count_key_missing_entirely(client: TestClient):
    body = _launch_single(client, persona_count=500)
    run_id = body["simulation_run_ids"][0]

    async def _remove_persona_count(run_id: str) -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                run = await session.get(SimulationRun, uuid.UUID(run_id))
                assert run is not None
                snapshot = dict(run.input_snapshot)
                del snapshot["persona_count"]
                run.input_snapshot = snapshot
                await session.commit()
        finally:
            await engine.dispose()

    anyio.run(_remove_persona_count, run_id)

    response = client.get(f"/api/simulations/runs/{run_id}")
    assert response.status_code == 200, response.text
    assert response.json()["persona_count"] is None


# --- F. OpenAPI sozlesmesi ------------------------------------------------------


def test_openapi_declares_persona_count_as_nullable_integer_with_bounds():
    from app.main import app

    schema = app.openapi()
    persona_count_schema = schema["components"]["schemas"]["SimulationRunResponse"]["properties"][
        "persona_count"
    ]

    # Pydantic v2, `int | None` + `Field(default=None, ge=.., le=..)` icin
    # anyOf: [{type: integer, min/maximum}, {type: null}] uretir.
    variants = persona_count_schema.get("anyOf", [persona_count_schema])
    integer_variant = next(v for v in variants if v.get("type") == "integer")
    null_variant = next(v for v in variants if v.get("type") == "null")

    assert integer_variant["minimum"] == MIN_PERSONA_COUNT
    assert integer_variant["maximum"] == MAX_PERSONA_COUNT
    assert null_variant is not None
