from pathlib import Path

import pytest
import yaml

from app.config import Settings

pytestmark = pytest.mark.unit


def test_render_postgres_url_is_normalized_for_asyncpg():
    settings = Settings(database_url="postgresql://user:strong-password@db:5432/app")
    assert settings.database_url == "postgresql+asyncpg://user:strong-password@db:5432/app"


def test_existing_asyncpg_url_is_unchanged():
    url = "postgresql+asyncpg://user:strong-password@db:5432/app"
    assert Settings(database_url=url).database_url == url


def test_render_blueprint_declares_only_free_resources():
    blueprint_path = Path(__file__).resolve().parents[2] / "render.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))

    assert blueprint["services"]
    assert all(service["plan"] == "free" for service in blueprint["services"])
    assert all(service["type"] in {"web", "keyvalue"} for service in blueprint["services"])
    assert all(database["plan"] == "free" for database in blueprint["databases"])

    # Ayri (public) analyzer web servisi KALDIRILDI - analyzer artik ana
    # container icinde loopback'te calisir. DB/Valkey kaynaklari cogaltilmadi.
    service_names = {service["name"] for service in blueprint["services"]}
    assert "synthetix-ux-ily-analyzer" not in service_names
    assert sum(1 for s in blueprint["services"] if s["type"] == "web") == 1
    assert len(blueprint["databases"]) == 1
    assert sum(1 for s in blueprint["services"] if s["type"] == "keyvalue") == 1

    main_service = next(service for service in blueprint["services"] if service["name"] == "synthetix-ux-ily")
    env = {item["key"]: item for item in main_service["envVars"]}
    # Analyzer artik loopback uzerinden cagrilir (public edge/429 YOK) ve
    # limitleri ana servisin env'inden okunur.
    assert env["ANALYZER_BASE_URL"]["value"] == "http://127.0.0.1:8100"
    assert env["MAX_CONCURRENT_ANALYSES"]["value"] == "1"
    assert env["NAVIGATION_TIMEOUT_SECONDS"]["value"] == "45"
    assert env["ACCESSIBILITY_SCAN_TIMEOUT_SECONDS"]["value"] == "20"
    assert env["JWT_SECRET_KEY"] == {"key": "JWT_SECRET_KEY", "generateValue": True}
    assert env["ANALYZER_SHARED_TOKEN"] == {
        "key": "ANALYZER_SHARED_TOKEN",
        "generateValue": True,
    }
    assert env["AI_REPORT_PROVIDER"]["value"] == "openai"
    assert env["OPENAI_API_KEY"] == {"key": "OPENAI_API_KEY", "sync": False}
    assert env["OPENAI_MODEL"]["value"] == "gpt-5.6-terra"
    assert env["OPENAI_REASONING_EFFORT"]["value"] == "medium"
    assert env["OPENAI_MAX_OUTPUT_TOKENS"]["value"] == "4000"
    assert env["AI_INTERACTION_HEATMAP_ENABLED"]["value"] == "true"
    assert env["AI_INTERACTION_HEATMAP_PROVIDER"]["value"] == "openai"
    assert "ALLOW_MOCK_AI_PROVIDER" not in env
    assert env["BOOTSTRAP_ADMIN_EMAIL"]["value"] == "synthetix.demo.admin@example.com"
    assert env["BOOTSTRAP_ADMIN_PASSWORD"] == {
        "key": "BOOTSTRAP_ADMIN_PASSWORD",
        "generateValue": True,
    }
    assert env["BOOTSTRAP_USER_EMAIL"]["value"] == "synthetix.demo.user@example.com"
    assert env["BOOTSTRAP_USER_PASSWORD"] == {
        "key": "BOOTSTRAP_USER_PASSWORD",
        "generateValue": True,
    }
