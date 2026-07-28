"""Simulasyon raporu API'si icin testler: tenant izolasyonu, degismez (immutable)
snapshot, belirsizlik gorunumu, yasak iddialarin yoklugu, bos heatmap ve
erisilebilir grafik ozeti.

Router uc noktalari HTTP uzerinden degil, dogrudan (Depends varsayilanlarini
atlayarak) cagrilir - bu, bu dosyanin geri kalan testleri gibi, tam bir
kayit/CSRF akisi kurmadan router mantigini test etmeyi saglar.

`test_engine`, `session` ve `organization` fixture'lari artik tests/conftest.py'de
paylasilan altyapidan gelir (izole test veritabani + her testte rollback).
"""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import baseline
from app.models.page_analysis import PageAnalysis, PageAnalysisSourceKind, PageAnalysisStatus
from app.models.projects import Project
from app.models.reports import Report
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.models.tests import TestDefinition, TestVariant
from app.routers import reports as reports_router
from app.services import personas, simulation_worker

pytestmark = pytest.mark.integration


async def _make_project_and_variant(
    session: AsyncSession,
    organization: Organization,
    *,
    role: str = "primary",
    url: str = "https://example.com/anasayfa",
    variant_name: str = "Ana Senaryo",
) -> tuple[Project, TestDefinition, TestVariant]:
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
        name=variant_name,
        config={"role": role, "url": url},
    )
    session.add(variant)
    await session.flush()
    return project, definition, variant


def _persona_sample(seed: int, persona_count: int = 200) -> dict:
    distribution = {
        personas.AGE_RANGE: [
            {"key": "18_24", "label": "18-24", "weight": 95, "min_age": 18, "max_age": 24},
            {"key": "25_34", "label": "25-34", "weight": 5, "min_age": 25, "max_age": 34},
        ],
    }
    result = personas.sample_cohorts(distribution, persona_count, seed)
    return {
        "generator_version": result.generator_version,
        "distribution_snapshot": result.distribution_snapshot,
        "segments": [
            {
                "key": s.key,
                "label": s.label,
                "dimension_values": s.dimension_values,
                "count": s.count,
                "share": s.share,
            }
            for s in result.segments
        ],
    }


async def _make_succeeded_run_with_report(
    session: AsyncSession,
    organization: Organization,
    variant: TestVariant,
    *,
    seed: int = 42,
    url: str = "https://example.com/anasayfa",
    role: str = "primary",
    persona_count: int = 200,
    with_persona_sample: bool = True,
    modules: list[str] | None = None,
) -> SimulationRun:
    input_snapshot = {
        "wizard_test_type": "existing_site_basic_ux",
        "persona_count": persona_count,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": modules or [],
        "url": url,
        "role": role,
        "pricing_version": "2026.1",
    }
    if with_persona_sample:
        input_snapshot["persona_sample"] = _persona_sample(seed, persona_count)

    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.RUNNING,
        deterministic_seed=seed,
        model_version="pending-engine",
        input_snapshot=input_snapshot,
    )
    session.add(run)
    await session.flush()

    # Gercek uretim yoluyla ayni sekilde: motoru calistir, run'i basariya
    # tasi ve Report'u olustur (bkz. app.services.simulation_worker.process_run).
    # Boylece test, rapor olusturma mantigini yeniden yazmaz.
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.SUCCEEDED
    return run


async def _get_report_for_run(session: AsyncSession, run: SimulationRun) -> Report:
    result = await session.execute(Report.__table__.select().where(Report.simulation_run_id == run.id))
    row = result.first()
    assert row is not None
    return await session.get(Report, row.id)


_FULL_LAYOUT_REGIONS = {
    "ust_navigasyon": {"x_pct": 0.0, "y_pct": 0.0, "width_pct": 100.0, "height_pct": 6.0},
    "hero_baslik": {"x_pct": 5.0, "y_pct": 8.0, "width_pct": 90.0, "height_pct": 10.0},
    "birincil_cta": {"x_pct": 40.0, "y_pct": 20.0, "width_pct": 20.0, "height_pct": 6.0},
    "govde_metni": {"x_pct": 5.0, "y_pct": 28.0, "width_pct": 90.0, "height_pct": 50.0},
    "alt_bilgi": {"x_pct": 0.0, "y_pct": 90.0, "width_pct": 100.0, "height_pct": 10.0},
}


