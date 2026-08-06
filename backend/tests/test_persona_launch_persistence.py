"""Adim 2: `app.services.test_wizard.launch_draft`in, sihirbaz baslatma
transaction'i icinde kalici `Persona` satirlarini nasil kaydettigini
dogrulayan uctan uca (API katmani) testler.

Bu dosya `tests/test_personas.py`deki servis-katmani (`build_representative_
personas`) testlerini TEKRARLAMAZ - yalnizca launch_draft entegrasyonunu
(SimulationRun-Persona kayit sirasi, A/B paylasimi, Chip/entitlement
etkilesimi, rollback, gecersiz projeksiyon) kapsar.
"""

import uuid

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.personas import Persona
from app.models.simulations import SimulationRun
from app.models.tests import TestDefinition
from app.services import entitlements, personas
from app.services import test_wizard as wizard_service
from app.services.pricing import FEATURE_BASIC_UX_TEST
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


# --- Yardimcilar (tests/test_personas.py / tests/test_projects_wizard.py ile ayni desen) --


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


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "Persona persistence testi"},
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
        "name": f"Persona persistence testi {uuid.uuid4().hex[:6]}",
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


async def _credit_chips(organization_id: str, amount: int) -> None:
    from app.models.tenancy import Organization
    from app.services import chip_ledger

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            org = await session.get(Organization, uuid.UUID(organization_id))
            assert org is not None
            await chip_ledger.credit(session, org.id, amount, "test setup credit")
            await session.commit()
    finally:
        await engine.dispose()


async def _get_chip_balance(organization_id: str) -> int:
    from app.services import chip_ledger

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await chip_ledger.get_chip_balance(session, uuid.UUID(organization_id))
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


async def _count_launch_artifacts(organization_id: str) -> tuple[int, int, int]:
    """(TestDefinition, SimulationRun, Persona) satir sayisi - rollback dogrulamasi icin."""

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            org_id = uuid.UUID(organization_id)
            definition_count = (
                await session.execute(
                    select(func.count())
                    .select_from(TestDefinition)
                    .where(TestDefinition.organization_id == org_id)
                )
            ).scalar_one()
            run_count = (
                await session.execute(
                    select(func.count())
                    .select_from(SimulationRun)
                    .where(SimulationRun.organization_id == org_id)
                )
            ).scalar_one()
            persona_count = (
                await session.execute(
                    select(func.count())
                    .select_from(Persona)
                    .join(SimulationRun, Persona.simulation_run_id == SimulationRun.id)
                    .where(SimulationRun.organization_id == org_id)
                )
            ).scalar_one()
            return definition_count, run_count, persona_count
    finally:
        await engine.dispose()


async def _entitlement_status(organization_id: str) -> str:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            entitlement = await entitlements.get_or_create_entitlement(
                session, uuid.UUID(organization_id), FEATURE_BASIC_UX_TEST
            )
            await session.commit()
            return entitlement.status.value
    finally:
        await engine.dispose()


# --- A. Tekil (tek varyantli) launch --------------------------------------


def test_single_variant_launch_persists_personas_matching_snapshot(client: TestClient):
    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    body = launch_response.json()
    assert body["used_free_entitlement"] is True
    assert body["reserved_chips"] == 0
    # Regresyon: launch response sozlesmesi 'personas' alani icermez.
    assert "personas" not in body

    run_id = body["simulation_run_ids"][0]
    run = anyio.run(_load_simulation_run, run_id)
    persona_sample = run.input_snapshot["persona_sample"]

    persona_rows = anyio.run(_load_personas, run_id)

    assert 1 <= len(persona_rows) <= personas.MAX_REPRESENTATIVE_PERSONAS
    assert all(p.simulation_run_id == uuid.UUID(run_id) for p in persona_rows)

    indices = sorted(p.index for p in persona_rows)
    assert indices == list(range(len(persona_rows)))

    assert sum(p.population_weight for p in persona_rows) == run.input_snapshot["persona_count"] == 1000
    assert all(p.population_weight > 0 for p in persona_rows)

    # general_web_users preset uretir 5*3*3=45 segment (<=100) -> merge YOK;
    # her persona birebir bir segmente karsilik gelmeli.
    assert len(persona_sample["segments"]) == len(persona_rows)
    expected = sorted(
        (tuple(sorted(s["dimension_values"].items())), s["count"]) for s in persona_sample["segments"]
    )
    actual = sorted((tuple(sorted(p.attributes.items())), p.population_weight) for p in persona_rows)
    assert expected == actual

    # `session_info` yalnizca organization_id'yi kanitlamak icin kullanildi.
    assert session_info["organization_id"]


