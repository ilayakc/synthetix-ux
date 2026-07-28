"""Paket 4B: SimulationRun <-> PageAnalysis bagimlilik durum makinesi testleri.

Bu dosya, spesifikasyonun can alici garantisini dogrular: yeni URL run'lari,
bagli PageAnalysis SUCCEEDED + gecerli `features` tasimadan analiz edilmez ve
hicbir kosulda sessizce `app.engine.fixtures` (sha256(url) yer tutucusu) yer
alanina duselmez; eski (page_analysis_id NULL) run'lar ise davranissiz
degismeden calismaya devam eder (geriye donuk uyumluluk).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import fixtures as engine_fixtures
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.services import simulation_worker

pytestmark = pytest.mark.integration


async def _make_variant(session: AsyncSession, organization: Organization, *, url: str):
    from app.models.projects import Project
    from app.models.tests import TestDefinition, TestVariant

    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()

    definition = TestDefinition(
        organization_id=organization.id,
        project_id=project.id,
        name=f"Test tanimi {uuid.uuid4().hex[:6]}",
    )
    session.add(definition)
    await session.flush()

    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Ana Senaryo",
        config={"role": "primary", "url": url},
    )
    session.add(variant)
    await session.flush()
    return variant


def _input_snapshot(*, url: str) -> dict:
    return {
        "wizard_test_type": "existing_site_basic_ux",
        "persona_count": 500,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": [],
        "url": url,
        "role": "primary",
        "pricing_version": "2026.2",
    }


def _dom_features(**overrides) -> dict:
    base = {
        "final_url": "https://example.com/",
        "redirect_count": 0,
        "title": "Ornek",
        "headings": [{"level": 1, "text": "Ornek", "order": 0}],
        "text_stats": {
            "word_count": 400,
            "avg_sentence_word_count": 12.0,
            "visible_text_char_count": 2000,
            "heading_count": 3,
        },
        "controls": {"link_count": 4, "button_count": 2, "form_count": 1, "form_field_count": 3},
        "element_boxes": [
            {"role": "button", "x": 100.0, "y": 200.0, "width": 120.0, "height": 40.0},
        ],
        "layout_regions": {
            "ust_navigasyon": None,
            "hero_baslik": None,
            "birincil_cta": {"x_pct": 40.0, "y_pct": 20.0, "width_pct": 20.0, "height_pct": 6.0},
            "govde_metni": None,
            "alt_bilgi": None,
        },
        "performance": {
            "dom_content_loaded_ms": 100.0,
            "load_event_ms": 150.0,
            "first_contentful_paint_ms": 80.0,
            "total_navigation_ms": 150.0,
        },
        "contrast_candidates": [
            {"selector": "p", "foreground": "#000", "background": "#fff", "ratio": 21.0, "meets_aa": True},
        ],
        "accessibility_precheck": {
            "disclaimer": "on kontrol",
            "violations": [],
            "passes_count": 3,
            "incomplete_count": 0,
        },
        "warnings": [],
    }
    base.update(overrides)
    return base


async def _make_page_analysis(
    session: AsyncSession,
    organization: Organization,
    *,
    url: str = "https://example.com/",
    status: PageAnalysisStatus = PageAnalysisStatus.SUCCEEDED,
    features: dict | None = None,
    content_sha256: str | None = "a" * 64,
) -> PageAnalysis:
    analysis = PageAnalysis(
        organization_id=organization.id,
        source_kind=PageAnalysisSourceKind.URL,
        url=url,
        authorization_confirmed=True,
        status=status,
        analyzer_version="analyzer-2026.1",
        snapshot_version="page-feature-snapshot-live-2026.2",
        source="analyzer_live",
        features=features,
        content_sha256=content_sha256,
        image_width=1366,
        image_height=900,
    )
    session.add(analysis)
    await session.flush()
    return analysis


async def _make_run(
    session: AsyncSession,
    organization: Organization,
    *,
    url: str = "https://example.com/",
    page_analysis_id: uuid.UUID | None = None,
    launch_run_id: uuid.UUID | None = None,
    chip_reservation_id: uuid.UUID | None = None,
    status: SimulationStatus = SimulationStatus.QUEUED,
) -> SimulationRun:
    variant = await _make_variant(session, organization, url=url)
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=status,
        deterministic_seed=42,
        model_version="pending-engine",
        input_snapshot=_input_snapshot(url=url),
        page_analysis_id=page_analysis_id,
        launch_run_id=launch_run_id,
        chip_reservation_id=chip_reservation_id,
    )
    session.add(run)
    await session.flush()
    return run


# --- Claim filtresi ----------------------------------------------------------


async def test_claim_skips_run_blocked_by_queued_page_analysis(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_page_analysis(
        session, organization, status=PageAnalysisStatus.QUEUED, features=None
    )
    run = await _make_run(session, organization, page_analysis_id=analysis.id)

    claimed = await simulation_worker.claim_next_queued_runs(session)

    assert run.id not in {r.id for r in claimed}
    await session.refresh(run)
    assert run.status == SimulationStatus.QUEUED
    assert run.attempt_count == 0


async def test_claim_skips_run_blocked_by_running_page_analysis(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_page_analysis(
        session, organization, status=PageAnalysisStatus.RUNNING, features=None
    )
    run = await _make_run(session, organization, page_analysis_id=analysis.id)

    claimed = await simulation_worker.claim_next_queued_runs(session)

    assert run.id not in {r.id for r in claimed}


async def test_claim_includes_run_with_succeeded_page_analysis(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_page_analysis(session, organization, features=_dom_features())
    run = await _make_run(session, organization, page_analysis_id=analysis.id)

    claimed = await simulation_worker.claim_next_queued_runs(session)

    assert run.id in {r.id for r in claimed}


async def test_claim_includes_legacy_run_with_null_page_analysis(
    session: AsyncSession, organization: Organization
):
    run = await _make_run(session, organization, page_analysis_id=None)

    claimed = await simulation_worker.claim_next_queued_runs(session)

    assert run.id in {r.id for r in claimed}


# --- FAILED PageAnalysis -> engellenen run'lar sonsuza kadar QUEUED kalmaz ---


async def test_fail_runs_blocked_by_failed_page_analysis_fails_run(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_page_analysis(
        session, organization, status=PageAnalysisStatus.FAILED, features=None
    )
    run = await _make_run(session, organization, page_analysis_id=analysis.id)

    count = await simulation_worker.fail_runs_blocked_by_failed_page_analysis(session)

    assert count == 1
    await session.refresh(run)
    assert run.status == SimulationStatus.FAILED
    assert run.error


async def test_fail_runs_blocked_by_failed_page_analysis_is_idempotent(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_page_analysis(
        session, organization, status=PageAnalysisStatus.FAILED, features=None
    )
    await _make_run(session, organization, page_analysis_id=analysis.id)

    first = await simulation_worker.fail_runs_blocked_by_failed_page_analysis(session)
    second = await simulation_worker.fail_runs_blocked_by_failed_page_analysis(session)

    assert first == 1
    assert second == 0


async def test_ab_one_side_page_analysis_failure_releases_full_group(
    session: AsyncSession, organization: Organization
):
    """A basarili PageAnalysis'e, B FAILED PageAnalysis'e bagliysa: B FAILED
    olunca grubun TUM Chip rezervasyonu release edilir (A basarili olsa bile)."""

    from app.models.billing import ChipReservationStatus
    from app.services import chip_ledger

    await chip_ledger.credit(session, organization.id, 500, "kredi")

    launch_run_id = uuid.uuid4()
    reservation = await chip_ledger.reserve_chips(
        session, organization.id, 10, "test", run_id=launch_run_id, idempotency_key=f"test:{launch_run_id}"
    )

    analysis_a = await _make_page_analysis(session, organization, features=_dom_features())
    analysis_b = await _make_page_analysis(
        session, organization, status=PageAnalysisStatus.FAILED, features=None
    )

    await _make_run(
        session,
        organization,
        page_analysis_id=analysis_a.id,
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
        status=SimulationStatus.SUCCEEDED,
    )
    run_b = await _make_run(
        session,
        organization,
        page_analysis_id=analysis_b.id,
        launch_run_id=launch_run_id,
        chip_reservation_id=reservation.id,
        status=SimulationStatus.QUEUED,
    )

    await simulation_worker.fail_runs_blocked_by_failed_page_analysis(session)

    await session.refresh(run_b)
    assert run_b.status == SimulationStatus.FAILED

    await session.refresh(reservation)
    assert reservation.status == ChipReservationStatus.RELEASED


# --- process_run: gercek DOM adapter yolu ------------------------------------


async def test_process_run_uses_dom_input_and_marks_feature_source_dom(
    session: AsyncSession, organization: Organization, monkeypatch
):
    analysis = await _make_page_analysis(session, organization, features=_dom_features())
    run = await _make_run(
        session, organization, page_analysis_id=analysis.id, status=SimulationStatus.RUNNING
    )

    def _boom(url: str, role: str):  # pragma: no cover - hicbir zaman cagrilmamali
        raise AssertionError("bagli PageAnalysis varken fixtures.get_page_feature_snapshot cagrilmamali")

    monkeypatch.setattr(engine_fixtures, "get_page_feature_snapshot", _boom)

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.SUCCEEDED
    assert run.result["feature_source"] == "dom"
    assert run.result["feature_source"] == "dom"
    assert run.result["provenance"]["page_analysis_id"] == str(analysis.id)


async def test_process_run_legacy_null_page_analysis_uses_fixture_path(
    session: AsyncSession, organization: Organization
):
    run = await _make_run(session, organization, page_analysis_id=None, status=SimulationStatus.RUNNING)

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.SUCCEEDED
    assert run.result["feature_source"] == "fixture"


async def test_process_run_fails_when_page_analysis_features_missing(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_page_analysis(session, organization, features=None)
    run = await _make_run(
        session, organization, page_analysis_id=analysis.id, status=SimulationStatus.RUNNING
    )

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.FAILED
    assert run.result is None


async def test_process_run_fails_when_page_analysis_schema_invalid(
    session: AsyncSession, organization: Organization
):
    bad_features = _dom_features()
    bad_features["controls"] = "not-a-dict"
    analysis = await _make_page_analysis(session, organization, features=bad_features)
    run = await _make_run(
        session, organization, page_analysis_id=analysis.id, status=SimulationStatus.RUNNING
    )

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.FAILED


async def test_process_run_fails_when_page_analysis_belongs_to_other_org(
    session: AsyncSession, organization: Organization, make_organization
):
    other_org = await make_organization(name="Baska Organizasyon")
    analysis = await _make_page_analysis(session, other_org, features=_dom_features())
    run = await _make_run(
        session, organization, page_analysis_id=analysis.id, status=SimulationStatus.RUNNING
    )

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.FAILED


async def test_process_run_dom_result_differs_for_different_features(
    session: AsyncSession, organization: Organization
):
    """Ayni URL, iki farkli gercek DOM feature seti -> farkli sonuc (URL hash'i degil)."""

    analysis_rich = await _make_page_analysis(
        session, organization, url="https://example.com/x", features=_dom_features(), content_sha256="b" * 64
    )
    analysis_poor = await _make_page_analysis(
        session,
        organization,
        url="https://example.com/x",
        features=_dom_features(
            controls={"link_count": 0, "button_count": 0, "form_count": 0, "form_field_count": 0},
            element_boxes=[],
            layout_regions={
                "ust_navigasyon": None,
                "hero_baslik": None,
                "birincil_cta": None,
                "govde_metni": None,
                "alt_bilgi": None,
            },
        ),
        content_sha256="c" * 64,
    )

    run_rich = await _make_run(
        session,
        organization,
        url="https://example.com/x",
        page_analysis_id=analysis_rich.id,
        status=SimulationStatus.RUNNING,
    )
    run_poor = await _make_run(
        session,
        organization,
        url="https://example.com/x",
        page_analysis_id=analysis_poor.id,
        status=SimulationStatus.RUNNING,
    )

    await simulation_worker.process_run(session, run_rich)
    await simulation_worker.process_run(session, run_poor)

    await session.refresh(run_rich)
    await session.refresh(run_poor)

    assert run_rich.result["page_feature_snapshot"] != run_poor.result["page_feature_snapshot"]


# --- process_run: gercek uctan uca DesignAsset (visual) yolu ------------------
# Bu blok, Paket 4 Final canli smoke testinde bulunan gercek bir regresyonu
# (`app.engine.baseline.run_baseline_simulation`in `input_snapshot.url` sart
# kosmasi - DesignAsset kaynaklarinda `url=None`) yeniden uretir; oncesinde
# hicbir test `launch_draft`in urettigiyle AYNI sekle sahip (pseudo-url iceren)
# bir input_snapshot ile process_run'i gercekten calistirmiyordu.


def _visual_features() -> dict:
    return {
        "feature_source": "visual_heuristic",
        "algorithm_version": "visual-analysis-1",
        "visual_cta_candidates": [
            {
                "kind": "visual_cta_candidate",
                "box": {"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.06},
                "heuristic_score": 0.72,
                "dominant_color": {"r": 30, "g": 90, "b": 200},
                "regional_visual_contrast_estimate": 6.5,
                "size_component": 0.4,
                "position_component": 0.6,
                "contrast_component": 0.3,
                "evidence": ["edge_contour", "size_heuristic"],
                "algorithm_version": "visual-analysis-1",
            }
        ],
        "candidate_disclaimer": "test",
        "synthetic_attention_estimate": {
            "cells": [{"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5, "intensity": 0.4}],
            "feature_source": "visual_heuristic",
            "algorithm_version": "visual-analysis-1",
            "disclaimer": "test",
        },
        "limitations": [],
    }


async def _make_design_asset(session: AsyncSession, organization: Organization) -> uuid.UUID:
    from app.models.design_assets import DesignAsset, DesignAssetContentType, DesignAssetStatus

    asset = DesignAsset(
        organization_id=organization.id,
        uploaded_by_user_id=None,
        content_type=DesignAssetContentType.PNG,
        byte_size=1234,
        width=800,
        height=600,
        checksum_sha256="b" * 64,
        status=DesignAssetStatus.ACTIVE,
        image_data=b"fake-png-bytes",
    )
    session.add(asset)
    await session.flush()
    return asset.id


async def _make_design_asset_run(
    session: AsyncSession,
    organization: Organization,
    *,
    design_asset_id: uuid.UUID,
    modules: list[str] | None = None,
) -> tuple[PageAnalysis, SimulationRun]:
    analysis = PageAnalysis(
        organization_id=organization.id,
        source_kind=PageAnalysisSourceKind.DESIGN_ASSET,
        design_asset_id=design_asset_id,
        url=None,
        authorization_confirmed=True,
        status=PageAnalysisStatus.SUCCEEDED,
        snapshot_version="design-asset-snapshot-1",
        source="design_asset",
        features=_visual_features(),
        content_sha256="b" * 64,
        image_width=800,
        image_height=600,
    )
    session.add(analysis)
    await session.flush()

    variant = await _make_variant(session, organization, url="https://unused.example.com")
    # `launch_draft` ile AYNI sekil (Paket 4 Final Hardening): gercek URL
    # yok, `url` acikca `None`dur; ayri, acikca isimlendirilmis
    # `source_reference` alani motorun "bos olmayan kimlik" sartini
    # karsilar (bkz. app.services.test_wizard.launch_draft,
    # app.engine.baseline.run_baseline_simulation,
    # app.engine.advanced_modules._require_source_identity).
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.RUNNING,
        deterministic_seed=42,
        model_version="pending-engine",
        input_snapshot={
            "wizard_test_type": "existing_site_basic_ux",
            "persona_count": 500,
            "target_audience": "Yeni B2B musteri adaylari",
            "modules": modules or [],
            "url": None,
            "source_reference": f"design-asset:{design_asset_id}",
            "role": "primary",
            "source_type": "screenshot",
            "design_asset_id": str(design_asset_id),
            "pricing_version": "2026.2",
        },
        page_analysis_id=analysis.id,
    )
    session.add(run)
    await session.flush()
    return analysis, run


async def test_process_run_succeeds_end_to_end_for_design_asset_source(
    session: AsyncSession, organization: Organization
):
    """DesignAsset (screenshot/AI) kaynakli bir run, `launch_draft`in urettigi
    GERCEK input_snapshot sekliyle `process_run`den basariyla gecmeli -
    regresyon: `run_baseline_simulation`in `url` sarti. Paket 4 Final
    Hardening: `result["url"]` ARTIK sahte bir deger tasimaz - acikca
    `None`dir, gercek kimlik yalnizca `source_reference`/`design_asset_id`
    alanlarindadir (bkz. app.services.test_wizard.launch_draft)."""

    design_asset_id = await _make_design_asset(session, organization)
    _analysis, run = await _make_design_asset_run(
        session, organization, design_asset_id=design_asset_id, modules=["synthetic_attention_estimate"]
    )

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.SUCCEEDED, run.error
    assert run.result["feature_source"] == "visual_heuristic"
    assert run.result["provenance"]["feature_source"] == "visual_heuristic"
    assert "synthetic_attention_estimate" in (run.result.get("modules") or {})
    assert run.result["url"] is None
    assert run.result["source_reference"] == f"design-asset:{design_asset_id}"
    module_result = run.result["modules"]["synthetic_attention_estimate"]
    assert module_result["url"] is None
    assert module_result["source_reference"] == f"design-asset:{design_asset_id}"


async def test_process_run_prioritizes_user_confirmed_cta_for_design_asset_source(
    session: AsyncSession, organization: Organization
):
    design_asset_id = await _make_design_asset(session, organization)
    _analysis, run = await _make_design_asset_run(
        session, organization, design_asset_id=design_asset_id, modules=["campaign_cta_test"]
    )
    run.input_snapshot = {
        **run.input_snapshot,
        "user_confirmed_cta": {
            "design_asset_id": str(design_asset_id),
            "box": {"x": 0.1, "y": 0.1, "w": 0.15, "h": 0.05},
            "selection_source": "manual_box",
            "verified_content_sha256": "b" * 64,
        },
    }
    await session.flush()

    await simulation_worker.process_run(session, run)

    await session.refresh(run)
    assert run.status == SimulationStatus.SUCCEEDED, run.error
    ctas = run.result["modules"]["campaign_cta_test"]["ctas"]
    assert ctas[0]["classification"] == "user_confirmed_cta"
