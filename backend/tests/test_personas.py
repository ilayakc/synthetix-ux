"""Persona tanimi, dagilimi ve deterministik ornekleme sistemi icin testler.

Iki katmanda test edilir:

- Servis katmani (`app.services.personas`, `app.services.persona_presets`):
  saf dogrulama/ornekleme mantigi ve DB'ye bagli preset CRUD kurallari.
- API katmani (`app.routers.personas`, `app.services.test_wizard`
  entegrasyonu): tenant izolasyonu ve sihirbaz baslatmasinin persona
  ornekleme snapshot'ini kalici olarak nasil kaydettigi.

Bu asamada personalarin *davranisi* (simulasyon sonucu) test edilmez; bkz.
Prompt 7. Buradaki testler yalnizca *kimin* test edilecegini (dagilim,
segment/cohort ornekleme) dogrular.
"""

import time
import uuid

import anyio
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.simulations import SimulationRun
from app.models.tenancy import Organization
from app.models.tests import PersonaPresetStatus
from app.services import persona_presets, personas
from tests.conftest import TEST_DATABASE_URL

# `test_engine`, `session`, `organization` ve `client` fixture'lari artik
# tests/conftest.py'de paylasilan altyapidan gelir (izole test veritabani +
# her testte rollback/truncate).

pytestmark = pytest.mark.integration


def _simple_distribution() -> dict:
    return {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 60},
            {"key": "desktop", "label": "Masaüstü", "weight": 40},
        ],
    }


# --- Dagilim dogrulama: yuzdelerin toplami, negatif/bos agirlik -----------


def test_distribution_weights_must_sum_to_100():
    distribution = {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 60},
            {"key": "desktop", "label": "Masaüstü", "weight": 30},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="100"):
        personas.validate_distribution(distribution)


def test_distribution_weights_summing_to_100_is_accepted():
    personas.validate_distribution(_simple_distribution())  # raise etmemeli


def test_negative_weight_is_rejected():
    distribution = {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 120},
            {"key": "desktop", "label": "Masaüstü", "weight": -20},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="negatif"):
        personas.validate_distribution(distribution)


def test_zero_weight_is_rejected():
    distribution = {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 100},
            {"key": "desktop", "label": "Masaüstü", "weight": 0},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="negatif"):
        personas.validate_distribution(distribution)


def test_missing_weight_is_rejected():
    distribution = {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil"},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="sayisal"):
        personas.validate_distribution(distribution)


def test_empty_bucket_list_is_rejected():
    with pytest.raises(personas.PersonaValidationError, match="bos olmayan"):
        personas.validate_distribution({personas.DEVICE_CLASS: []})


def test_unknown_dimension_is_rejected():
    with pytest.raises(personas.PersonaValidationError, match="Bilinmeyen boyut"):
        personas.validate_distribution({"favori_renk": [{"key": "mavi", "label": "Mavi", "weight": 100}]})


def test_duplicate_bucket_key_is_rejected():
    distribution = {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 50},
            {"key": "mobile", "label": "Mobil 2", "weight": 50},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="yinelenen"):
        personas.validate_distribution(distribution)


# --- Yas araligi cakismasi --------------------------------------------------