def test_single_variant_launch_without_persona_selection_creates_no_personas(client: TestClient):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], persona_count=300)
    del payload["persona_preset_id"]
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_id = launch_response.json()["simulation_run_ids"][0]

    run = anyio.run(_load_simulation_run, run_id)
    assert "persona_sample" not in run.input_snapshot

    persona_rows = anyio.run(_load_personas, run_id)
    assert persona_rows == []


# --- B. A/B (coklu varyant) launch -----------------------------------------


def test_ab_launch_each_run_has_own_persona_rows_with_shared_snapshot_content(client: TestClient):
    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    persona_count = 1500
    payload = _ab_payload(project["id"], persona_count=persona_count)
    _patch_draft(client, draft["id"], payload)

    anyio.run(_credit_chips, session_info["organization_id"], 3000)
    balance_before = anyio.run(_get_chip_balance, session_info["organization_id"])
    assert balance_before == 3000

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    body = launch_response.json()
    run_ids = body["simulation_run_ids"]
    assert len(run_ids) == 2

    # persona_count (1500) > FREE_BASIC_UX_TEST_PERSONA_LIMIT (1000) -> ucretli;
    # tek bir rezervasyon (1500 Chip) - VARYANT BASINA degil.
    assert body["used_free_entitlement"] is False
    assert body["reserved_chips"] == persona_count

    balance_after = anyio.run(_get_chip_balance, session_info["organization_id"])
    assert balance_after == 3000 - persona_count

    run_a = anyio.run(_load_simulation_run, run_ids[0])
    run_b = anyio.run(_load_simulation_run, run_ids[1])

    # Ayni launch_run_id grubuna ait (chip/entitlement muhasebesi paylasilir).
    assert run_a.launch_run_id == run_b.launch_run_id
    assert run_a.chip_reservation_id == run_b.chip_reservation_id
    assert run_a.chip_reservation_id is not None

    persona_rows_a = anyio.run(_load_personas, run_ids[0])
    persona_rows_b = anyio.run(_load_personas, run_ids[1])

    assert persona_rows_a
    assert persona_rows_b
    assert sum(p.population_weight for p in persona_rows_a) == persona_count
    assert sum(p.population_weight for p in persona_rows_b) == persona_count

    # Farkli run'lar ASLA ayni Persona satirini paylasmaz (farkli PK/simulation_run_id).
    ids_a = {p.id for p in persona_rows_a}
    ids_b = {p.id for p in persona_rows_b}
    assert ids_a.isdisjoint(ids_b)
    assert {p.simulation_run_id for p in persona_rows_a} == {uuid.UUID(run_ids[0])}
    assert {p.simulation_run_id for p in persona_rows_b} == {uuid.UUID(run_ids[1])}

    # Ayni preset/persona_count kullanildigi icin (largest-remainder tamamen
    # aritmetik oldugundan, bkz. app.services.personas docstring'i) her iki
    # varyantin cohort/persona icerigi (index/label/attributes/agirlik) AYNI
    # olmali - yalnizca simulation_run_id (ve PK) farklidir.
    def _content(rows: list[Persona]) -> list[tuple]:
        return [(p.index, p.label, tuple(sorted(p.attributes.items())), p.population_weight) for p in rows]

    assert _content(persona_rows_a) == _content(persona_rows_b)


# --- C. Ucretsiz entitlement akisi ------------------------------------------


def test_free_entitlement_launch_persists_personas_and_consumes_entitlement_only(client: TestClient):
    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    balance_before = anyio.run(_get_chip_balance, session_info["organization_id"])
    assert balance_before == 0

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    body = launch_response.json()
    assert body["used_free_entitlement"] is True
    assert body["reserved_chips"] == 0

    run_id = body["simulation_run_ids"][0]
    persona_rows = anyio.run(_load_personas, run_id)
    assert persona_rows

    # Ucretsiz hak kullanildigi icin Chip bakiyesi HIC degismemeli.
    balance_after = anyio.run(_get_chip_balance, session_info["organization_id"])
    assert balance_after == 0

    status = anyio.run(_entitlement_status, session_info["organization_id"])
    assert status == "reserved"


# --- D. Ucretli Chip akisi ---------------------------------------------------


