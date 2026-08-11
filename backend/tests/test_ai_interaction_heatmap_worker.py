"""AI etkilesim isi haritasi worker/lifecycle testleri (DB-destekli).

`test_ai_pipeline_worker.py` ile AYNI fixture deseni: rollback eden `session`
YERINE gercek commit eden `maker` (async_sessionmaker) + test sonu TRUNCATE -
cunku worker claim -> provider -> persist akisi ayri commit'lenmis
transaction'lar gerektirir.

Kapsam: claim/provider/persist basari yolu, aday YOK (bos sonuc, provider
CAGRILMAZ), fingerprint dedup (ayni fingerprint icin provider IKINCI kez
cagrilmaz), retryable hata -> bounded retry, gecersiz cikti -> terminal FAILED,
Chip settlement (tumu basari -> consume; biri basarisiz -> release).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Organization,
    PageAnalysis,
    Project,
    SimulationRun,
    TestDefinition,
    TestVariant,
)
from app.models.billing import ChipReservationStatus
from app.models.interaction_heatmap import InteractionHeatmap, InteractionHeatmapStatus
from app.models.page_analysis import PageAnalysisSourceKind, PageAnalysisStatus
from app.models.simulations import SimulationStatus
from app.services import chip_ledger
from app.services.ai_interaction_heatmap import AIHotspotSelection, AIInteractionOutput
from app.services.ai_interaction_heatmap import worker as hm_worker
from app.services.ai_interaction_heatmap.openai_selector import (
    MockInteractionHeatmapSelector,
    SelectorResult,
)
from app.services.ai_pipeline.provider_errors import AIProviderTransportError
from tests.conftest import TEST_DATABASE_URL, _truncate_all_tables

pytestmark = pytest.mark.integration


_ELEMENT_BOXES = [
    {"role": "button", "label": "Uye ol", "interaction_kind": "button", "x": 100, "y": 40, "width": 120, "height": 36},
    {"role": "link", "label": "Cok Satanlar", "interaction_kind": "content_link", "x": 100, "y": 400, "width": 140, "height": 24},
]


@pytest.fixture
async def maker(test_engine):
    session_maker = async_sessionmaker(test_engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await _truncate_all_tables(TEST_DATABASE_URL)


async def _make_org(maker) -> uuid.UUID:
    """Org'u GERCEK commit eden bir maker session'inda olusturur - rollback eden
    `organization` fixture'i ayri worker session'larindan gorunmez."""

    async with maker() as session:
        org = Organization(name="HM Org", slug=f"hm-org-{uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.commit()
        return org.id


class CountingSelector:
    """Mock seciciyi saran; `select` cagri sayisini tutar (dedup dogrulamasi)."""

    def __init__(self) -> None:
        self._inner = MockInteractionHeatmapSelector()
        self.provider_name = self._inner.provider_name
        self.model_name = self._inner.model_name
        self.method = self._inner.method
        self.is_mock = True
        self.calls = 0

    async def select(self, **kwargs):
        self.calls += 1
        return await self._inner.select(**kwargs)


class InvalidOutputSelector:
    provider_name = "mock"
    model_name = "deterministic-dom-visual-ranker"
    method = "dom_visual_feature_ranking"
    is_mock = True

    async def select(
        self,
        *,
        candidates,
        target_task,
        target_audience,
        persona_distribution,
        confirmed_candidate_id,
        screenshot_data,
        screenshot_content_type,
    ):
        return SelectorResult(
            output=AIInteractionOutput(
                summary="x",
                hotspots=[
                    AIHotspotSelection(
                        candidate_id="candidate-999",
                        score=0.9,
                        confidence="high",
                        reason="uydurma id",
                        task_relevance="direct",
                    )
                ],
                unmatched_task_warning=None,
            ),
            provider_name=self.provider_name,
            model_name=self.model_name,
            method=self.method,
        )


class TransientFailSelector:
    provider_name = "openai"
    model_name = "gpt-test"
    method = "openai_candidate_selection"
    is_mock = False

    async def select(self, **kwargs):
        raise AIProviderTransportError("gecici test hatasi")


async def _seed_run(
    maker,
    organization_id: uuid.UUID,
    *,
    features: dict | None = None,
    target_task: str = "Yeni hesap olustur",
    content_sha256: str = "sha-abc",
    launch_run_id: uuid.UUID | None = None,
    heatmap_reservation_id: uuid.UUID,
    status: SimulationStatus = SimulationStatus.SUCCEEDED,
) -> uuid.UUID:
    async with maker() as session:
        project = Project(organization_id=organization_id, name=f"P {uuid.uuid4().hex[:6]}")
        session.add(project)
        await session.flush()
        definition = TestDefinition(
            organization_id=organization_id, project_id=project.id, name=f"D {uuid.uuid4().hex[:6]}"
        )
        session.add(definition)
        await session.flush()
        variant = TestVariant(
            organization_id=organization_id,
            test_definition_id=definition.id,
            name=f"V {uuid.uuid4().hex[:6]}",
            config={"role": "primary"},
        )
        session.add(variant)
        await session.flush()
        analysis = PageAnalysis(
            organization_id=organization_id,
            source_kind=PageAnalysisSourceKind.URL,
            url="https://example.com",
            status=PageAnalysisStatus.SUCCEEDED,
            features={"element_boxes": _ELEMENT_BOXES} if features is None else features,
            image_width=1280,
            image_height=1600,
            content_sha256=content_sha256,
        )
        session.add(analysis)
        await session.flush()
        run = SimulationRun(
            organization_id=organization_id,
            test_variant_id=variant.id,
            status=status,
            deterministic_seed=uuid.uuid4().int & ((1 << 63) - 1),
            model_version="engine",
            input_snapshot={
                "modules": ["ai_interaction_heatmap"],
                "target_task": target_task,
                "target_audience": "Yeni kullanicilar",
            },
            launch_run_id=launch_run_id,
            heatmap_chip_reservation_id=heatmap_reservation_id,
            page_analysis_id=analysis.id,
        )
        session.add(run)
        await session.flush()
        await session.commit()
        return run.id


async def _reserve(maker, organization_id: uuid.UUID, *, launch_run_id: uuid.UUID) -> uuid.UUID:
    async with maker() as session:
        await chip_ledger.credit(session, organization_id, 1000, "kredi")
        reservation = await chip_ledger.reserve_chips(
            session, organization_id, 30, "heatmap", run_id=launch_run_id
        )
        await session.commit()
        return reservation.id


async def _load_heatmap(maker, run_id: uuid.UUID) -> InteractionHeatmap | None:
    async with maker() as session:
        return (
            await session.execute(
                select(InteractionHeatmap).where(InteractionHeatmap.simulation_run_id == run_id)
            )
        ).scalar_one_or_none()


# --- Basari yolu -------------------------------------------------------------


async def test_process_creates_succeeded_heatmap_with_hotspots(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    run_id = await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)

    selector = CountingSelector()
    result = await hm_worker.process_one_interaction_heatmap(maker, selector=selector)

    assert result.claimed is True
    assert result.outcome == "succeeded"
    assert selector.calls == 1
    hm = await _load_heatmap(maker, run_id)
    assert hm is not None
    assert hm.status == InteractionHeatmapStatus.SUCCEEDED
    assert hm.result is not None
    hotspots = hm.result["hotspots"]
    assert hotspots and hotspots[0]["label"] == "Uye ol"
    # AI koordinat uretmedi: koordinatlar gercek aday kaydindan geldi.
    assert 0.0 <= hotspots[0]["x"] <= 1.0
    assert "göz takibi verisi değildir" in hm.result["disclaimer"]


async def test_no_candidates_yields_empty_result_without_provider_call(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    run_id = await _seed_run(
        maker, org_id, features={"element_boxes": []}, launch_run_id=launch,
        heatmap_reservation_id=reservation_id,
    )
    selector = CountingSelector()
    result = await hm_worker.process_one_interaction_heatmap(maker, selector=selector)
    assert result.outcome == "succeeded_empty"
    assert selector.calls == 0  # aday yok -> provider CAGRILMAZ
    hm = await _load_heatmap(maker, run_id)
    assert hm.status == InteractionHeatmapStatus.SUCCEEDED
    assert hm.result["hotspots"] == []
    assert hm.result["unmatched_task_warning"]


async def test_same_fingerprint_does_not_call_provider_twice(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    # Iki AYRI run, birebir AYNI kanit (ayni features + content_sha256 + task) ->
    # ayni fingerprint.
    run_a = await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)
    run_b = await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)

    selector = CountingSelector()
    await hm_worker.process_one_interaction_heatmap(maker, selector=selector)
    await hm_worker.process_one_interaction_heatmap(maker, selector=selector)

    assert selector.calls == 1  # ikinci run sonucu REUSE etti
    hm_a = await _load_heatmap(maker, run_a)
    hm_b = await _load_heatmap(maker, run_b)
    assert hm_a.status == hm_b.status == InteractionHeatmapStatus.SUCCEEDED
    assert hm_a.result["hotspots"] == hm_b.result["hotspots"]


async def test_invalid_output_is_terminal_failed(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    run_id = await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)

    result = await hm_worker.process_one_interaction_heatmap(maker, selector=InvalidOutputSelector())
    assert result.outcome == "failed"
    assert result.error_code == hm_worker.ERROR_CODE_OUTPUT_VALIDATION_FAILED
    hm = await _load_heatmap(maker, run_id)
    assert hm.status == InteractionHeatmapStatus.FAILED
    assert hm.result is None  # gecersiz cikti HARITAYA donusturulmez


async def test_retryable_error_requeues_until_max_then_fails(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    run_id = await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)

    selector = TransientFailSelector()
    # MAX_ATTEMPTS kadar retryable hata: her biri QUEUED'a geri, sonuncusu FAILED.
    outcomes = []
    for _ in range(hm_worker.MAX_ATTEMPTS):
        outcomes.append(await hm_worker.process_one_interaction_heatmap(maker, selector=selector))
    assert outcomes[0].outcome == "requeued"
    assert outcomes[-1].outcome == "failed"
    hm = await _load_heatmap(maker, run_id)
    assert hm.status == InteractionHeatmapStatus.FAILED
    assert hm.attempt_count == hm_worker.MAX_ATTEMPTS


async def test_provider_missing_is_noop(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    run_id = await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)
    # ctx'te secici yok -> hicbir sey claim edilmez, job olusmaz (QUEUED kalir).
    out = await hm_worker.run_interaction_heatmap_queue_cycle({})
    assert out["status"] == "disabled"
    hm = await _load_heatmap(maker, run_id)
    assert hm is None


# --- Settlement --------------------------------------------------------------


async def _reservation_status(maker, reservation_id: uuid.UUID) -> ChipReservationStatus:
    async with maker() as session:
        from app.models.billing import ChipReservation

        reservation = await session.get(ChipReservation, reservation_id)
        return reservation.status


async def test_settlement_consumes_when_all_succeeded(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)
    selector = MockInteractionHeatmapSelector()
    await hm_worker.process_one_interaction_heatmap(maker, selector=selector)

    result = await hm_worker.settle_interaction_heatmap_reservations(maker)
    assert result.groups_consumed == 1
    assert await _reservation_status(maker, reservation_id) == ChipReservationStatus.CONSUMED


async def test_settlement_releases_when_job_failed(maker):
    org_id = await _make_org(maker)
    launch = uuid.uuid4()
    reservation_id = await _reserve(maker, org_id, launch_run_id=launch)
    await _seed_run(maker, org_id, launch_run_id=launch, heatmap_reservation_id=reservation_id)
    # gecersiz cikti -> FAILED
    await hm_worker.process_one_interaction_heatmap(maker, selector=InvalidOutputSelector())

    result = await hm_worker.settle_interaction_heatmap_reservations(maker)
    assert result.groups_released == 1
    assert await _reservation_status(maker, reservation_id) == ChipReservationStatus.RELEASED
