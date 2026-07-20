"""Simulasyon raporu API'si: tamamlanmis bir run'in degismez (immutable) snapshot'indan
salt-okunur bir rapor gorunumu uretir.

Onemli ilke: bu router hicbir run'i yeniden calistirmaz/yeniden hesaplamaz. Butun
metrikler `Report.content` (bkz. app.services.simulation_worker.process_run - basarili
bir calistirma sonrasi tek seferlik olusturulan, bir daha guncellenmeyen JSON kopyasi)
uzerinden okunur; A/B karsilastirmasi bile iki ayri (zaten hesaplanmis) sonuc
sozlugu uzerinde calisan saf bir fonksiyondur (bkz. app.engine.baseline.
compare_baseline_results). Kritik bulgular da zaten depolanmis nokta tahminleri
uzerinde sabit esiklerle turetilir; yeni bir rastgelelik/model cagrisi yoktur.

Tenant izolasyonu: Report -> SimulationRun -> TestVariant -> TestDefinition -> Project
zincirinin her halkasi ayri ayri `organization_id` ile dogrulanir (bkz.
`_get_owned_report_context`), diger router'larla ayni "bulunamadi (404)" deseniyle
(bkz. app.routers.simulations._get_owned_run).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_organization_id
from app.engine.baseline import compare_baseline_results
from app.models.page_analysis import PageAnalysis, PageAnalysisStatus
from app.models.projects import Project
from app.models.reports import Report
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tests import TestDefinition, TestVariant

router = APIRouter(prefix="/api/reports", tags=["reports"])

NOT_REAL_USER_DATA_LABEL = "Gerçek kullanıcı verisi değildir"
SYNTHETIC_ATTENTION_LABEL = "Sentetik dikkat tahmini"

# `synthetic_attention_estimate` motorunun urettigi 5 sabit bolge anahtari
# (bkz. app.engine.advanced_modules._ATTENTION_REGIONS) - gorsel isi
# haritasinin ekran goruntusu uzerine yerlesebilmesi icin, analyzer'in
# `layout_regions` cikisindaki (bkz. analyzer/app/schemas.py) AYNI 5 anahtarla
# eslesmesi gerekir.
HEATMAP_REGION_KEYS = (
    "ust_navigasyon",
    "hero_baslik",
    "birincil_cta",
    "govde_metni",
    "alt_bilgi",
)

SCREENSHOT_MISSING_REASON = (
    "Bu URL icin daha once alinmis, saklama suresi dolmamis bir sayfa "
    "ekran goruntusu bulunamadi; gorsel katman gosterilemiyor, erisilebilir "
    "tablo gorunumu kullaniliyor. Gorsel katmani gormek icin once bu URL "
    "icin bir sayfa analizi (ekran goruntusu) calistirin."
)

COORDINATES_MISSING_REASON = (
    "Sayfadaki bazi yerlesim bolgelerinin (navigasyon/baslik/CTA/icerik/alt "
    "bilgi) konum verisi tespit edilemedi; rastgele koordinat uretilmez, "
    "bu nedenle gorsel katman yerine erisilebilir tablo gorunumu kullaniliyor."
)

# Bir segmentin "kucuk sentetik ornek" sayilmasi icin esik (yaygin istatistiksel
# esneklik kurali); bu bir anlamlilik testi degildir, yalnizca UI'da bir uyari
# tetiklemek icin kullanilir.
SMALL_SAMPLE_THRESHOLD = 30

PERSONA_SEGMENT_NOTE = (
    "Küçük sentetik örneklemli segmentlerde (n<30) belirsizlik daha yüksektir. "
    "Bu sayılar sentetik persona örneğidir; gerçek kullanıcı örneklemi değildir."
)


# --- Yanit modelleri -----------------------------------------------------------


class ReportListItemResponse(BaseModel):
    id: uuid.UUID
    title: str
    simulation_run_id: uuid.UUID
    project_id: uuid.UUID
    project_name: str
    test_definition_id: uuid.UUID
    test_definition_name: str
    variant_name: str
    variant_role: str
    model_version: str
    calibration_status: str
    created_at: datetime


class ReportInfoBox(BaseModel):
    not_real_user_data_label: str
    model_version: str
    calibration_status: str
    generated_at: datetime
    deterministic_seed: int
    rules_version: str | None
    fixture_version: str | None
    input_summary: dict


class PersonaSegmentSummary(BaseModel):
    key: str
    label: str
    count: int
    share: float
    small_sample_warning: bool


class CriticalFinding(BaseModel):
    key: str
    severity: str
    text: str


class HeatmapRegionBox(BaseModel):
    x_pct: float
    y_pct: float
    width_pct: float
    height_pct: float


class HeatmapRegion(BaseModel):
    key: str
    label: str
    score: float
    level: str
    box: HeatmapRegionBox | None = None


class HeatmapSection(BaseModel):
    available: bool
    label: str
    grid: list[dict] | None
    disclaimer: str | None = None
    regions: list[HeatmapRegion] | None = None
    screenshot_url: str | None = None
    coordinates_available: bool = False
    coordinates_unavailable_reason: str | None = None


class CampaignCtaSection(BaseModel):
    ctas: list[dict]
    message_clarity_findings: list[dict]
    disclaimer: str


class NetworkDeviceSection(BaseModel):
    profiles: list[dict]
    error_rate: float
    disclaimer: str


class AccessibleChartSummary(BaseModel):
    chart_key: str
    text: str


class ReportDetailResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    project_id: uuid.UUID
    project_name: str
    test_definition_id: uuid.UUID
    test_definition_name: str
    variant_name: str
    variant_role: str
    info_box: ReportInfoBox
    metrics: dict
    disclaimer: str
    methodology_reference: str
    ab_comparison: dict | None
    persona_segments: list[PersonaSegmentSummary]
    persona_segment_note: str
    critical_findings: list[CriticalFinding]
    heatmap: HeatmapSection
    campaign_cta: CampaignCtaSection | None
    network_device: NetworkDeviceSection | None
    accessible_chart_summaries: list[AccessibleChartSummary]
    export_json_url: str
    export_csv_url: str


# --- Tenant-guvenli yukleme ----------------------------------------------------


class _ReportContext:
    def __init__(
        self,
        report: Report,
        run: SimulationRun,
        variant: TestVariant,
        definition: TestDefinition,
        project: Project,
    ) -> None:
        self.report = report
        self.run = run
        self.variant = variant
        self.definition = definition
        self.project = project


async def _get_owned_report_context(
    session: AsyncSession, organization_id: uuid.UUID, report_id: uuid.UUID
) -> _ReportContext:
    report = await session.get(Report, report_id)
    if report is None or report.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Rapor bulunamadi")

    run = await session.get(SimulationRun, report.simulation_run_id)
    variant = await session.get(TestVariant, run.test_variant_id) if run else None
    definition = await session.get(TestDefinition, variant.test_definition_id) if variant else None
    project = await session.get(Project, definition.project_id) if definition else None

    if (
        run is None
        or variant is None
        or definition is None
        or project is None
        or run.organization_id != organization_id
        or variant.organization_id != organization_id
        or definition.organization_id != organization_id
        or project.organization_id != organization_id
    ):
        raise HTTPException(status_code=404, detail="Rapor bulunamadi")

    return _ReportContext(report, run, variant, definition, project)


# --- Turetilmis bolumler (yalnizca depolanmis veriden, yeniden hesaplama yok) --


def _build_persona_segments(run: SimulationRun) -> list[PersonaSegmentSummary]:
    persona_sample = (run.input_snapshot or {}).get("persona_sample") or {}
    segments = persona_sample.get("segments") or []
    return [
        PersonaSegmentSummary(
            key=segment["key"],
            label=segment["label"],
            count=segment["count"],
            share=segment["share"],
            small_sample_warning=segment["count"] < SMALL_SAMPLE_THRESHOLD,
        )
        for segment in segments
    ]


def _score_level(score: float) -> str:
    """Skoru dusuk/orta/yuksek etiketine cevirir (bkz. docs/methodology.md).

    5 bolgenin paylari toplamda 1'e esittir (ortalama pay 0.2); esikler bu
    ortalama etrafinda simetrik, sabit ve dokumante edilmis bir kesim
    noktasidir - bir anlamlilik testi degildir (bkz. SMALL_SAMPLE_THRESHOLD
    ile ayni yaklasim).
    """

    if score < 0.15:
        return "low"
    if score > 0.25:
        return "high"
    return "medium"


async def _find_linked_screenshot_analysis(
    session: AsyncSession, organization_id: uuid.UUID, url: str | None
) -> PageAnalysis | None:
    """Ayni organizasyonda, ayni URL icin daha once basariyla tamamlanmis ve
    ekran goruntusu saklama suresi henuz dolmamis (bkz.
    app.services.page_analysis.purge_expired_screenshots - suresi dolan
    kayitlarda `screenshot_data` NULL'a cekilir) en guncel `PageAnalysis`
    kaydini bulur. Baska bir organizasyonun ekran goruntusune ASLA erisilmez
    (`organization_id` filtresi burada da uygulanir, endpoint tarafinda da
    ayrica rapor sahipligi dogrulanir).
    """

    if not url:
        return None

    result = await session.execute(
        select(PageAnalysis)
        .where(
            PageAnalysis.organization_id == organization_id,
            PageAnalysis.url == url,
            PageAnalysis.status == PageAnalysisStatus.SUCCEEDED,
            PageAnalysis.screenshot_data.is_not(None),
        )
        .order_by(PageAnalysis.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def _build_heatmap(
    session: AsyncSession,
    organization_id: uuid.UUID,
    report_id: uuid.UUID,
    run: SimulationRun,
    content: dict,
) -> HeatmapSection:
    # `attention_grid`, yalnizca sihirbazda `synthetic_attention_estimate`
    # modulu secilmisse `app.services.simulation_worker` tarafindan
    # doldurulur (bkz. app.engine.advanced_modules.run_synthetic_attention_estimate).
    # Veri yoksa sahte bir gorsel/grid ASLA uretilmez.
    grid = content.get("attention_grid")
    attention_module = (content.get("modules") or {}).get("synthetic_attention_estimate")
    disclaimer = attention_module.get("disclaimer") if attention_module else None
    if not grid:
        return HeatmapSection(
            available=False, label=SYNTHETIC_ATTENTION_LABEL, grid=None, disclaimer=disclaimer
        )

    regions = [
        HeatmapRegion(
            key=cell["key"], label=cell["label"], score=cell["score"], level=_score_level(cell["score"])
        )
        for cell in grid
    ]

    url = (run.input_snapshot or {}).get("url")
    analysis = await _find_linked_screenshot_analysis(session, organization_id, url)

    screenshot_url: str | None = None
    coordinates_available = False
    coordinates_unavailable_reason: str | None = None

    if analysis is None:
        coordinates_unavailable_reason = SCREENSHOT_MISSING_REASON
    else:
        layout_regions = (analysis.features or {}).get("layout_regions") or {}
        missing = [key for key in HEATMAP_REGION_KEYS if not layout_regions.get(key)]
        if missing:
            coordinates_unavailable_reason = COORDINATES_MISSING_REASON
        else:
            coordinates_available = True
            screenshot_url = f"/api/reports/{report_id}/heatmap-screenshot"
            for region in regions:
                box = layout_regions.get(region.key)
                if box:
                    region.box = HeatmapRegionBox.model_validate(box)

    return HeatmapSection(
        available=True,
        label=SYNTHETIC_ATTENTION_LABEL,
        grid=grid,
        disclaimer=disclaimer,
        regions=regions,
        screenshot_url=screenshot_url,
        coordinates_available=coordinates_available,
        coordinates_unavailable_reason=coordinates_unavailable_reason,
    )


def _build_campaign_cta(content: dict) -> CampaignCtaSection | None:
    module = (content.get("modules") or {}).get("campaign_cta_test")
    if module is None:
        return None
    return CampaignCtaSection(
        ctas=module["ctas"],
        message_clarity_findings=module["message_clarity_findings"],
        disclaimer=module["disclaimer"],
    )


def _build_network_device(content: dict) -> NetworkDeviceSection | None:
    module = (content.get("modules") or {}).get("network_device_test")
    if module is None:
        return None
    return NetworkDeviceSection(
        profiles=module["profiles"],
        error_rate=module["error_rate"],
        disclaimer=module["disclaimer"],
    )


def _build_critical_findings(metrics: dict) -> list[CriticalFinding]:
    findings: list[CriticalFinding] = []

    contrast = metrics["contrast_check"]
    if not contrast["pass"]:
        findings.append(
            CriticalFinding(
                key="contrast_below_threshold",
                severity="warning",
                text=(
                    f"Kontrast oranı WCAG AA eşiğinin altında (ortalama {contrast['avg_ratio']}, "
                    f"eşik {contrast['threshold']})."
                ),
            )
        )

    completion = metrics["task_completion_probability"]["point_estimate"]
    if completion < 0.5:
        findings.append(
            CriticalFinding(
                key="low_task_completion",
                severity="warning",
                text=f"Sentetik görev tamamlama tahmini düşük (%{round(completion * 100)}).",
            )
        )

    abandonment = metrics["abandonment_probability"]["point_estimate"]
    if abandonment > 0.4:
        findings.append(
            CriticalFinding(
                key="high_abandonment",
                severity="warning",
                text=f"Sentetik terk (abandonment) tahmini yüksek (%{round(abandonment * 100)}).",
            )
        )

    misclick = metrics["misclick_probability"]["point_estimate"]
    if misclick > 0.2:
        findings.append(
            CriticalFinding(
                key="high_misclick",
                severity="warning",
                text=f"Sentetik yanlış tıklama tahmini yüksek (%{round(misclick * 100)}).",
            )
        )

    readability = metrics["readability_score"]
    if readability < 50:
        findings.append(
            CriticalFinding(
                key="low_readability",
                severity="warning",
                text=f"Okunabilirlik skoru düşük ({readability}/100).",
            )
        )

    if not findings:
        findings.append(
            CriticalFinding(
                key="no_threshold_triggered",
                severity="info",
                text="Tanımlı eşik tabanlı kritik bulgu tetiklenmedi.",
            )
        )

    return findings


def _pct(value: float) -> str:
    return f"%{round(value * 100)}"


def _build_accessible_chart_summaries(metrics: dict) -> list[AccessibleChartSummary]:
    duration = metrics["task_duration_seconds"]
    return [
        AccessibleChartSummary(
            chart_key="task_completion_probability",
            text=(
                "Görev tamamlama olasılığı: nokta tahmini "
                f"{_pct(metrics['task_completion_probability']['point_estimate'])}, belirsizlik "
                f"aralığı {_pct(metrics['task_completion_probability']['low'])}–"
                f"{_pct(metrics['task_completion_probability']['high'])}."
            ),
        ),
        AccessibleChartSummary(
            chart_key="misclick_probability",
            text=(
                "Yanlış tıklama olasılığı: nokta tahmini "
                f"{_pct(metrics['misclick_probability']['point_estimate'])}, belirsizlik "
                f"aralığı {_pct(metrics['misclick_probability']['low'])}–"
                f"{_pct(metrics['misclick_probability']['high'])}."
            ),
        ),
        AccessibleChartSummary(
            chart_key="abandonment_probability",
            text=(
                "Terk olasılığı: nokta tahmini "
                f"{_pct(metrics['abandonment_probability']['point_estimate'])}, belirsizlik "
                f"aralığı {_pct(metrics['abandonment_probability']['low'])}–"
                f"{_pct(metrics['abandonment_probability']['high'])}."
            ),
        ),
        AccessibleChartSummary(
            chart_key="task_duration_seconds",
            text=(
                f"Tahmini görev süresi: nokta tahmini {round(duration['point_estimate'])} sn, "
                f"p10–p90 {round(duration['p10'])}–{round(duration['p90'])} sn."
            ),
        ),
    ]


async def _find_ab_sibling_report(
    session: AsyncSession, organization_id: uuid.UUID, ctx: _ReportContext
) -> tuple[SimulationRun, TestVariant] | None:
    """Ayni test tanimindaki, tam olarak iki varyantli bir A/B kurulumunda
    diger varyantin en son basarili calistirmasini + raporunu bulur.

    Karsilastirma daima iki ayri, zaten tamamlanmis run'in `Report.content`
    degerleri uzerinden hesaplanir (bkz. modul dokstring'i); hicbir run
    burada yeniden calistirilmaz.
    """

    variants_result = await session.execute(
        select(TestVariant)
        .where(TestVariant.test_definition_id == ctx.definition.id)
        .order_by(TestVariant.created_at)
    )
    variants = list(variants_result.scalars().all())
    if len(variants) != 2:
        return None

    sibling_variant = next((v for v in variants if v.id != ctx.variant.id), None)
    if sibling_variant is None:
        return None

    sibling_run_result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.test_variant_id == sibling_variant.id,
            SimulationRun.status == SimulationStatus.SUCCEEDED,
        )
        .order_by(SimulationRun.created_at.desc())
        .limit(1)
    )
    sibling_run = sibling_run_result.scalar_one_or_none()
    if sibling_run is None or sibling_run.result is None:
        return None

    sibling_report_result = await session.execute(
        select(Report)
        .where(Report.simulation_run_id == sibling_run.id)
        .order_by(Report.created_at.desc())
        .limit(1)
    )
    if sibling_report_result.scalar_one_or_none() is None:
        return None

    return sibling_run, sibling_variant


async def _build_detail_response(
    session: AsyncSession, organization_id: uuid.UUID, ctx: _ReportContext
) -> ReportDetailResponse:
    content = ctx.report.content
    metrics = content["metrics"]
    input_snapshot = ctx.run.input_snapshot or {}

    ab_comparison: dict | None = None
    sibling = await _find_ab_sibling_report(session, organization_id, ctx)
    if sibling is not None:
        sibling_run, sibling_variant = sibling
        assert sibling_run.result is not None
        this_role = input_snapshot.get("role", "primary")
        this_is_a = this_role == "existing" or this_role == "primary"
        result_a = content if this_is_a else sibling_run.result
        result_b = sibling_run.result if this_is_a else content
        persona_count_a = (
            input_snapshot.get("persona_count", 0)
            if this_is_a
            else sibling_run.input_snapshot.get("persona_count", 0)
        )
        persona_count_b = (
            sibling_run.input_snapshot.get("persona_count", 0)
            if this_is_a
            else input_snapshot.get("persona_count", 0)
        )
        ab_comparison = compare_baseline_results(
            result_a,
            result_b,
            persona_count_a=persona_count_a,
            persona_count_b=persona_count_b,
        )
        ab_comparison["this_variant_role"] = "variant_a" if this_is_a else "variant_b"
        ab_comparison["sibling_variant_name"] = sibling_variant.name

    return ReportDetailResponse(
        id=ctx.report.id,
        title=ctx.report.title,
        created_at=ctx.report.created_at,
        project_id=ctx.project.id,
        project_name=ctx.project.name,
        test_definition_id=ctx.definition.id,
        test_definition_name=ctx.definition.name,
        variant_name=ctx.variant.name,
        variant_role=input_snapshot.get("role", "primary"),
        info_box=ReportInfoBox(
            not_real_user_data_label=NOT_REAL_USER_DATA_LABEL,
            model_version=ctx.run.model_version,
            calibration_status=ctx.run.calibration_status.value,
            generated_at=ctx.report.created_at,
            deterministic_seed=ctx.run.deterministic_seed,
            rules_version=ctx.run.rules_version,
            fixture_version=ctx.run.fixture_version,
            input_summary={
                "url": input_snapshot.get("url"),
                "wizard_test_type": input_snapshot.get("wizard_test_type"),
                "persona_count": input_snapshot.get("persona_count"),
                "target_audience": input_snapshot.get("target_audience"),
            },
        ),
        metrics=metrics,
        disclaimer=content.get("disclaimer", ""),
        methodology_reference=content.get("methodology_reference", "docs/methodology.md"),
        ab_comparison=ab_comparison,
        persona_segments=_build_persona_segments(ctx.run),
        persona_segment_note=PERSONA_SEGMENT_NOTE,
        critical_findings=_build_critical_findings(metrics),
        heatmap=await _build_heatmap(session, organization_id, ctx.report.id, ctx.run, content),
        campaign_cta=_build_campaign_cta(content),
        network_device=_build_network_device(content),
        accessible_chart_summaries=_build_accessible_chart_summaries(metrics),
        export_json_url=f"/api/reports/{ctx.report.id}/export.json",
        export_csv_url=f"/api/reports/{ctx.report.id}/export.csv",
    )


# --- Uc noktalar ----------------------------------------------------------------


@router.get("", response_model=list[ReportListItemResponse])
async def list_reports(
    project_id: uuid.UUID | None = None,
    test_definition_id: uuid.UUID | None = None,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> list[ReportListItemResponse]:
    """Organizasyonun raporlarini (istege bagli proje/test tanimi filtresiyle) listeler."""

    query = (
        select(Report, SimulationRun, TestVariant, TestDefinition, Project)
        .join(SimulationRun, SimulationRun.id == Report.simulation_run_id)
        .join(TestVariant, TestVariant.id == SimulationRun.test_variant_id)
        .join(TestDefinition, TestDefinition.id == TestVariant.test_definition_id)
        .join(Project, Project.id == TestDefinition.project_id)
        .where(Report.organization_id == organization_id)
    )
    if project_id is not None:
        query = query.where(Project.id == project_id)
    if test_definition_id is not None:
        query = query.where(TestDefinition.id == test_definition_id)
    query = query.order_by(Report.created_at.desc())

    result = await session.execute(query)
    rows = result.all()
    await session.commit()

    return [
        ReportListItemResponse(
            id=report.id,
            title=report.title,
            simulation_run_id=run.id,
            project_id=project.id,
            project_name=project.name,
            test_definition_id=definition.id,
            test_definition_name=definition.name,
            variant_name=variant.name,
            variant_role=(run.input_snapshot or {}).get("role", "primary"),
            model_version=run.model_version,
            calibration_status=run.calibration_status.value,
            created_at=report.created_at,
        )
        for report, run, variant, definition, project in rows
    ]


@router.get("/{report_id}", response_model=ReportDetailResponse)
async def get_report(
    report_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> ReportDetailResponse:
    ctx = await _get_owned_report_context(session, organization_id, report_id)
    response = await _build_detail_response(session, organization_id, ctx)
    await session.commit()
    return response


@router.get("/{report_id}/heatmap-screenshot")
async def get_report_heatmap_screenshot(
    report_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Sentetik dikkat isi haritasinin uzerine bindirilecegi, daha once
    Playwright analyzer tarafindan alinmis GUVENLI ekran goruntusunu doner.

    Erisim, once bu rapor uzerinden tenant sahipligi (`_get_owned_report_context`
    - Report -> SimulationRun -> TestVariant -> TestDefinition -> Project
    zincirinin her halkasi), sonra ayrica eslesen `PageAnalysis` kaydinin
    kendi `organization_id`'si ile CIFT dogrulanir; baska bir organizasyonun
    ekran goruntusune bu yoldan hicbir sekilde erisilemez. Frontend, harici
    (analyzer/uçuk depolama) bir URL'yi ASLA dogrudan gorsel kaynagi olarak
    kullanmaz - yalnizca bu yetkilendirilmis uc noktayi cagirir.
    """

    ctx = await _get_owned_report_context(session, organization_id, report_id)
    url = (ctx.run.input_snapshot or {}).get("url")
    analysis = await _find_linked_screenshot_analysis(session, organization_id, url)
    await session.commit()

    if analysis is None or analysis.screenshot_data is None:
        raise HTTPException(
            status_code=404, detail="Ekran goruntusu mevcut degil veya saklama suresi doldu"
        )

    return Response(content=analysis.screenshot_data, media_type="image/png")


@router.get("/{report_id}/export.json")
async def export_report_json(
    report_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ctx = await _get_owned_report_context(session, organization_id, report_id)
    response = await _build_detail_response(session, organization_id, ctx)
    await session.commit()

    return Response(
        content=response.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="rapor-{report_id}.json"'},
    )


@router.get("/{report_id}/export.csv")
async def export_report_csv(
    report_id: uuid.UUID,
    organization_id: uuid.UUID = Depends(get_organization_id),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ctx = await _get_owned_report_context(session, organization_id, report_id)
    detail = await _build_detail_response(session, organization_id, ctx)
    await session.commit()

    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(["bolum", "anahtar", "nokta_tahmini", "dusuk", "yuksek", "birim"])
    metrics = detail.metrics
    writer.writerow(
        [
            "metrik",
            "task_completion_probability",
            metrics["task_completion_probability"]["point_estimate"],
            metrics["task_completion_probability"]["low"],
            metrics["task_completion_probability"]["high"],
            "olasilik",
        ]
    )
    writer.writerow(
        [
            "metrik",
            "task_duration_seconds",
            metrics["task_duration_seconds"]["point_estimate"],
            metrics["task_duration_seconds"]["low"],
            metrics["task_duration_seconds"]["high"],
            "saniye",
        ]
    )
    writer.writerow(
        [
            "metrik",
            "misclick_probability",
            metrics["misclick_probability"]["point_estimate"],
            metrics["misclick_probability"]["low"],
            metrics["misclick_probability"]["high"],
            "olasilik",
        ]
    )
    writer.writerow(
        [
            "metrik",
            "abandonment_probability",
            metrics["abandonment_probability"]["point_estimate"],
            metrics["abandonment_probability"]["low"],
            metrics["abandonment_probability"]["high"],
            "olasilik",
        ]
    )
    writer.writerow(["metrik", "readability_score", metrics["readability_score"], "", "", "skor"])
    writer.writerow(
        [
            "metrik",
            "contrast_avg_ratio",
            metrics["contrast_check"]["avg_ratio"],
            "",
            metrics["contrast_check"]["threshold"],
            "oran",
        ]
    )

    writer.writerow([])
    writer.writerow(["persona_segment", "anahtar", "etiket", "n", "pay", "kucuk_ornek_uyarisi"])
    for segment in detail.persona_segments:
        writer.writerow(
            [
                "persona_segment",
                segment.key,
                segment.label,
                segment.count,
                segment.share,
                segment.small_sample_warning,
            ]
        )

    writer.writerow([])
    writer.writerow(["kritik_bulgu", "anahtar", "onem", "metin"])
    for finding in detail.critical_findings:
        writer.writerow(["kritik_bulgu", finding.key, finding.severity, finding.text])

    if detail.campaign_cta is not None:
        writer.writerow([])
        writer.writerow(["kampanya_cta", "anahtar", "sira", "ust_ekranda", "tiklama_nokta_tahmini"])
        for cta in detail.campaign_cta.ctas:
            writer.writerow(
                [
                    "kampanya_cta",
                    cta["key"],
                    cta["rank"],
                    cta["above_fold"],
                    cta["click_probability"]["point_estimate"],
                ]
            )

    if detail.network_device is not None:
        writer.writerow([])
        writer.writerow(["ag_cihaz_profili", "anahtar", "cihaz", "ag", "basarili", "yukleme_ms"])
        for profile in detail.network_device.profiles:
            timings = profile.get("timings") or {}
            writer.writerow(
                [
                    "ag_cihaz_profili",
                    profile["profile_key"],
                    profile["device_label"],
                    profile["network_label"],
                    profile["succeeded"],
                    timings.get("total_navigation_ms", ""),
                ]
            )
        writer.writerow(["ag_cihaz_hata_orani", "", "", "", "", detail.network_device.error_rate])

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="rapor-{report_id}.csv"'},
    )