async def _make_page_analysis(
    session: AsyncSession,
    organization: Organization,
    *,
    url: str = "https://example.com/anasayfa",
    status: PageAnalysisStatus = PageAnalysisStatus.SUCCEEDED,
    screenshot_data: bytes | None = b"fake-png-bytes",
    layout_regions: dict | None = None,
) -> PageAnalysis:
    analysis = PageAnalysis(
        organization_id=organization.id,
        url=url,
        authorization_confirmed=True,
        status=status,
        screenshot_data=screenshot_data,
        features={
            "final_url": url,
            "redirect_count": 0,
            "layout_regions": layout_regions if layout_regions is not None else dict(_FULL_LAYOUT_REGIONS),
        },
    )
    session.add(analysis)
    await session.flush()
    return analysis


_VISUAL_ATTENTION_CELLS = [
    {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5, "intensity": 0.9},
    {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5, "intensity": 0.2},
]


async def _make_design_asset_page_analysis(
    session: AsyncSession,
    organization: Organization,
    *,
    screenshot_data: bytes | None = b"fake-png-bytes",
    visual_cta_candidates: list[dict] | None = None,
    attention_cells: list[dict] | None = None,
    content_sha256: str = "d" * 64,
) -> PageAnalysis:
    analysis = PageAnalysis(
        organization_id=organization.id,
        source_kind=PageAnalysisSourceKind.DESIGN_ASSET,
        url=None,
        authorization_confirmed=True,
        status=PageAnalysisStatus.SUCCEEDED,
        screenshot_data=screenshot_data,
        content_sha256=content_sha256,
        image_width=800,
        image_height=600,
        features={
            "feature_source": "visual_heuristic",
            "algorithm_version": "visual-analysis-1",
            "visual_cta_candidates": (
                visual_cta_candidates
                if visual_cta_candidates is not None
                else [
                    {
                        "box": {"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.06},
                        "heuristic_score": 0.72,
                    },
                    {
                        "box": {"x": 0.1, "y": 0.8, "w": 0.15, "h": 0.05},
                        "heuristic_score": 0.31,
                    },
                ]
            ),
            "synthetic_attention_estimate": {
                "cells": attention_cells if attention_cells is not None else list(_VISUAL_ATTENTION_CELLS),
                "feature_source": "visual_heuristic",
                "algorithm_version": "visual-analysis-1",
                "disclaimer": "test disclaimer",
            },
        },
    )
    session.add(analysis)
    await session.flush()
    return analysis


# --- Tenant izolasyonu ---------------------------------------------------------


@pytest.mark.security
async def test_get_report_cross_tenant_raises_not_found(session: AsyncSession, organization: Organization):
    other_org = Organization(name="Other Org", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    with pytest.raises(HTTPException) as exc_info:
        await reports_router.get_report(report.id, organization_id=other_org.id, session=session)
    assert exc_info.value.status_code == 404


@pytest.mark.security
async def test_list_reports_only_returns_own_organization(session: AsyncSession, organization: Organization):
    other_org = Organization(name="Other Org 2", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    _project, _definition, variant = await _make_project_and_variant(session, organization)
    await _make_succeeded_run_with_report(session, organization, variant)

    own_listing = await reports_router.list_reports(
        project_id=None, test_definition_id=None, organization_id=organization.id, session=session
    )
    other_listing = await reports_router.list_reports(
        project_id=None, test_definition_id=None, organization_id=other_org.id, session=session
    )

    assert len(own_listing) == 1
    assert other_listing == []


# --- Degismez (immutable) snapshot --------------------------------------------


async def test_report_detail_reads_from_immutable_report_content_not_live_run(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    original_completion = report.content["metrics"]["task_completion_probability"]["point_estimate"]

    # Run'in kendi `result` alanini (rapor olusturulduktan sonra) degistir;
    # rapor cevabi Report.content'ten okumali, bu mutasyondan etkilenmemelidir.
    mutated_result = dict(run.result)
    mutated_result["metrics"] = dict(mutated_result["metrics"])
    mutated_result["metrics"]["task_completion_probability"] = {
        "distribution": "triangular",
        "point_estimate": 0.01,
        "low": 0.0,
        "mode": 0.01,
        "high": 0.02,
    }
    run.result = mutated_result
    await session.flush()

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.metrics["task_completion_probability"]["point_estimate"] == original_completion
    assert detail.metrics["task_completion_probability"]["point_estimate"] != 0.01


async def test_repeated_fetch_of_same_report_is_stable(session: AsyncSession, organization: Organization):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    first = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    second = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert first.metrics == second.metrics
    assert first.created_at == second.created_at


# --- Belirsizlik gorunumu ve erisilebilir grafik ozeti -------------------------


@pytest.mark.security
async def test_accessible_chart_summaries_cover_uncertainty_metrics(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    chart_keys = {s.chart_key for s in detail.accessible_chart_summaries}
    assert chart_keys == {
        "task_completion_probability",
        "misclick_probability",
        "abandonment_probability",
        "task_duration_seconds",
    }
    for summary in detail.accessible_chart_summaries:
        assert "belirsizlik" in summary.text or "p10" in summary.text


# --- Persona segmentleri: kucuk sentetik ornek uyarisi -------------------------


async def test_persona_segments_flag_small_synthetic_samples(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    # Kasitli olarak carpik bir dagilim: bir segment n<30 ile sonuclanir.
    run = await _make_succeeded_run_with_report(session, organization, variant, persona_count=100)
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert len(detail.persona_segments) >= 1
    small_segments = [s for s in detail.persona_segments if s.count < 30]
    assert any(s.small_sample_warning for s in small_segments) or all(
        s.count >= 30 for s in detail.persona_segments
    )
    for segment in detail.persona_segments:
        assert segment.small_sample_warning == (segment.count < reports_router.SMALL_SAMPLE_THRESHOLD)
    assert "n<30" in detail.persona_segment_note


async def test_no_persona_sample_yields_empty_segments(session: AsyncSession, organization: Organization):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, with_persona_sample=False)
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.persona_segments == []


# --- Bos heatmap (motor grid uretmiyor) ----------------------------------------


async def test_heatmap_is_empty_state_when_engine_produces_no_grid(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.available is False
    assert detail.heatmap.grid is None
    assert detail.heatmap.label == reports_router.SYNTHETIC_ATTENTION_LABEL
    assert detail.heatmap.disclaimer is None
    assert detail.campaign_cta is None
    assert detail.network_device is None


# --- Gelismis modul bolumleri: sentetik dikkat / kampanya CTA / ag-cihaz ------


async def test_heatmap_filled_with_disclaimer_when_attention_module_selected(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, modules=["synthetic_attention_estimate"]
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.available is True
    assert detail.heatmap.grid is not None
    assert len(detail.heatmap.grid) == 5
    assert detail.heatmap.disclaimer is not None
    lowered = detail.heatmap.disclaimer.lower()
    assert "goz izleme" in lowered or "eye-tracking" in lowered or "eye tracking" in lowered
    # Bu organizasyon icin, ayni URL'de daha once basarili bir sayfa analizi
    # (ekran goruntusu) yoksa gorsel katman uretilemez; tablo gorunumune
    # duser ve nedeni acikca belirtilir.
    assert detail.heatmap.coordinates_available is False
    assert detail.heatmap.screenshot_url is None
    assert detail.heatmap.coordinates_unavailable_reason is not None


# --- Gorsel isi haritasi: ekran goruntusu baglama, koordinat eksikligi, ---------
# --- saklama suresi dolmus ekran goruntusu, tenant izolasyonu ------------------


async def test_heatmap_shows_visual_coordinates_when_screenshot_and_layout_regions_exist(
    session: AsyncSession, organization: Organization
):
    url = "https://example.com/anasayfa"
    await _make_page_analysis(session, organization, url=url)

    _project, _definition, variant = await _make_project_and_variant(session, organization, url=url)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, url=url, modules=["synthetic_attention_estimate"]
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.available is True
    assert detail.heatmap.coordinates_available is True
    assert detail.heatmap.coordinates_unavailable_reason is None
    assert detail.heatmap.screenshot_url == f"/api/reports/{report.id}/heatmap-screenshot"
    assert detail.heatmap.regions is not None
    assert len(detail.heatmap.regions) == 5
    for region in detail.heatmap.regions:
        assert region.box is not None
        assert region.level in ("low", "medium", "high")

    screenshot_response = await reports_router.get_report_heatmap_screenshot(
        report.id, organization_id=organization.id, session=session
    )
    assert screenshot_response.media_type == "image/png"
    assert screenshot_response.body == b"fake-png-bytes"


async def test_heatmap_falls_back_to_table_when_layout_regions_incomplete(
    session: AsyncSession, organization: Organization
):
    url = "https://example.com/anasayfa"
    incomplete_regions = dict(_FULL_LAYOUT_REGIONS)
    incomplete_regions["alt_bilgi"] = None  # footer bulunamadi -> rastgele koordinat uretilmez
    await _make_page_analysis(session, organization, url=url, layout_regions=incomplete_regions)

    _project, _definition, variant = await _make_project_and_variant(session, organization, url=url)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, url=url, modules=["synthetic_attention_estimate"]
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.available is True
    assert detail.heatmap.coordinates_available is False
    assert detail.heatmap.screenshot_url is None
    assert "koordinat" in detail.heatmap.coordinates_unavailable_reason.lower()
    # Puan/bolge tablosu (erisilebilir alternatif) her zaman doludur.
    assert detail.heatmap.grid is not None
    assert len(detail.heatmap.grid) == 5


async def test_heatmap_screenshot_unavailable_when_retention_expired(
    session: AsyncSession, organization: Organization
):
    url = "https://example.com/anasayfa"
    # Saklama suresi dolmus kayitlarda `screenshot_data` NULL'a cekilir
    # (bkz. app.services.page_analysis.purge_expired_screenshots); metadata
    # satiri kalir ama ikili veri kalmaz.
    await _make_page_analysis(session, organization, url=url, screenshot_data=None)

    _project, _definition, variant = await _make_project_and_variant(session, organization, url=url)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, url=url, modules=["synthetic_attention_estimate"]
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.coordinates_available is False
    assert detail.heatmap.screenshot_url is None
    assert "ekran goruntusu" in detail.heatmap.coordinates_unavailable_reason.lower()

    with pytest.raises(HTTPException) as exc_info:
        await reports_router.get_report_heatmap_screenshot(
            report.id, organization_id=organization.id, session=session
        )
    assert exc_info.value.status_code == 404


@pytest.mark.security
async def test_heatmap_screenshot_endpoint_rejects_other_organization(
    session: AsyncSession, organization: Organization
):
    other_org = Organization(name="Other Org 3", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    url = "https://example.com/anasayfa"
    await _make_page_analysis(session, organization, url=url)

    _project, _definition, variant = await _make_project_and_variant(session, organization, url=url)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, url=url, modules=["synthetic_attention_estimate"]
    )
    report = await _get_report_for_run(session, run)

    with pytest.raises(HTTPException) as exc_info:
        await reports_router.get_report_heatmap_screenshot(
            report.id, organization_id=other_org.id, session=session
        )
    assert exc_info.value.status_code == 404


# --- Paket 4 Final: FK-tabanli cozumleme + rapor-bagli retention ---------------


async def test_heatmap_resolves_via_page_analysis_fk_even_when_url_does_not_match(
    session: AsyncSession, organization: Organization
):
    """Paket 4 Final: `SimulationRun.page_analysis_id` FK'si baglandiktan
    sonra, run'in `input_snapshot.url`'si o PageAnalysis'in `url`'siyle
    ARTIK EŞLEŞMESE bile (ör. kullanici sonradan draft'ta URL'yi degistirmis
    olabilir - bu senaryo kasitli kurulur) dogru capture FK uzerinden
    bulunur; eski URL string eslestirmesi burada YANLIS (baska bir)
    PageAnalysis'i bulurdu ya da hic bulamazdi."""

    linked_analysis = await _make_page_analysis(session, organization, url="https://fk-linked.example.com/")
    # Ayni organizasyonda, run'in KENDI input_snapshot URL'siyle eslesen
    # BASKA (yanlis) bir PageAnalysis de bilerek olusturulur - eski string
    # eslestirme yolu bunu (yanlis capture'i) bulurdu.
    await _make_page_analysis(session, organization, url="https://example.com/anasayfa")

    _project, _definition, variant = await _make_project_and_variant(
        session, organization, url="https://example.com/anasayfa"
    )
    run = await _make_succeeded_run_with_report(
        session,
        organization,
        variant,
        url="https://example.com/anasayfa",
        modules=["synthetic_attention_estimate"],
    )
    run.page_analysis_id = linked_analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.heatmap.available is True
    assert detail.heatmap.screenshot_url == f"/api/reports/{report.id}/heatmap-screenshot"

    screenshot_response = await reports_router.get_report_heatmap_screenshot(
        report.id, organization_id=organization.id, session=session
    )
    assert screenshot_response.body == b"fake-png-bytes"


@pytest.mark.security
async def test_report_fk_resolution_rejects_cross_tenant_page_analysis(
    session: AsyncSession, organization: Organization
):
    """FK cozumlemesi bile, hedef PageAnalysis'in `organization_id`si
    RAPORUN organizasyonuyla eslesmezse (teorik/savunma amacli durum)
    gorseli ASLA sizdirmamali - erisilebilir tablo gorunumune duser."""

    other_org = Organization(name="Other Org FK", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()
    foreign_analysis = await _make_page_analysis(session, other_org, url="https://foreign.example.com/")

    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, modules=["synthetic_attention_estimate"]
    )
    run.page_analysis_id = foreign_analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.heatmap.screenshot_url is None
    assert detail.heatmap.coordinates_available is False

    with pytest.raises(HTTPException) as exc_info:
        await reports_router.get_report_heatmap_screenshot(
            report.id, organization_id=organization.id, session=session
        )
    assert exc_info.value.status_code == 404


async def test_report_linked_screenshot_survives_short_ttl_expiry_until_report_retention_elapses(
    session: AsyncSession, organization: Organization
):
    """Paket 4 Final retention: kisa `screenshot_expires_at` gecmis olsa
    bile, capture TAMAMLANMIS bir Rapor'a bagliysa purge, rapor-bagli
    (daha uzun) retention penceresi dolana kadar ERTELENIR - aksi halde
    tamamlanmis bir raporun gorsel katmani sessizce kaybolurdu."""

    from datetime import UTC, datetime, timedelta

    from app.services import page_analysis as page_analysis_service

    linked_analysis = await _make_page_analysis(session, organization, url="https://retained.example.com/")
    linked_analysis.screenshot_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    run.page_analysis_id = linked_analysis.id
    await session.flush()
    await _get_report_for_run(session, run)  # Report var oldugunu dogrular.

    purged_count = await page_analysis_service.purge_expired_screenshots(session)
    assert purged_count == 0

    await session.refresh(linked_analysis)
    assert linked_analysis.screenshot_data is not None


async def test_unlinked_short_ttl_screenshot_is_purged_normally(
    session: AsyncSession, organization: Organization
):
    """Herhangi bir run'a/rapora baglanmamis bir capture, rapor-bagli
    retention'dan ETKILENMEZ - mevcut kisa TTL davranisi degismeden
    calisir."""

    from datetime import UTC, datetime, timedelta

    from app.services import page_analysis as page_analysis_service

    orphan_analysis = await _make_page_analysis(session, organization, url="https://orphan.example.com/")
    orphan_analysis.screenshot_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    purged_count = await page_analysis_service.purge_expired_screenshots(session)
    assert purged_count == 1

    await session.refresh(orphan_analysis)
    assert orphan_analysis.screenshot_data is None


async def test_old_report_without_matching_page_analysis_opens_without_error(
    session: AsyncSession, organization: Organization
):
    """Bu ozellikten once olusturulmus (eslesen bir PageAnalysis'i asla
    olmamis) eski bir rapor, hata vermeden acilmali ve tablo gorunumune
    dusmelidir."""

    _project, _definition, variant = await _make_project_and_variant(
        session, organization, url="https://legacy.example.com/"
    )
    run = await _make_succeeded_run_with_report(
        session,
        organization,
        variant,
        url="https://legacy.example.com/",
        modules=["synthetic_attention_estimate"],
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.available is True
    assert detail.heatmap.coordinates_available is False
    assert detail.heatmap.screenshot_url is None


async def test_campaign_cta_section_present_when_module_selected(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=["campaign_cta_test"])
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.campaign_cta is not None
    assert len(detail.campaign_cta.ctas) >= 1
    assert detail.campaign_cta.disclaimer


async def test_network_device_section_present_when_module_selected(
    session: AsyncSession, organization: Organization, monkeypatch: pytest.MonkeyPatch
):
    from app.services import device_network_analysis

    async def _fake_run_network_device_test(url: str) -> dict:
        return {
            "module_key": "network_device_test",
            "analyzer_version": "device-network-analyzer-2026.1",
            "url": url,
            "profiles": [
                {
                    "profile_key": "desktop_broadband",
                    "device_label": "Masaustu",
                    "network_label": "Genis bant",
                    "succeeded": True,
                    "error": None,
                    "timings": {
                        "dom_content_loaded_ms": 100.0,
                        "load_event_ms": 150.0,
                        "total_navigation_ms": 150.0,
                    },
                    "accessibility_violation_count": 0,
                }
            ],
            "error_rate": 0.0,
            "warnings": [],
            "disclaimer": "gercek teknik olcum, gercek kullanici deneyimi degildir",
        }

    monkeypatch.setattr(device_network_analysis, "run_network_device_test", _fake_run_network_device_test)

    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, modules=["network_device_test"]
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.network_device is not None
    assert detail.network_device.error_rate == 0.0
    assert len(detail.network_device.profiles) == 1


# --- Yasak iddialarin yoklugu ---------------------------------------------------


FORBIDDEN_MARKETING_WORDS = ("kazandı", "kanıtlandı", "gerçek dönüşüm", "göz takibi")


@pytest.mark.security
async def test_report_text_contains_no_forbidden_claims_or_marketing_language(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    # Esik tabanli her bulguyu tetiklemek icin kotu bir sayfa (uzun form,
    # cok adim, ust-kivrim CTA yok) simule etmek yerine dogrudan varsayilan
    # (iyi) sayfa kullanilir; her iki durumda da metin yasak ifade icermemeli.
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    all_text = [detail.disclaimer, detail.persona_segment_note]
    all_text += [f.text for f in detail.critical_findings]
    all_text += [s.text for s in detail.accessible_chart_summaries]

    for text in all_text:
        baseline.assert_no_banned_claims(text)
        lowered = text.lower()
        for word in FORBIDDEN_MARKETING_WORDS:
            assert word not in lowered, f"yasakli ifade bulundu: '{word}' -> {text}"


# --- A/B karsilastirmasi: yalnizca iki varyantli kurulumda ---------------------


async def test_ab_comparison_present_for_two_variant_definition(
    session: AsyncSession, organization: Organization
):
    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()

    definition = TestDefinition(organization_id=organization.id, project_id=project.id, name="A/B tanimi")
    session.add(definition)
    await session.flush()

    variant_a = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Mevcut Tasarim",
        config={"role": "existing", "url": "https://a.example.com"},
    )
    variant_b = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Yeni Tasarim",
        config={"role": "new", "url": "https://b.example.com"},
    )
    session.add_all([variant_a, variant_b])
    await session.flush()

    run_a = await _make_succeeded_run_with_report(
        session, organization, variant_a, url="https://a.example.com", role="existing", seed=1
    )
    await _make_succeeded_run_with_report(
        session, organization, variant_b, url="https://b.example.com", role="new", seed=2
    )
    report_a = await _get_report_for_run(session, run_a)

    detail = await reports_router.get_report(report_a.id, organization_id=organization.id, session=session)

    assert detail.ab_comparison is not None
    assert detail.ab_comparison["calibration_status"] == "uncalibrated"
    assert detail.ab_comparison["this_variant_role"] == "variant_a"
    assert detail.ab_comparison["sibling_variant_name"] == "Yeni Tasarim"
    baseline.assert_no_banned_claims(detail.ab_comparison["note"])

    # Paket 4 Final: A/B raporu tek sayfada iki bagimsiz taraf gosterebilsin
    # diye sibling'in kimligi, kaynak turu ve KENDI heatmap bolumu de tasinir.
    assert uuid.UUID(detail.ab_comparison["sibling_report_id"])
    assert detail.ab_comparison["this_source_type"] == "url"
    assert detail.ab_comparison["sibling_source_type"] == "url"
    assert "sibling_heatmap" in detail.ab_comparison
    assert detail.ab_comparison["sibling_heatmap"]["label"] == detail.heatmap.label
    assert "same_snapshot_sha256" in detail.ab_comparison


async def test_ab_comparison_flags_identical_snapshot_hash(session: AsyncSession, organization: Organization):
    """Iki taraf da AYNI PageAnalysis capture'ina (dolayisiyla ayni
    content_sha256'ya) baglanmissa, bu acikca isaretlenir - byte-duzeyinde
    ayni snapshot karsilastirildigi anlamina gelir, gorsel farklilik
    hakkinda hicbir sey KANITLAMAZ."""

    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()
    definition = TestDefinition(
        organization_id=organization.id, project_id=project.id, name="A/B ayni snapshot"
    )
    session.add(definition)
    await session.flush()
    variant_a = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Mevcut Tasarim",
        config={"role": "existing"},
    )
    variant_b = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Yeni Tasarim",
        config={"role": "new"},
    )
    session.add_all([variant_a, variant_b])
    await session.flush()

    shared_analysis = await _make_page_analysis(session, organization, url="https://shared.example.com/")
    shared_analysis.content_sha256 = "a" * 64
    await session.flush()

    run_a = await _make_succeeded_run_with_report(session, organization, variant_a, role="existing", seed=1)
    run_a.page_analysis_id = shared_analysis.id
    run_b = await _make_succeeded_run_with_report(session, organization, variant_b, role="new", seed=2)
    run_b.page_analysis_id = shared_analysis.id
    await session.flush()
    report_a = await _get_report_for_run(session, run_a)

    detail = await reports_router.get_report(report_a.id, organization_id=organization.id, session=session)
    assert detail.ab_comparison is not None
    assert detail.ab_comparison["same_snapshot_sha256"] is True


async def test_ab_comparison_absent_for_single_variant_definition(
    session: AsyncSession, organization: Organization
):
    _project, _definition, variant = await _make_project_and_variant(session, organization, role="primary")
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.ab_comparison is None


# --- Disa aktarim: JSON / CSV ---------------------------------------------------


async def test_export_json_returns_attachment_with_metrics(session: AsyncSession, organization: Organization):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    response = await reports_router.export_report_json(
        report.id, organization_id=organization.id, session=session
    )

    assert response.media_type == "application/json"
    assert "attachment" in response.headers["content-disposition"]
    assert b"task_completion_probability" in response.body


async def test_export_csv_contains_metric_and_segment_rows(session: AsyncSession, organization: Organization):
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)

    response = await reports_router.export_report_csv(
        report.id, organization_id=organization.id, session=session
    )

    assert response.media_type == "text/csv"
    csv_text = response.body.decode("utf-8")
    assert "task_completion_probability" in csv_text
    assert "persona_segment" in csv_text
    assert "kritik_bulgu" in csv_text


# --- Paket 4 Final Hardening: gercek visual attention overlay ------------------


async def test_heatmap_for_design_asset_source_returns_real_visual_grid(
    session: AsyncSession, organization: Organization
):
    """Screenshot/AI kaynakli raporlar artik DOM'un 5 sabit semantik bolgesini
    DEGIL, gercek OpenCV 8x6 piksel grid'ini dondurur - modul secimine
    BAGLI DEGILDIR (bu veri PageAnalysis islenirken her zaman uretilir)."""

    analysis = await _make_design_asset_page_analysis(session, organization)
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)

    assert detail.heatmap.available is True
    assert detail.heatmap.overlay_kind == "synthetic_visual_attention"
    assert detail.heatmap.feature_source == "visual_heuristic"
    assert detail.heatmap.regions is None
    assert detail.heatmap.grid is None
    assert detail.heatmap.visual_cells is not None
    assert len(detail.heatmap.visual_cells) == 2
    assert detail.heatmap.visual_cells[0].intensity == 0.9
    assert detail.heatmap.image_width == 800
    assert detail.heatmap.image_height == 600
    assert detail.heatmap.content_sha256 == "d" * 64
    assert detail.heatmap.screenshot_url == f"/api/reports/{report.id}/heatmap-screenshot"
    assert detail.heatmap.disclaimer  # her zaman dolu, sahte bir katman disclaimersiz gosterilmez


def test_visual_attention_disclaimer_constant_has_required_scientific_wording():
    lowered = reports_router.VISUAL_ATTENTION_DISCLAIMER.lower()
    assert "göz takibi" in lowered or "eye" in lowered
    assert "gerçek" in lowered


async def test_heatmap_for_design_asset_source_unavailable_when_cells_missing(
    session: AsyncSession, organization: Organization
):
    """Grid bossa/gecersizse sahte bir katman ASLA uretilmez."""

    analysis = await _make_design_asset_page_analysis(session, organization, attention_cells=[])
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.heatmap.available is False
    assert detail.heatmap.overlay_kind == "synthetic_visual_attention"
    assert detail.heatmap.visual_cells is None


async def test_url_source_heatmap_still_uses_semantic_regions(
    session: AsyncSession, organization: Organization
):
    """URL/DOM raporlarinda mevcut 5-bolge davranisi DEGISMEDI."""

    url = "https://example.com/anasayfa"
    await _make_page_analysis(session, organization, url=url)
    _project, _definition, variant = await _make_project_and_variant(session, organization, url=url)
    run = await _make_succeeded_run_with_report(
        session, organization, variant, url=url, modules=["synthetic_attention_estimate"]
    )
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.heatmap.overlay_kind == "semantic_region"
    assert detail.heatmap.feature_source == "dom"
    assert detail.heatmap.visual_cells is None
    assert detail.heatmap.regions is not None


# --- Paket 4 Final Hardening: CTA overlay sozlesmesi ---------------------------


async def test_cta_overlay_returns_visual_candidates_for_design_asset_source(
    session: AsyncSession, organization: Organization
):
    analysis = await _make_design_asset_page_analysis(session, organization)
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.cta_overlay.available is True
    assert detail.cta_overlay.feature_source == "visual_heuristic"
    classifications = {box.classification for box in detail.cta_overlay.boxes}
    assert classifications == {"visual_cta_candidate"}
    assert len(detail.cta_overlay.boxes) == 2
    assert all(box.heuristic_score is not None for box in detail.cta_overlay.boxes)


async def test_cta_overlay_user_confirmed_is_labeled_and_deduped_against_candidate(
    session: AsyncSession, organization: Organization
):
    """Kullanici, aday listesindeki 0. kutuyu onaylamissa - o aday BIR DAHA
    ayrica cizilmez (dedupe), yalnizca 'user_confirmed_cta' olarak gorunur."""

    analysis = await _make_design_asset_page_analysis(session, organization)
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = analysis.id
    run.input_snapshot = {
        **run.input_snapshot,
        "user_confirmed_cta": {
            "design_asset_id": str(uuid.uuid4()),
            "box": {"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.06},
            "selection_source": "candidate_confirmation",
            "source_candidate_index": 0,
            "verified_content_sha256": "d" * 64,
        },
    }
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    classifications = [box.classification for box in detail.cta_overlay.boxes]
    assert classifications.count("user_confirmed_cta") == 1
    # Aday listesindeki 2 taneden yalnizca 1'i (dedupe edilmemis olan) kalmali.
    assert classifications.count("visual_cta_candidate") == 1
    confirmed = next(box for box in detail.cta_overlay.boxes if box.classification == "user_confirmed_cta")
    assert confirmed.label == "Sizin seçtiğiniz CTA"


async def test_cta_overlay_manual_box_confirmation_does_not_dedupe_candidates(
    session: AsyncSession, organization: Organization
):
    """Manuel cizilen bir onay hicbir adaya karsilik gelmez - dedupe edilmez,
    tum adaylar + onaylanan kutu birlikte gorunur."""

    analysis = await _make_design_asset_page_analysis(session, organization)
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = analysis.id
    run.input_snapshot = {
        **run.input_snapshot,
        "user_confirmed_cta": {
            "design_asset_id": str(uuid.uuid4()),
            "box": {"x": 0.05, "y": 0.05, "w": 0.1, "h": 0.05},
            "selection_source": "manual_box",
            "source_candidate_index": None,
            "verified_content_sha256": "d" * 64,
        },
    }
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    classifications = [box.classification for box in detail.cta_overlay.boxes]
    assert classifications.count("user_confirmed_cta") == 1
    assert classifications.count("visual_cta_candidate") == 2


async def test_cta_overlay_returns_dom_candidates_normalized_for_url_source(
    session: AsyncSession, organization: Organization
):
    url = "https://example.com/anasayfa"
    analysis = PageAnalysis(
        organization_id=organization.id,
        url=url,
        authorization_confirmed=True,
        status=PageAnalysisStatus.SUCCEEDED,
        screenshot_data=b"fake-png-bytes",
        image_width=1000,
        image_height=500,
        features={
            "final_url": url,
            "redirect_count": 0,
            "element_boxes": [
                {"role": "button", "x": 100.0, "y": 50.0, "width": 200.0, "height": 40.0},
                {"role": "link", "x": 0.0, "y": 0.0, "width": 50.0, "height": 20.0},
                {"role": "div", "x": 10.0, "y": 10.0, "width": 10.0, "height": 10.0},
            ],
        },
    )
    session.add(analysis)
    await session.flush()

    _project, _definition, variant = await _make_project_and_variant(session, organization, url=url)
    run = await _make_succeeded_run_with_report(session, organization, variant, url=url, modules=[])
    run.page_analysis_id = analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.cta_overlay.feature_source == "dom"
    assert len(detail.cta_overlay.boxes) == 2  # yalnizca button/link, div haric
    box = next(b for b in detail.cta_overlay.boxes if b.x == 0.1)
    assert box.classification == "dom_interactive_candidate"
    assert box.y == 0.1
    assert box.w == 0.2
    assert box.h == 0.08


async def test_cta_overlay_unavailable_when_no_boxes(session: AsyncSession, organization: Organization):
    analysis = await _make_design_asset_page_analysis(session, organization, visual_cta_candidates=[])
    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.cta_overlay.available is False
    assert detail.cta_overlay.boxes == []


@pytest.mark.security
async def test_cta_overlay_cross_tenant_page_analysis_is_rejected(
    session: AsyncSession, organization: Organization
):
    other_org = Organization(name="Other Org CTA", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()
    foreign_analysis = await _make_design_asset_page_analysis(session, other_org)

    _project, _definition, variant = await _make_project_and_variant(session, organization)
    run = await _make_succeeded_run_with_report(session, organization, variant, modules=[])
    run.page_analysis_id = foreign_analysis.id
    await session.flush()
    report = await _get_report_for_run(session, run)

    detail = await reports_router.get_report(report.id, organization_id=organization.id, session=session)
    assert detail.cta_overlay.available is False
    assert detail.cta_overlay.boxes == []