@pytest.mark.security
def test_paid_chip_launch_cost_is_unaffected_by_persona_row_count(client: TestClient):
    """Chip maliyeti yalnizca quote'a (persona_count * birim fiyat + modul
    maliyetleri) baglidir - kalici Persona SATIR SAYISI (en fazla 100) bu
    hesaba HICBIR sekilde ek maliyet olarak yansimamalidir."""

    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    persona_count = 500
    payload = _basic_payload(project["id"], persona_count=persona_count)
    payload["modules"] = ["network_device_test", "campaign_cta_test", "synthetic_attention_estimate"]
    _patch_draft(client, draft["id"], payload)

    anyio.run(_credit_chips, session_info["organization_id"], 500)
    balance_before = anyio.run(_get_chip_balance, session_info["organization_id"])
    assert balance_before == 500

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    body = launch_response.json()

    # Temel test 500<=1000 icin ucretsizdir; yalnizca modul maliyeti rezerve edilir.
    assert body["used_free_entitlement"] is True
    reserved_chips = body["reserved_chips"]
    assert reserved_chips > 0

    balance_after = anyio.run(_get_chip_balance, session_info["organization_id"])
    assert balance_before - balance_after == reserved_chips

    run_id = body["simulation_run_ids"][0]
    persona_rows = anyio.run(_load_personas, run_id)
    assert 1 <= len(persona_rows) <= personas.MAX_REPRESENTATIVE_PERSONAS
    assert sum(p.population_weight for p in persona_rows) == persona_count

    run = anyio.run(_load_simulation_run, run_id)
    assert run.chip_reservation_id is not None
    assert run.free_entitlement_feature_key is not None


# --- E. Rollback -------------------------------------------------------------


def test_launch_failure_after_persona_persistence_rolls_back_everything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """A/B'nin ilk varyanti (Persona satirlari dahil) basariyla flush edildikten
    SONRA, ikinci varyantin PageAnalysis olusturma adiminda kontrollu bir hata
    enjekte edilir. `launch_draft` bu noktadan sonra hicbir sekilde
    `session.commit()`e ulasamaz (bkz. app.routers.test_wizard.launch_draft /
    app.db.get_session) - bu yuzden ilk varyantin (zaten flush edilmis, henuz
    commit edilmemis) SimulationRun + Persona satirlari da TAMAMEN geri
    alinmalidir; yetim satir kalmamalidir."""

    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _ab_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    original_create_analysis = wizard_service.page_analysis_service.create_analysis
    call_count = {"n": 0}

    async def _flaky_create_analysis(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("Adim 2 rollback testi: kontrollu enjekte edilen hata")
        return await original_create_analysis(*args, **kwargs)

    monkeypatch.setattr(wizard_service.page_analysis_service, "create_analysis", _flaky_create_analysis)

    entitlement_before = anyio.run(_entitlement_status, session_info["organization_id"])
    assert entitlement_before == "available"

    # TestClient varsayilan olarak sunucu tarafinda yakalanmamis istisnalari
    # test'e YENIDEN FIRLATIR (raise_server_exceptions=True) - bu RuntimeError
    # da bir HTTPException/DraftValidationError OLMADIGI icin router tarafindan
    # yakalanmaz, dogrudan burada yukselir.
    with pytest.raises(RuntimeError, match="Adim 2 rollback testi"):
        client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))

    assert call_count["n"] == 2

    definition_count, run_count, persona_count = anyio.run(
        _count_launch_artifacts, session_info["organization_id"]
    )
    assert (definition_count, run_count, persona_count) == (0, 0, 0)

    # Ucretsiz hak rezervasyonu da (temel test <=1000 persona icin ucretsizdir)
    # geri alinmis olmali - hala 'available', 'reserved' DEGIL.
    entitlement_after = anyio.run(_entitlement_status, session_info["organization_id"])
    assert entitlement_after == "available"


# --- F. Gecersiz projeksiyon --------------------------------------------------


def test_launch_rejects_when_projection_weight_sum_mismatches_persona_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    def _broken_projection(cohort_sample):
        # Kasitli olarak bozuk: toplam agirlik persona_count (1000) ile eslesmiyor.
        return (personas.PersonaProjection(index=0, label="bozuk", attributes={}, population_weight=1),)

    monkeypatch.setattr(wizard_service.personas, "build_representative_personas", _broken_projection)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 400, launch_response.text
    assert (
        "persona_count" in launch_response.json()["detail"]
        or "eslesmiyor" in launch_response.json()["detail"]
    )

    definition_count, run_count, persona_count = anyio.run(
        _count_launch_artifacts, session_info["organization_id"]
    )
    assert (definition_count, run_count, persona_count) == (0, 0, 0)


# --- Adim 2.5: A/B'de ortak persona_sample_seed (tie-break garantisi) -------


