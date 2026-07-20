"""Analiz modulu katalogu API'si ve sihirbazin yeni gelismis modulleri kabul etmesi.

`client` fixture'i tests/conftest.py'den gelir (bkz. o dosyanin docstring'i).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

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


def _create_project(client: TestClient) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": f"Proje {uuid.uuid4().hex[:8]}"},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_draft(client: TestClient) -> dict:
    response = client.post("/api/tests/drafts", headers=_csrf_headers(client))
    assert response.status_code == 201, response.text
    return response.json()


def test_catalog_requires_authentication(client):
    response = client.get("/api/analysis-modules/catalog")
    assert response.status_code == 401


def test_catalog_lists_seven_active_modules_with_full_metadata(client):
    _register(client)
    response = client.get("/api/analysis-modules/catalog")
    assert response.status_code == 200
    body = response.json()

    assert body["catalog_version"] == "2026.1"
    assert len(body["modules"]) == 7

    by_key = {m["key"]: m for m in body["modules"]}
    for required_field in (
        "key",
        "name",
        "description",
        "outputs",
        "measurement_type",
        "chip_cost",
        "free_entitlement_feature_key",
        "estimated_duration_minutes",
        "selectable_in_wizard",
    ):
        assert required_field in by_key["network_device_test"]

    assert by_key["network_device_test"]["measurement_type"] == "technical_measurement"
    assert by_key["synthetic_attention_estimate"]["measurement_type"] == "synthetic_estimate"
    assert by_key["basic_ux_test"]["selectable_in_wizard"] is False
    assert by_key["network_device_test"]["selectable_in_wizard"] is True


def test_wizard_accepts_new_advanced_module_keys(client):
    _register(client)
    project = _create_project(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={
            "payload": {
                "project_id": project["id"],
                "modules": ["network_device_test", "campaign_cta_test", "synthetic_attention_estimate"],
            }
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["payload"]["modules"]) == {
        "network_device_test",
        "campaign_cta_test",
        "synthetic_attention_estimate",
    }


def test_wizard_rejects_unknown_module_key(client):
    _register(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"modules": ["not_a_real_module"]}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_wizard_rejects_test_type_key_as_module_selection(client):
    # "basic_ux_test" bir katalog girdisi olsa da, sihirbazin 4. adiminda
    # AYRICA secilemez (test_type olarak zaten 1. adimda secilir).
    _register(client)
    draft = _create_draft(client)

    response = client.patch(
        f"/api/tests/drafts/{draft['id']}",
        json={"payload": {"modules": ["basic_ux_test"]}},
        headers=_csrf_headers(client),
    )
    assert response.status_code == 400


def test_quote_prices_new_advanced_modules(client):
    _register(client)
    response = client.post(
        "/api/billing/quote",
        json={
            "persona_count": 100,
            "test_type": "accessibility_precheck",
            "modules": ["network_device_test", "campaign_cta_test", "synthetic_attention_estimate"],
        },
        headers=_csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pricing_version"] == "2026.2"

    costs = {item["key"]: item["chip_cost"] for item in body["line_items"]}
    assert costs["network_device_test"] == 40
    assert costs["campaign_cta_test"] == 35
    assert costs["synthetic_attention_estimate"] == 25