def test_overlapping_age_ranges_are_rejected():
    distribution = {
        personas.AGE_RANGE: [
            {"key": "a", "label": "18-30", "weight": 50, "min_age": 18, "max_age": 30},
            {"key": "b", "label": "25-40", "weight": 50, "min_age": 25, "max_age": 40},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="cakisiyor"):
        personas.validate_distribution(distribution)


def test_non_overlapping_age_ranges_are_accepted():
    distribution = {
        personas.AGE_RANGE: [
            {"key": "a", "label": "18-30", "weight": 50, "min_age": 18, "max_age": 30},
            {"key": "b", "label": "31-40", "weight": 50, "min_age": 31, "max_age": 40},
        ]
    }
    personas.validate_distribution(distribution)  # raise etmemeli


def test_adjacent_boundary_age_ranges_are_rejected_as_overlap():
    # max_age=30 ve min_age=30 ayni yasi (30) iki kovaya birden dahil eder.
    distribution = {
        personas.AGE_RANGE: [
            {"key": "a", "label": "18-30", "weight": 50, "min_age": 18, "max_age": 30},
            {"key": "b", "label": "30-40", "weight": 50, "min_age": 30, "max_age": 40},
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="cakisiyor"):
        personas.validate_distribution(distribution)


# --- Stereotip / ayrimcilik yasagi ------------------------------------------


@pytest.mark.security
@pytest.mark.parametrize(
    "banned_key",
    ["income", "gelir", "intelligence", "zeka", "credit_score", "purchase_likelihood"],
)
def test_banned_inference_keys_are_rejected(banned_key: str):
    distribution = {
        personas.AGE_RANGE: [
            {
                "key": "a",
                "label": "18-30",
                "weight": 100,
                "min_age": 18,
                "max_age": 30,
                banned_key: "high",
            },
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="stereotip"):
        personas.validate_distribution(distribution)


@pytest.mark.security
def test_banned_inference_key_nested_deep_is_rejected():
    distribution = {
        personas.REGION: [
            {
                "key": "tr",
                "label": "Türkiye",
                "weight": 100,
                "scenario_interest": {
                    "estimate": "high",
                    "confidence": "low",
                    "assumption": "Senaryo bazli bir varsayimdir.",
                    "extra": {"predicted_behavior": "buys_more"},
                },
            }
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="stereotip"):
        personas.validate_distribution(distribution)


# --- Bolgesel "senaryo bazli tahmini ilgi" ----------------------------------


def test_regional_scenario_interest_requires_assumption():
    distribution = {
        personas.REGION: [
            {
                "key": "tr",
                "label": "Türkiye",
                "weight": 100,
                "scenario_interest": {"estimate": "high", "confidence": "low"},
            }
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="assumption"):
        personas.validate_distribution(distribution)


@pytest.mark.security
def test_regional_scenario_interest_rejects_market_demand_claim():
    distribution = {
        personas.REGION: [
            {
                "key": "tr",
                "label": "Türkiye",
                "weight": 100,
                "scenario_interest": {
                    "estimate": "high",
                    "confidence": "medium",
                    "assumption": "Bu bolgede gercek pazar talebi yuksektir.",
                },
            }
        ]
    }
    with pytest.raises(personas.PersonaValidationError, match="iddia"):
        personas.validate_distribution(distribution)


@pytest.mark.security
def test_regional_scenario_interest_server_always_stamps_disclaimer():
    bucket = {
        "key": "tr",
        "label": "Türkiye",
        "weight": 100,
        "scenario_interest": {
            "estimate": "medium",
            "confidence": "low",
            "assumption": "Senaryoya dayali bir varsayimdir; dogrulanmamistir.",
            "disclaimer": "musteri gercek talebi kanitlar",  # spoof denemesi
        },
    }
    distribution = {personas.REGION: [bucket]}
    personas.validate_distribution(distribution)

    # Sunucu, cagiranin gonderdigi 'disclaimer' degerini yok sayip kendi
    # sabit uyarisini yazmis olmalidir.
    assert bucket["scenario_interest"]["disclaimer"] == personas.REGIONAL_INTEREST_DISCLAIMER


def test_builtin_presets_all_pass_validation():
    for preset in personas.list_builtin_presets():
        personas.validate_distribution(preset.distribution)


# --- Deterministik cohort/segment ornekleme ---------------------------------


def test_sample_cohorts_is_deterministic_for_same_snapshot_and_seed():
    distribution = _simple_distribution()
    first = personas.sample_cohorts(distribution, 10_000, deterministic_seed=42)
    second = personas.sample_cohorts(distribution, 10_000, deterministic_seed=42)

    assert first.sample_hash == second.sample_hash
    assert [(s.key, s.count) for s in first.segments] == [(s.key, s.count) for s in second.segments]


def test_sample_cohorts_different_seed_may_change_hash_but_not_total():
    distribution = {
        personas.DEVICE_CLASS: [
            {"key": "mobile", "label": "Mobil", "weight": 33},
            {"key": "desktop", "label": "Masaüstü", "weight": 33},
            {"key": "tablet", "label": "Tablet", "weight": 34},
        ]
    }
    seed_a = personas.sample_cohorts(distribution, 101, deterministic_seed=1)
    seed_b = personas.sample_cohorts(distribution, 101, deterministic_seed=2)

    assert sum(s.count for s in seed_a.segments) == 101
    assert sum(s.count for s in seed_b.segments) == 101


@pytest.mark.parametrize("persona_count", [100, 1000, 12_345, 50_000])
def test_sample_cohorts_counts_always_sum_to_persona_count(persona_count: int):
    result = personas.sample_cohorts(_simple_distribution(), persona_count, deterministic_seed=7)
    assert sum(s.count for s in result.segments) == persona_count


def test_sample_cohorts_without_any_dimension_returns_single_segment():
    result = personas.sample_cohorts({}, 500, deterministic_seed=1)
    assert len(result.segments) == 1
    assert result.segments[0].count == 500


def test_sample_cohorts_below_minimum_persona_count_is_rejected():
    with pytest.raises(personas.PersonaValidationError):
        personas.sample_cohorts(_simple_distribution(), 10, deterministic_seed=1)


def test_sample_cohorts_too_many_segments_is_rejected():
    huge_distribution = {
        personas.AGE_RANGE: [
            {"key": f"g{i}", "label": f"g{i}", "weight": 100 / 25, "min_age": i, "max_age": i}
            for i in range(0, 25)
        ],
        personas.REGION: [{"key": f"r{i}", "label": f"r{i}", "weight": 100 / 25} for i in range(25)],
        personas.DEVICE_CLASS: [{"key": f"d{i}", "label": f"d{i}", "weight": 100 / 10} for i in range(10)],
    }
    # 25 * 25 * 10 = 6250 > MAX_SEGMENTS -- yas araliklari tek nokta oldugundan cakismaz.
    with pytest.raises(personas.PersonaValidationError, match="segment"):
        personas.sample_cohorts(huge_distribution, 50_000, deterministic_seed=1)


def test_sample_cohorts_snapshot_is_immutable_against_later_mutation_of_input():
    distribution = _simple_distribution()
    result = personas.sample_cohorts(distribution, 1000, deterministic_seed=5)
    original_hash = result.sample_hash
    original_snapshot = dict(result.distribution_snapshot["distribution"])

    # Girdi sonradan mutasyona ugratilir; onceden uretilmis sonucun
    # snapshot'i (ve hash'i) etkilenmemelidir.
    distribution[personas.DEVICE_CLASS][0]["weight"] = 999

    assert result.sample_hash == original_hash
    assert result.distribution_snapshot["distribution"] == original_snapshot
    assert result.distribution_snapshot["distribution"][personas.DEVICE_CLASS][0]["weight"] == 60


# --- 50.000 olcek smoke testi ------------------------------------------------


def test_sample_cohorts_50000_scale_smoke():
    """En genis hazir preset ile 50.000 personalik bir ornekleme makul surede tamamlanmali."""

    distribution = personas.BUILTIN_PRESETS["accessibility_focused_seniors"].distribution

    started = time.monotonic()
    result = personas.sample_cohorts(distribution, personas.MAX_PERSONA_COUNT, deterministic_seed=99)
    elapsed = time.monotonic() - started

    assert result.total_count == personas.MAX_PERSONA_COUNT
    assert sum(s.count for s in result.segments) == personas.MAX_PERSONA_COUNT
    assert len(result.segments) <= personas.MAX_SEGMENTS
    # Cohort/segment yaklasimi persona basina degil, segment basina calisir;
    # bu yuzden 50.000 personalik bir ornekleme de saniyenin cok altinda
    # tamamlanmalidir (kalici satir materyalizasyonu yoktur).
    assert elapsed < 2.0


def test_iter_segment_batches_covers_all_segments_without_duplication():
    distribution = personas.BUILTIN_PRESETS["general_web_users"].distribution
    result = personas.sample_cohorts(distribution, 50_000, deterministic_seed=3)

    batched_keys = [
        segment.key for batch in personas.iter_segment_batches(result, batch_size=3) for segment in batch
    ]
    assert sorted(batched_keys) == sorted(s.key for s in result.segments)


# --- Sirkete ozel preset CRUD: olusturma / kopyalama / arsivleme -----------


async def test_create_preset_persists_valid_distribution(session: AsyncSession, organization: Organization):
    preset = await persona_presets.create_preset(
        session,
        organization.id,
        name="Ozel dagilim",
        description="test",
        distribution=_simple_distribution(),
    )
    assert preset.status == PersonaPresetStatus.ACTIVE
    assert preset.config == _simple_distribution()


async def test_create_preset_rejects_invalid_distribution(session: AsyncSession, organization: Organization):
    with pytest.raises(personas.PersonaValidationError):
        await persona_presets.create_preset(
            session,
            organization.id,
            name="Gecersiz",
            description=None,
            distribution={personas.DEVICE_CLASS: [{"key": "a", "label": "A", "weight": 40}]},
        )


async def test_copy_builtin_preset_creates_editable_org_copy(
    session: AsyncSession, organization: Organization
):
    builtin_id = personas.builtin_preset_id("general_web_users")
    copy = await persona_presets.copy_preset(
        session,
        organization.id,
        source_preset_id=builtin_id,
        new_name="Bizim genel kullanicilar",
    )

    assert copy.source_builtin_key == "general_web_users"
    assert copy.config == personas.BUILTIN_PRESETS["general_web_users"].distribution
    assert copy.organization_id == organization.id

    # Kopya artik normal CRUD'a tabidir: guncellenebilir.
    updated = await persona_presets.update_preset(session, copy, name="Yeni isim")
    assert updated.name == "Yeni isim"


async def test_archive_preset_is_idempotent(session: AsyncSession, organization: Organization):
    preset = await persona_presets.create_preset(
        session,
        organization.id,
        name="Arsivlenecek",
        description=None,
        distribution=_simple_distribution(),
    )

    first = await persona_presets.archive_preset(session, preset)
    assert first.status == PersonaPresetStatus.ARCHIVED
    assert first.archived_at is not None

    second = await persona_presets.archive_preset(session, preset)
    assert second.archived_at == first.archived_at


async def test_archived_preset_cannot_be_updated(session: AsyncSession, organization: Organization):
    preset = await persona_presets.create_preset(
        session,
        organization.id,
        name="Arsivlenecek",
        description=None,
        distribution=_simple_distribution(),
    )
    await persona_presets.archive_preset(session, preset)

    with pytest.raises(persona_presets.PersonaPresetArchivedError):
        await persona_presets.update_preset(session, preset, name="yeni isim")


async def test_list_org_presets_excludes_archived_by_default(
    session: AsyncSession, organization: Organization
):
    active = await persona_presets.create_preset(
        session, organization.id, name="Aktif", description=None, distribution=_simple_distribution()
    )
    archived = await persona_presets.create_preset(
        session, organization.id, name="Arsivli", description=None, distribution=_simple_distribution()
    )
    await persona_presets.archive_preset(session, archived)

    default_listing = await persona_presets.list_org_presets(session, organization.id)
    assert [p.id for p in default_listing] == [active.id]

    full_listing = await persona_presets.list_org_presets(session, organization.id, include_archived=True)
    assert {p.id for p in full_listing} == {active.id, archived.id}


@pytest.mark.security
async def test_org_presets_are_isolated_by_tenant(session: AsyncSession, organization: Organization):
    other_org = Organization(name="Other Persona Org", slug=f"other-persona-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    preset = await persona_presets.create_preset(
        session, organization.id, name="Org A preset", description=None, distribution=_simple_distribution()
    )

    with pytest.raises(persona_presets.PersonaPresetNotFoundError):
        await persona_presets.get_owned_preset(session, other_org.id, preset.id)

    own = await persona_presets.get_owned_preset(session, organization.id, preset.id)
    assert own.id == preset.id


# --- API katmani: tenant izolasyonu ve sihirbaz entegrasyonu ---------------


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


def test_api_lists_builtin_presets_without_any_org_presets(client: TestClient):
    _register(client)
    response = client.get("/api/personas/presets")
    assert response.status_code == 200
    body = response.json()
    builtin_keys = {p["id"] for p in body if p["is_builtin"]}
    assert personas.builtin_preset_id("general_web_users") in builtin_keys


@pytest.mark.security
def test_api_persona_preset_tenant_isolation(client: TestClient):
    _register(client)
    create_response = client.post(
        "/api/personas/presets",
        json={"name": f"Org A preset {uuid.uuid4().hex[:6]}", "distribution": _simple_distribution()},
        headers=_csrf_headers(client),
    )
    assert create_response.status_code == 201, create_response.text
    preset_id = create_response.json()["id"]
    snapshot_a = _snapshot_cookies(client)

    _register(client)  # organizasyon B'ye gecer

    get_response = client.get(f"/api/personas/presets/{preset_id}")
    assert get_response.status_code == 404

    patch_response = client.patch(
        f"/api/personas/presets/{preset_id}",
        json={"name": "baska organizasyon"},
        headers=_csrf_headers(client),
    )
    assert patch_response.status_code == 404

    archive_response = client.post(
        f"/api/personas/presets/{preset_id}/archive", headers=_csrf_headers(client)
    )
    assert archive_response.status_code == 404

    _restore_cookies(client, snapshot_a)
    own_response = client.get(f"/api/personas/presets/{preset_id}")
    assert own_response.status_code == 200


def test_api_create_preset_rejects_bad_distribution(client: TestClient):
    _register(client)
    response = client.post(
        "/api/personas/presets",
        json={
            "name": f"Gecersiz {uuid.uuid4().hex[:6]}",
            "distribution": {personas.DEVICE_CLASS: [{"key": "a", "label": "A", "weight": -5}]},
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_api_copy_builtin_preset_then_archive(client: TestClient):
    _register(client)
    builtin_id = personas.builtin_preset_id("mobile_first_gen_z")

    copy_response = client.post(
        f"/api/personas/presets/{builtin_id}/copy",
        json={"name": f"Kopya {uuid.uuid4().hex[:6]}"},
        headers=_csrf_headers(client),
    )
    assert copy_response.status_code == 201, copy_response.text
    copied = copy_response.json()
    assert copied["is_builtin"] is False
    assert copied["source_builtin_key"] == "mobile_first_gen_z"

    archive_response = client.post(
        f"/api/personas/presets/{copied['id']}/archive", headers=_csrf_headers(client)
    )
    assert archive_response.status_code == 200
    assert archive_response.json()["status"] == "archived"


def test_api_sample_preview_is_deterministic_for_same_seed(client: TestClient):
    _register(client)
    body = {
        "persona_count": 5000,
        "preset_id": personas.builtin_preset_id("general_web_users"),
        "seed": 12345,
    }
    first = client.post("/api/personas/sample-preview", json=body)
    second = client.post("/api/personas/sample-preview", json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["sample_hash"] == second.json()["sample_hash"]
    assert first.json()["segments"] == second.json()["segments"]


@pytest.mark.security
def test_api_sample_preview_rejects_stereotype_field(client: TestClient):
    _register(client)
    response = client.post(
        "/api/personas/sample-preview",
        json={
            "persona_count": 500,
            "distribution": {
                personas.AGE_RANGE: [
                    {
                        "key": "a",
                        "label": "18-30",
                        "weight": 100,
                        "min_age": 18,
                        "max_age": 30,
                        "income": "high",
                    }
                ]
            },
        },
    )
    assert response.status_code == 400
    assert "stereotip" in response.json()["detail"]


def test_api_sample_preview_requires_exactly_one_source(client: TestClient):
    _register(client)
    response = client.post(
        "/api/personas/sample-preview",
        json={
            "persona_count": 500,
            "preset_id": personas.builtin_preset_id("general_web_users"),
            "distribution": _simple_distribution(),
        },
    )
    assert response.status_code == 400


# --- Sihirbaz entegrasyonu: baslatma anindaki degismez snapshot -----------


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}", "description": "Persona testi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


async def _load_simulation_run(run_id: str) -> SimulationRun:
    # Izole test veritabanina karsi, tek kullanimlik bir NullPool motoru:
    # `client` fixture'i (bkz. tests/conftest.py) istekleri TEST_DATABASE_URL'e
    # yonlendirir; bu yardimci da ayni veritabanina okumalidir.
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            result = await session.execute(select(SimulationRun).where(SimulationRun.id == uuid.UUID(run_id)))
            run = result.scalar_one()
            return run
    finally:
        await engine.dispose()


def test_wizard_launch_records_immutable_persona_sample_snapshot(client: TestClient):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = {
        "project_id": project["id"],
        "name": f"Persona snapshot testi {uuid.uuid4().hex[:6]}",
        "target_task": "Anasayfadan urun bul",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": 1000,
        "target_audience": "Genel kullanicilar",
        "persona_preset_id": personas.builtin_preset_id("general_web_users"),
        "authorization_confirmed": True,
        "modules": [],
    }
    patch_response = client.patch(
        f"/api/tests/drafts/{draft['id']}", json={"payload": payload}, headers=_csrf_headers(client)
    )
    assert patch_response.status_code == 200, patch_response.text

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_id = launch_response.json()["simulation_run_ids"][0]

    run = anyio.run(_load_simulation_run, run_id)
    persona_sample = run.input_snapshot["persona_sample"]

    assert persona_sample["generator_version"] == personas.GENERATOR_VERSION
    assert sum(s["count"] for s in persona_sample["segments"]) == 1000

    # Ayni dagilim + ayni persona_count + ayni (kalici kaydedilmis)
    # deterministic_seed her zaman ayni ozeti uretmelidir (bkz.
    # app.services.personas.sample_cohorts docstring'i).
    recomputed = personas.sample_cohorts(
        persona_sample["distribution_snapshot"]["distribution"],
        1000,
        run.deterministic_seed,
    )
    assert recomputed.sample_hash == persona_sample["sample_hash"]


def test_wizard_launch_without_persona_selection_still_succeeds(client: TestClient):
    """Geriye donuk uyumluluk: persona preset/dagilim secimi yapilmasa da sihirbaz calismaya devam eder."""

    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    payload = {
        "project_id": project["id"],
        "name": f"Persona secilmeyen test {uuid.uuid4().hex[:6]}",
        "target_task": "Sepete urun ekle",
        "test_type": "existing_site_basic_ux",
        "current_url": "https://example.com",
        "persona_count": 300,
        "target_audience": "Genel kullanicilar",
        "authorization_confirmed": True,
        "modules": [],
    }
    client.patch(f"/api/tests/drafts/{draft['id']}", json={"payload": payload}, headers=_csrf_headers(client))

    launch_response = client.post(f"/api/tests/drafts/{draft['id']}/launch", headers=_csrf_headers(client))
    assert launch_response.status_code == 200, launch_response.text
    run_id = launch_response.json()["simulation_run_ids"][0]

    run = anyio.run(_load_simulation_run, run_id)
    assert "persona_sample" not in run.input_snapshot


@pytest.mark.security
def test_wizard_patch_rejects_stereotype_field_in_persona_distribution(client: TestClient):
    _register(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "persona_distribution": {
                    personas.DEVICE_CLASS: [
                        {"key": "mobile", "label": "Mobil", "weight": 100, "purchase_likelihood": "high"}
                    ]
                }
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400