def _tie_break_distribution() -> dict:
    """4 esit-agirlikli (%25) kombinasyon ureten bir dagilim: `persona_count=101`
    ile her segmentin ham payi (101 * 0.25 = 25.25) BIREBIR AYNI kesirli
    kalana sahip olur -> largest-remainder'in +1 dagitacagi TEK fazlalik icin
    4 segment arasinda KESIN bir esitlik (tie) olusur (bkz. app.services.
    personas._allocate_counts). Bu, `general_web_users` gibi yuvarlak
    preset'lerin ASLA tetiklemedigi tie-break yolunu GERCEKTEN egzersiz eder."""

    return {
        personas.AGE_RANGE: [
            {"key": "genc", "label": "Genc", "weight": 50, "min_age": 18, "max_age": 30},
            {"key": "yetiskin", "label": "Yetiskin", "weight": 50, "min_age": 31, "max_age": 60},
        ],
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 50},
            {"key": "desktop", "label": "Masaustu", "weight": 50},
        ],
    }


def test_ab_tie_break_distribution_produces_identical_cohort_and_personas_across_variants(
    client: TestClient,
):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    persona_count = 101
    payload = _ab_payload(project["id"], persona_count=persona_count)
    del payload["persona_preset_id"]
    payload["persona_distribution"] = _tie_break_distribution()
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_ids = launch_response.json()["simulation_run_ids"]
    assert len(run_ids) == 2

    run_a = anyio.run(_load_simulation_run, run_ids[0])
    run_b = anyio.run(_load_simulation_run, run_ids[1])

    # Tie-break'in GERCEKTEN tetiklendigini dogrula (sanity check): 4 segment,
    # biri 26, digerleri 25 - largest-remainder'in "hangi segment +1 alir"
    # kararinin tie-break'e (seed'e) bagli oldugu asil senaryo budur.
    segment_counts = sorted(s["count"] for s in run_a.input_snapshot["persona_sample"]["segments"])
    assert segment_counts == [25, 25, 25, 26]

    # A. Ortak persona_sample_seed + birebir ayni cohort snapshot.
    assert run_a.input_snapshot["persona_sample_seed"] == run_b.input_snapshot["persona_sample_seed"]
    assert run_a.input_snapshot["persona_sample"] == run_b.input_snapshot["persona_sample"]

    # SimulationRun.deterministic_seed motor determinizmi icin HALA varyant
    # basina farklidir - bu Adim 2.5 kapsaminda DEGISTIRILMEDI.
    assert run_a.deterministic_seed != run_b.deterministic_seed

    persona_rows_a = anyio.run(_load_personas, run_ids[0])
    persona_rows_b = anyio.run(_load_personas, run_ids[1])

    assert sum(p.population_weight for p in persona_rows_a) == persona_count
    assert sum(p.population_weight for p in persona_rows_b) == persona_count

    def _content(rows: list[Persona]) -> list[tuple]:
        return [(p.index, p.label, tuple(sorted(p.attributes.items())), p.population_weight) for p in rows]

    assert _content(persona_rows_a) == _content(persona_rows_b)

    ids_a = {p.id for p in persona_rows_a}
    ids_b = {p.id for p in persona_rows_b}
    assert ids_a.isdisjoint(ids_b)
    assert {p.simulation_run_id for p in persona_rows_a} == {run_a.id}
    assert {p.simulation_run_id for p in persona_rows_b} == {run_b.id}


