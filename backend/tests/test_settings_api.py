"""Ayarlar API'si: kullanici tercihleri, organizasyon varsayilanlari, roller, tenant izolasyonu.

`client` fixture'i tests/conftest.py'den gelir. Rol testleri icin, gercek bir
davet akisi henuz olmadigindan (bu asamanin kapsami disinda), ayni kullanicinin
erisim tokenini farkli bir `role` degeriyle yeniden imzaliyoruz - `Principal.role`
zaten dogrudan (DB'ye tekrar sorulmadan) imzali token'dan cozumlendigi icin
(bkz. app/dependencies.py `get_current_principal`), bu rol yetkilendirmesini
gercek HTTP istekleri uzerinden dogrulamanin en dogru yoludur.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.cookies import ACCESS_TOKEN_COOKIE
from app.security import create_access_token

pytestmark = pytest.mark.integration


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


def _as_role(client: TestClient, session: dict, role: str) -> None:
    """Ayni kullanicinin erisim cookie'sini farkli bir rolle yeniden imzalar."""

    token = create_access_token(
        user_id=uuid.UUID(session["user_id"]),
        organization_id=uuid.UUID(session["organization_id"]),
        role=role,
    )
    client.cookies.set(ACCESS_TOKEN_COOKIE, token)


def test_get_and_patch_own_preferences(client):
    _register(client)

    response = client.get("/api/settings/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["language"] == "tr"
    assert body["timezone"] == "Europe/Istanbul"
    assert body["theme"] == "system"
    assert body["compact_view"] is False

    response = client.patch(
        "/api/settings/me",
        json={"display_name": "Yeni Ad", "theme": "dark", "compact_view": True},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == "Yeni Ad"
    assert body["theme"] == "dark"
    assert body["compact_view"] is True
    # Gonderilmeyen alanlar degismedi
    assert body["language"] == "tr"
    assert body["notify_report_ready"] is True


def test_owner_can_update_company_name_and_currency(client):
    _register(client)

    response = client.patch(
        "/api/settings/organization",
        json={"name": "Yeni Sirket Adi", "currency": "USD"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Yeni Sirket Adi"
    assert body["currency"] == "USD"
    assert body["can_edit_company"] is True
    assert body["can_edit_defaults"] is True


def test_admin_can_update_company_name(client):
    session = _register(client)
    _as_role(client, session, "admin")

    response = client.patch(
        "/api/settings/organization",
        json={"name": "Admin Degisikligi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Admin Degisikligi"


def test_analyst_and_viewer_cannot_update_company_name(client):
    session = _register(client)

    _as_role(client, session, "analyst")
    response = client.patch(
        "/api/settings/organization",
        json={"name": "Analist Degisikligi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403

    _as_role(client, session, "viewer")
    response = client.patch(
        "/api/settings/organization",
        json={"name": "Izleyici Degisikligi"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


def test_analyst_can_update_test_defaults_but_viewer_cannot(client):
    session = _register(client)

    _as_role(client, session, "analyst")
    response = client.patch(
        "/api/settings/organization",
        json={"default_persona_count": 1000},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["default_persona_count"] == 1000

    _as_role(client, session, "viewer")
    response = client.patch(
        "/api/settings/organization",
        json={"default_persona_count": 2000},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 403


def test_viewer_can_read_but_not_write(client):
    session = _register(client)
    _as_role(client, session, "viewer")

    response = client.get("/api/settings/organization")
    assert response.status_code == 200
    body = response.json()
    assert body["can_edit_company"] is False
    assert body["can_edit_defaults"] is False


def test_cross_organization_settings_are_isolated(client):
    org_a = _register(client)
    response = client.patch(
        "/api/settings/organization",
        json={"name": "Org A Ozel Adi", "currency": "EUR"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text

    org_b = _register(client)
    response = client.get("/api/settings/organization")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] != "Org A Ozel Adi"
    assert body["currency"] == "TRY"
    assert body["organization_id"] == org_b["organization_id"]
    assert body["organization_id"] != org_a["organization_id"]


@pytest.mark.parametrize(
    "count,expected_status",
    [(99, 400), (100, 200), (50_000, 200), (50_001, 400)],
)
def test_persona_count_bounds(client, count, expected_status):
    _register(client)
    response = client.patch(
        "/api/settings/organization",
        json={"default_persona_count": count},
        headers=_csrf_headers(client),
    )
    assert response.status_code == expected_status, response.text


def test_invalid_theme_rejected(client):
    _register(client)
    response = client.patch(
        "/api/settings/me",
        json={"theme": "rainbow"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_invalid_timezone_rejected(client):
    _register(client)
    response = client.patch(
        "/api/settings/me",
        json={"timezone": "Mars/Olympus_Mons"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_invalid_module_key_rejected(client):
    _register(client)
    response = client.patch(
        "/api/settings/organization",
        json={"default_modules": ["not_a_real_module"]},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_settings_applied_to_new_wizard_draft(client):
    _register(client)
    response = client.patch(
        "/api/settings/organization",
        json={
            "default_persona_count": 1500,
            "default_modules": ["network_device_test"],
            "default_target_audience": "Genç, mobil kullanıcılar",
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text

    draft_response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert draft_response.status_code == 201, draft_response.text
    payload = draft_response.json()["payload"]
    assert payload["persona_count"] == 1500
    assert payload["modules"] == ["network_device_test"]
    assert payload["target_audience"] == "Genç, mobil kullanıcılar"


def test_settings_change_does_not_affect_existing_draft(client):
    _register(client)
    client.patch(
        "/api/settings/organization",
        json={"default_persona_count": 800},
        headers=_csrf_headers(client),
    )
    draft = client.post("/api/tests/drafts", headers=_csrf_headers(client)).json()
    assert draft["payload"]["persona_count"] == 800

    client.patch(
        "/api/settings/organization",
        json={"default_persona_count": 5000},
        headers=_csrf_headers(client),
    )

    refetched = client.get(f"/api/tests/drafts/{draft['id']}")
    assert refetched.status_code == 200
    assert refetched.json()["payload"]["persona_count"] == 800


def test_no_scientific_integrity_bypass_fields_exist(client):
    _register(client)
    me = client.get("/api/settings/me").json()
    org = client.get("/api/settings/organization").json()

    forbidden_substrings = ("disclaimer", "calibration", "hide_", "synthetic_label")
    for payload in (me, org):
        for key in payload:
            lowered = key.lower()
            assert not any(term in lowered for term in forbidden_substrings), key


def test_repeated_identical_save_has_no_side_effect(client):
    _register(client)
    first = client.patch(
        "/api/settings/me",
        json={"theme": "light"},
        headers=_csrf_headers(client),
    )
    assert first.status_code == 200
    updated_at_1 = first.json()["updated_at"]

    second = client.patch(
        "/api/settings/me",
        json={"theme": "light"},
        headers=_csrf_headers(client),
    )
    assert second.status_code == 200
    assert second.json()["updated_at"] == updated_at_1


def test_stale_default_persona_preset_falls_back_safely(client):
    _register(client)

    preset_response = client.post(
        "/api/personas/presets",
        json={"name": "Ozel Preset", "distribution": {}},
        headers=_csrf_headers(client),
    )
    assert preset_response.status_code == 201, preset_response.text
    preset_id = preset_response.json()["id"]

    set_default = client.patch(
        "/api/settings/organization",
        json={"default_persona_preset_id": preset_id},
        headers=_csrf_headers(client),
    )
    assert set_default.status_code == 200, set_default.text
    assert set_default.json()["effective_default_persona_preset_id"] == preset_id

    archive_response = client.post(
        f"/api/personas/presets/{preset_id}/archive",
        headers=_csrf_headers(client),
    )
    assert archive_response.status_code == 200, archive_response.text

    org_settings = client.get("/api/settings/organization")
    assert org_settings.status_code == 200
    body = org_settings.json()
    assert body["default_persona_preset_id"] == preset_id
    assert body["effective_default_persona_preset_id"] is None
    assert len(body["warnings"]) > 0

    draft = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert draft.status_code == 201, draft.text
    assert "persona_preset_id" not in draft.json()["payload"]
