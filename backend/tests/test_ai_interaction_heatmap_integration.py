"""AI etkilesim isi haritasi: quote, launch readiness ve SALT-OKUNUR rapor
bolumu (durum makinesi) testleri.

`session` (rollback) fixture'i yeterlidir: bu testler yalnizca saf quote
hesabini, readiness kapisini ve `_build_interaction_heatmap`in (yalnizca OKUYAN)
davranisini dogrular - worker'in commit'lenmis akisi ayri bir dosyadadir
(bkz. test_ai_interaction_heatmap_worker.py).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interaction_heatmap import InteractionHeatmap, InteractionHeatmapStatus
from app.models.projects import Project
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.models.tests import TestDefinition, TestVariant
from app.services import quotes
from app.services.pricing import FEATURE_BASIC_UX_TEST
from app.services.test_wizard import (
    AI_INTERACTION_HEATMAP_NOT_READY_MESSAGE,
    DraftValidationError,
    validate_interaction_heatmap_readiness,
)

pytestmark = pytest.mark.integration


# --- Quote -------------------------------------------------------------------


async def test_quote_includes_heatmap_chips_separately(session: AsyncSession, organization: Organization):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=100,
        test_type=FEATURE_BASIC_UX_TEST,
        modules=["ai_interaction_heatmap"],
    )
    assert quote.interaction_heatmap_chips == 30
    assert quote.ai_report_chips == 0
    # baseline (required_chips) heatmap ucretini ICERMEZ.
    assert quote.total_chips == quote.required_chips + 30
    keys = {item.key for item in quote.line_items}
    assert "ai_interaction_heatmap" in keys


async def test_quote_both_ai_modules_are_independent(session: AsyncSession, organization: Organization):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=100,
        test_type=FEATURE_BASIC_UX_TEST,
        modules=["ai_report", "ai_interaction_heatmap"],
    )
    assert quote.ai_report_chips == 50
    assert quote.interaction_heatmap_chips == 30
    assert quote.total_chips == quote.required_chips + 50 + 30


# --- Launch readiness kapisi -------------------------------------------------


def test_readiness_rejects_when_flag_disabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_interaction_heatmap_enabled", False)
    with pytest.raises(DraftValidationError) as exc:
        validate_interaction_heatmap_readiness({"modules": ["ai_interaction_heatmap"]})
    assert str(exc.value) == AI_INTERACTION_HEATMAP_NOT_READY_MESSAGE


def test_readiness_passes_when_enabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_interaction_heatmap_enabled", True)
    monkeypatch.setattr(settings, "ai_interaction_heatmap_provider", "mock")
    monkeypatch.setattr(settings, "environment", "development")
    # exception yok
    validate_interaction_heatmap_readiness({"modules": ["ai_interaction_heatmap"]})


def test_readiness_ignores_when_module_not_selected(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_interaction_heatmap_enabled", False)
    validate_interaction_heatmap_readiness({"modules": ["network_device_test"]})


# --- SALT-OKUNUR rapor bolumu durum makinesi ---------------------------------


async def _make_run(session: AsyncSession, organization: Organization, *, modules: list[str]) -> SimulationRun:
    project = Project(organization_id=organization.id, name=f"P {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()
    definition = TestDefinition(
        organization_id=organization.id, project_id=project.id, name=f"D {uuid.uuid4().hex[:6]}"
    )
    session.add(definition)
    await session.flush()
    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="V",
        config={"role": "primary"},
    )
    session.add(variant)
    await session.flush()
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.SUCCEEDED,
        deterministic_seed=1,
        model_version="engine",
        input_snapshot={"modules": modules, "target_task": "Hesap olustur"},
    )
    session.add(run)
    await session.flush()
    return run


async def test_report_section_not_requested(session: AsyncSession, organization: Organization):
    from app.routers.reports import _build_interaction_heatmap

    run = await _make_run(session, organization, modules=["network_device_test"])
    section = await _build_interaction_heatmap(session, organization.id, uuid.uuid4(), run)
    assert section.requested is False
    assert section.status == "not_requested"
    assert section.available is False


async def test_report_section_queued_when_no_job_row(session: AsyncSession, organization: Organization):
    from app.routers.reports import _build_interaction_heatmap

    run = await _make_run(session, organization, modules=["ai_interaction_heatmap"])
    section = await _build_interaction_heatmap(session, organization.id, uuid.uuid4(), run)
    assert section.requested is True
    assert section.status == "queued"
    assert section.available is False
    # Zorunlu aciklama her durumda dolu.
    assert "göz takibi verisi değildir" in section.disclaimer


async def test_report_section_succeeded_reads_saved_result(session: AsyncSession, organization: Organization):
    from app.routers.reports import _build_interaction_heatmap

    run = await _make_run(session, organization, modules=["ai_interaction_heatmap"])
    hm = InteractionHeatmap(
        organization_id=organization.id,
        simulation_run_id=run.id,
        status=InteractionHeatmapStatus.SUCCEEDED,
        provider="openai",
        model_name="gpt-x",
        result={
            "provider": "openai",
            "model_name": "gpt-x",
            "method": "openai_candidate_selection",
            "method_label": "OpenAI aday secimi (DOM ve gorsel ozellik destekli)",
            "summary": "ozet",
            "hotspots": [
                {
                    "candidate_id": "candidate-1",
                    "label": "Uye ol",
                    "interaction_kind": "button",
                    "role": "button",
                    "x": 0.1,
                    "y": 0.05,
                    "w": 0.1,
                    "h": 0.03,
                    "above_fold": True,
                    "score": 0.9,
                    "confidence": "high",
                    "reason": "dogrudan",
                    "task_relevance": "direct",
                }
            ],
            "unmatched_task_warning": None,
            "disclaimer": "Bu harita ... goz takibi verisi degildir.",
        },
    )
    session.add(hm)
    await session.flush()

    section = await _build_interaction_heatmap(session, organization.id, uuid.uuid4(), run)
    assert section.status == "succeeded"
    assert section.available is True
    assert section.provider == "openai"
    assert len(section.hotspots) == 1
    assert section.hotspots[0].label == "Uye ol"


async def test_report_section_failed_exposes_only_error_code(session: AsyncSession, organization: Organization):
    from app.routers.reports import _build_interaction_heatmap

    run = await _make_run(session, organization, modules=["ai_interaction_heatmap"])
    hm = InteractionHeatmap(
        organization_id=organization.id,
        simulation_run_id=run.id,
        status=InteractionHeatmapStatus.FAILED,
        provider="openai",
        error_code="provider_timeout",
    )
    session.add(hm)
    await session.flush()

    section = await _build_interaction_heatmap(session, organization.id, uuid.uuid4(), run)
    assert section.status == "failed"
    assert section.available is False
    assert section.error_code == "provider_timeout"
    assert not section.hotspots


async def test_report_section_exposes_lite_analysis_mode(
    session: AsyncSession, organization: Organization
):
    """Analyzer hafif ("lite") modda calistiysa, salt-okunur rapor bolumu bunu
    `analysis_mode="lite"` + `analysis_limited=True` olarak frontend'e tasir -
    UI bunu (hata degil) bilgilendirme olarak gosterebilsin."""

    from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
    from app.routers.reports import _build_interaction_heatmap

    run = await _make_run(session, organization, modules=["ai_interaction_heatmap"])
    analysis = PageAnalysis(
        organization_id=organization.id,
        source_kind=PageAnalysisSourceKind.URL,
        url="https://example.com",
        status=PageAnalysisStatus.SUCCEEDED,
        features={"element_boxes": [], "analysis_mode": "lite", "analysis_limited": True},
        image_width=1366,
        image_height=900,
        content_sha256="sha-lite",
    )
    session.add(analysis)
    await session.flush()
    run.page_analysis_id = analysis.id
    await session.flush()

    section = await _build_interaction_heatmap(session, organization.id, uuid.uuid4(), run)
    assert section.analysis_mode == "lite"
    assert section.analysis_limited is True