def test_ab_launch_samples_cohort_exactly_once(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """`personas.sample_cohorts` (asil ornekleme/rastgelelik cagrisi), coklu
    varyantli (A/B) bir launch'ta TAM OLARAK BIR KEZ calisir - varyant basina
    tekrar TEKRAR sample edilmez (bkz. `launch_draft`: cohort ornekleme artik
    varyant dongusunun DISINDA)."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _ab_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    original_sample_cohorts = wizard_service.personas.sample_cohorts
    calls: list[tuple] = []

    def _spy_sample_cohorts(*args, **kwargs):
        calls.append((args, kwargs))
        return original_sample_cohorts(*args, **kwargs)

    monkeypatch.setattr(wizard_service.personas, "sample_cohorts", _spy_sample_cohorts)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    assert len(launch_response.json()["simulation_run_ids"]) == 2

    assert len(calls) == 1


# --- Adim 2.5: input_snapshot hash, persona_sample_seed'e duyarli ----------


def test_input_snapshot_hash_is_sensitive_to_persona_sample_seed():
    """`app.engine.baseline._hash_input_snapshot`, TUM `input_snapshot`
    sozlugunu (sort_keys=True ile) hashler - bu yuzden yeni eklenen
    `persona_sample_seed` alani ayrica kod degisikligi GEREKMEDEN otomatik
    olarak hash'e dahil olur; bu test bunu acikca dogrular."""

    from app.engine.baseline import _hash_input_snapshot

    base_snapshot = {"persona_sample_seed": 111, "persona_count": 500, "url": "https://example.com"}
    changed_seed_snapshot = {**base_snapshot, "persona_sample_seed": 222}

    hash_a = _hash_input_snapshot(base_snapshot, deterministic_seed=1, rules_version="v1")
    hash_b = _hash_input_snapshot(changed_seed_snapshot, deterministic_seed=1, rules_version="v1")
    assert hash_a != hash_b

    # Ayni snapshot + ayni seed + ayni rules_version -> ayni hash (determinizm).
    hash_a_again = _hash_input_snapshot(base_snapshot, deterministic_seed=1, rules_version="v1")
    assert hash_a == hash_a_again

    # Eski (Adim 2.5 ONCESI, persona_sample_seed'siz) snapshot fixture'lari
    # hala gecerli/hashlenebilir kalir - geriye donuk uyumluluk.
    legacy_snapshot = {"persona_count": 500, "url": "https://example.com"}
    legacy_hash = _hash_input_snapshot(legacy_snapshot, deterministic_seed=1, rules_version="v1")
    assert legacy_hash != hash_a
    legacy_hash_again = _hash_input_snapshot(legacy_snapshot, deterministic_seed=1, rules_version="v1")
    assert legacy_hash == legacy_hash_again


# --- Adim 2.5: retry mevcut cohort/persona satirlarini korur ----------------


async def _mark_run_failed(run_id: str) -> None:
    from app.models.simulations import SimulationStatus

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(SimulationRun, uuid.UUID(run_id))
            assert run is not None
            run.status = SimulationStatus.FAILED
            run.error = "Adim 2.5 retry testi: kasitli basarisizlik"
            await session.commit()
    finally:
        await engine.dispose()


async def _retry_run(organization_id: str, run_id: str) -> None:
    from app.services import simulation_worker

    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await simulation_worker.retry_run(session, uuid.UUID(organization_id), uuid.UUID(run_id))
            await session.commit()
    finally:
        await engine.dispose()


def test_retry_reuses_existing_cohort_snapshot_and_persona_rows(client: TestClient):
    """`simulation_worker.retry_run`, AYNI (id degismeyen) `SimulationRun`
    satirini yeniden kuyruga alir - `input_snapshot` (persona_sample_seed +
    persona_sample dahil) DEGISTIRILMEZ, yeni bir cohort sample/Persona
    satiri URETILMEZ (bkz. app.services.simulation_worker.retry_run: hicbir
    yerde `_resolve_persona_sample`/`build_representative_personas`
    cagrilmaz)."""

    session_info = _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = _basic_payload(project["id"], persona_count=1000)
    _patch_draft(client, draft["id"], payload)

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_id = launch_response.json()["simulation_run_ids"][0]

    run_before = anyio.run(_load_simulation_run, run_id)
    persona_rows_before = anyio.run(_load_personas, run_id)
    assert persona_rows_before

    anyio.run(_mark_run_failed, run_id)
    anyio.run(_retry_run, session_info["organization_id"], run_id)

    run_after = anyio.run(_load_simulation_run, run_id)
    persona_rows_after = anyio.run(_load_personas, run_id)

    # Retry, YENI bir SimulationRun OLUSTURMAZ - ayni satir yeniden kuyruga alinir.
    assert run_after.id == run_before.id
    assert run_after.status.value == "queued"

    # input_snapshot (persona_sample_seed + persona_sample dahil) BIREBIR AYNI.
    assert run_after.input_snapshot == run_before.input_snapshot

    # Persona satirlari (PK dahil) BIREBIR AYNI - yeni satir uretilmedi/silinmedi.
    assert [p.id for p in persona_rows_after] == [p.id for p in persona_rows_before]
    assert [(p.index, p.attributes, p.population_weight) for p in persona_rows_after] == [
        (p.index, p.attributes, p.population_weight) for p in persona_rows_before
    ]

    # Retry, ucretsiz hakki YENIDEN rezerve eder (bkz. simulation_worker.retry_run) -
    # bu Adim 2.5'in kapsami disinda, mevcut (degismeyen) retry davranisidir.
    entitlement_after = anyio.run(_entitlement_status, session_info["organization_id"])
    assert entitlement_after == "reserved"
