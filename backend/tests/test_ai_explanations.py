"""AI destekli açıklama katmanı için testler.

Kapsam: sağlayıcı kapalıyken (varsayılan `AI_PROVIDER=none`) tam
çalışırlık, çıktı şema doğrulaması, her bulgunun bir `metric_id`'ye
dayanma zorunluluğu, yasaklı ifadelerin reddi, girdiye ham/hassas veri
sızmaması (redaksiyon), zaman aşımında güvenli şablona düşme ve
simülasyonda bulunmayan ("uydurma") bir metriğe atıf yapan çıktının asla
dışarı sızmaması.

Router seviyesindeki testler uc noktalari HTTP uzerinden degil dogrudan
cagirir. `test_engine`, `session` ve `organization` fixture'lari artik
tests/conftest.py'de paylasilan altyapidan gelir (izole test veritabani +
her testte rollback).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import Principal
from app.models.audit import AuditLog
from app.models.projects import Project
from app.models.reports import Report
from app.models.simulations import SimulationRun, SimulationStatus
from app.models.tenancy import Organization
from app.models.tests import TestDefinition, TestVariant
from app.routers import ai_explanations as ai_explanations_router
from app.services import ai_explanation as ai
from app.services import personas, simulation_worker

pytestmark = pytest.mark.integration

# --- Ortak sabit girdi (DB gerektirmeyen birim testleri icin) -------------------


def _sample_ai_input() -> ai.AIExplanationInput:
    return ai.AIExplanationInput(
        report_id=str(uuid.uuid4()),
        simulation_run_id=str(uuid.uuid4()),
        model_version="heuristic-baseline-2026.1",
        rules_version="rules-2026.1",
        fixture_version="fixtures-2026.1",
        methodology_reference="docs/methodology.md",
        metrics=[
            ai.AIExplanationMetricSnapshot(
                metric_id="task_completion_probability",
                point_estimate=0.62,
                low=0.5,
                high=0.74,
                unit="probability",
            ),
            ai.AIExplanationMetricSnapshot(
                metric_id="misclick_probability",
                point_estimate=0.12,
                low=0.08,
                high=0.16,
                unit="probability",
            ),
        ],
        critical_findings=[
            ai.AIExplanationCriticalFinding(
                key="low_task_completion", severity="warning", text="Test bulgusu."
            )
        ],
        persona_segments=[
            ai.AIExplanationPersonaSegment(
                key="18_24", label="18-24", count=190, share=0.95, small_sample_warning=False
            )
        ],
    )


# --- 1) Saglayici kapaliyken (varsayilan) tam calisirlik -------------------------


@pytest.mark.unit
async def test_none_provider_is_default_and_requires_no_key_or_network():
    assert settings.ai_provider == "none"
    provider = ai.get_provider()
    assert isinstance(provider, ai.NoneProvider)

    ai_input = _sample_ai_input()
    output, audit_record = await ai.generate_explanation(ai_input)

    assert output.calibration_status == "uncalibrated"
    assert output.provider == "none"
    assert output.model_name is None
    assert audit_record["fallback_used"] is False
    assert output.short_summary


@pytest.mark.unit
async def test_remote_provider_without_endpoint_falls_back_to_none():
    # AI_PROVIDER=remote ama uc nokta yapilandirilmamis: guvenli sekilde
    # NoneProvider'a duser, hicbir istisna firlatmaz.
    original_provider = settings.ai_provider
    original_endpoint = settings.ai_remote_endpoint
    settings.ai_provider = "remote"
    settings.ai_remote_endpoint = None
    try:
        provider = ai.get_provider()
        assert isinstance(provider, ai.NoneProvider)
    finally:
        settings.ai_provider = original_provider
        settings.ai_remote_endpoint = original_endpoint


# --- 2) Sema dogrulamasi ----------------------------------------------------------


@pytest.mark.unit
def test_output_schema_requires_all_sections():
    ai_input = _sample_ai_input()
    with pytest.raises(ValidationError):
        ai.AIExplanationOutput(
            short_summary="",
            metric_basis=[],
            possible_explanations=[],
            suggested_verification_experiment="",
            limitations="",
            prompt_version=ai.PROMPT_VERSION,
            provider="none",
            model_name=None,
            generated_at="2026-01-01T00:00:00Z",
        )
    del ai_input  # yalnizca modul import edilebilirligini kanitlamak icin


@pytest.mark.unit
def test_calibration_status_cannot_be_anything_but_uncalibrated():
    with pytest.raises(ValidationError):
        ai.AIExplanationOutput(
            calibration_status="calibrated",
            short_summary="ozet",
            metric_basis=[ai.AIExplanationFinding(text="bulgu", metric_ids=["task_completion_probability"])],
            possible_explanations=[
                ai.AIExplanationFinding(text="aciklama", metric_ids=["task_completion_probability"])
            ],
            suggested_verification_experiment="deney",
            limitations="sinirlama",
            prompt_version=ai.PROMPT_VERSION,
            provider="none",
            model_name=None,
            generated_at="2026-01-01T00:00:00Z",
        )


# --- 3) Metrik dayanagi zorunlulugu ----------------------------------------------


@pytest.mark.unit
def test_finding_without_metric_citation_is_rejected():
    with pytest.raises(ValidationError):
        ai.AIExplanationFinding(text="Kaynaksiz bir bulgu.", metric_ids=[])


@pytest.mark.unit
def test_finding_with_blank_metric_id_is_rejected():
    with pytest.raises(ValidationError):
        ai.AIExplanationFinding(text="bulgu", metric_ids=[""])


@pytest.mark.unit
@pytest.mark.security
async def test_output_citing_unknown_metric_id_is_rejected_and_falls_back():
    ai_input = _sample_ai_input()

    class FakeFabricatingProvider(ai.BaseExplainabilityProvider):
        name = "fake-fabricator"
        model_name = "fake-model"

        async def generate(self, ai_input: ai.AIExplanationInput) -> ai.AIExplanationOutput:
            import datetime as _dt

            return ai.AIExplanationOutput(
                short_summary="Donusum orani %340 arttı.",
                metric_basis=[
                    ai.AIExplanationFinding(
                        text="Uydurma donusum orani metrigi.",
                        metric_ids=["fake_conversion_rate"],
                    )
                ],
                possible_explanations=[
                    ai.AIExplanationFinding(
                        text="Baska bir uydurma aciklama.", metric_ids=["fake_conversion_rate"]
                    )
                ],
                suggested_verification_experiment="deney",
                limitations="sinirlama",
                prompt_version=ai.PROMPT_VERSION,
                provider=self.name,
                model_name=self.model_name,
                generated_at=_dt.datetime.now(_dt.UTC),
            )

    output, audit_record = await ai.generate_explanation(ai_input, provider=FakeFabricatingProvider())

    assert audit_record["fallback_used"] is True
    assert "fake_conversion_rate" not in [
        m for f in (*output.metric_basis, *output.possible_explanations) for m in f.metric_ids
    ]
    # Fallback ciktisi, gercek girdideki metriklere atifta bulunmalidir.
    output.validate_against_input(ai_input)


# --- 4) Yasakli ifadeler -----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.security
@pytest.mark.parametrize(
    "phrase",
    [
        "gerçek kullanıcı gördü ki bu buton çalışmıyor",
        "kullanıcılar böyle davrandı",
        "bu değişikliğin kesin neden-sonuç ilişkisi var",
        "kanıtlandı ki buton çalışmıyor",
        "gerçek göz takibi verisiyle doğrulandı",
        "erişilebilirlik sertifikası aldı",
        "gerçek pazar talebi bunu gösteriyor",
    ],
)
def test_banned_ai_phrases_are_rejected(phrase: str):
    with pytest.raises(ai.AIExplanationValidationError):
        ai.assert_no_banned_ai_claims(phrase)


@pytest.mark.unit
def test_clean_text_passes_banned_phrase_check():
    ai.assert_no_banned_ai_claims("Bu sentetik, kalibre edilmemiş bir tahmindir.")


@pytest.mark.unit
@pytest.mark.security
async def test_output_containing_banned_phrase_falls_back_to_template():
    ai_input = _sample_ai_input()

    class FakeOverclaimingProvider(ai.BaseExplainabilityProvider):
        name = "fake-overclaimer"
        model_name = "fake-model"

        async def generate(self, ai_input: ai.AIExplanationInput) -> ai.AIExplanationOutput:
            import datetime as _dt

            return ai.AIExplanationOutput(
                short_summary="Bu, gerçek kullanıcı gördü ve kanıtlandı.",
                metric_basis=[
                    ai.AIExplanationFinding(text="bulgu", metric_ids=["task_completion_probability"])
                ],
                possible_explanations=[
                    ai.AIExplanationFinding(text="aciklama", metric_ids=["task_completion_probability"])
                ],
                suggested_verification_experiment="deney",
                limitations="sinirlama",
                prompt_version=ai.PROMPT_VERSION,
                provider=self.name,
                model_name=self.model_name,
                generated_at=_dt.datetime.now(_dt.UTC),
            )

    output, audit_record = await ai.generate_explanation(ai_input, provider=FakeOverclaimingProvider())

    assert audit_record["fallback_used"] is True
    assert output.provider == "none"
    ai.assert_no_banned_ai_claims(output.short_summary)


# --- 5) Veri redaksiyonu: ham HTML/cookie/token/e-posta asla girdide olamaz ------


@pytest.mark.unit
@pytest.mark.security
def test_ai_explanation_input_schema_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        ai.AIExplanationInput(
            report_id=str(uuid.uuid4()),
            simulation_run_id=str(uuid.uuid4()),
            model_version="v1",
            rules_version=None,
            fixture_version=None,
            methodology_reference="docs/methodology.md",
            metrics=[],
            critical_findings=[],
            persona_segments=[],
            html="<div>gizli sayfa icerigi</div>",
        )


@pytest.mark.unit
@pytest.mark.security
def test_ai_explanation_input_schema_forbids_pii_fields():
    forbidden_kwargs = {
        "cookies": {"session": "abc"},
        "token": "secret-token",
        "user_email": "user@example.com",
        "form_values": {"password": "hunter2"},
    }
    for key, value in forbidden_kwargs.items():
        with pytest.raises(ValidationError):
            ai.AIExplanationInput(
                report_id=str(uuid.uuid4()),
                simulation_run_id=str(uuid.uuid4()),
                model_version="v1",
                rules_version=None,
                fixture_version=None,
                methodology_reference="docs/methodology.md",
                metrics=[],
                critical_findings=[],
                persona_segments=[],
                **{key: value},
            )


@pytest.mark.unit
@pytest.mark.security
def test_build_input_from_report_only_reads_allowlisted_fields():
    ai_input = ai.build_input_from_report(
        report_id=str(uuid.uuid4()),
        simulation_run_id=str(uuid.uuid4()),
        model_version="heuristic-baseline-2026.1",
        rules_version="rules-2026.1",
        fixture_version="fixtures-2026.1",
        methodology_reference="docs/methodology.md",
        metrics={
            "task_completion_probability": {"point_estimate": 0.5, "low": 0.4, "high": 0.6},
            "misclick_probability": {"point_estimate": 0.1, "low": 0.05, "high": 0.15},
            "abandonment_probability": {"point_estimate": 0.2, "low": 0.1, "high": 0.3},
            "task_duration_seconds": {"point_estimate": 60, "p10": 40, "p90": 90},
            "readability_score": 72.0,
            "contrast_check": {"pass": True, "avg_ratio": 5.1, "threshold": 4.5},
        },
        critical_findings=[{"key": "k", "severity": "info", "text": "t"}],
        persona_segments=[
            {"key": "18_24", "label": "18-24", "count": 100, "share": 1.0, "small_sample_warning": False}
        ],
    )
    dumped = ai_input.model_dump()
    forbidden_markers = ("html", "cookie", "token", "email", "password", "form_value")
    serialized = str(dumped).lower()
    for marker in forbidden_markers:
        assert marker not in serialized


# --- 6) Zaman asimi / saglayici hatasi -> guvenli sablona duser ------------------


@pytest.mark.unit
async def test_provider_timeout_falls_back_to_template_without_raising():
    ai_input = _sample_ai_input()

    class FakeTimeoutProvider(ai.BaseExplainabilityProvider):
        name = "fake-timeout"
        model_name = "fake-model"

        async def generate(self, ai_input: ai.AIExplanationInput) -> ai.AIExplanationOutput:
            raise ai.AIProviderUnavailableError("simulated timeout")

    output, audit_record = await ai.generate_explanation(ai_input, provider=FakeTimeoutProvider())

    assert output.provider == "none"
    assert audit_record["fallback_used"] is True
    assert "simulated timeout" in (audit_record["fallback_reason"] or "")


async def test_remote_http_provider_unreachable_endpoint_raises_unavailable():
    provider = ai.RemoteHttpProvider(
        endpoint="http://127.0.0.1:1/ai-explain",
        api_key=None,
        model_name="unreachable-model",
        timeout_seconds=2,
        max_retries=0,
        max_output_tokens=200,
    )
    with pytest.raises(ai.AIProviderUnavailableError):
        await provider.generate(_sample_ai_input())


async def test_generate_explanation_never_raises_even_when_remote_provider_unreachable():
    provider = ai.RemoteHttpProvider(
        endpoint="http://127.0.0.1:1/ai-explain",
        api_key=None,
        model_name="unreachable-model",
        timeout_seconds=2,
        max_retries=0,
        max_output_tokens=200,
    )
    output, audit_record = await ai.generate_explanation(_sample_ai_input(), provider=provider)
    assert output.provider == "none"
    assert audit_record["fallback_used"] is True


# --- Router seviyesi: gercek raporla uctan uca ------------------------------------


async def _make_succeeded_run_with_report(
    session: AsyncSession, organization: Organization, variant: TestVariant
) -> SimulationRun:
    distribution = {
        personas.AGE_RANGE: [
            {"key": "18_24", "label": "18-24", "weight": 95, "min_age": 18, "max_age": 24},
            {"key": "25_34", "label": "25-34", "weight": 5, "min_age": 25, "max_age": 34},
        ],
    }
    sample = personas.sample_cohorts(distribution, 200, 42)
    input_snapshot = {
        "wizard_test_type": "existing_site_basic_ux",
        "persona_count": 200,
        "target_audience": "Yeni B2B musteri adaylari",
        "modules": [],
        "url": "https://example.com/anasayfa",
        "role": "primary",
        "pricing_version": "2026.1",
        "persona_sample": {
            "generator_version": sample.generator_version,
            "distribution_snapshot": sample.distribution_snapshot,
            "segments": [
                {
                    "key": s.key,
                    "label": s.label,
                    "dimension_values": s.dimension_values,
                    "count": s.count,
                    "share": s.share,
                }
                for s in sample.segments
            ],
        },
    }
    run = SimulationRun(
        organization_id=organization.id,
        test_variant_id=variant.id,
        status=SimulationStatus.RUNNING,
        deterministic_seed=42,
        model_version="pending-engine",
        input_snapshot=input_snapshot,
    )
    session.add(run)
    await session.flush()
    await simulation_worker.process_run(session, run)
    assert run.status == SimulationStatus.SUCCEEDED
    return run


async def _get_report_for_run(session: AsyncSession, run: SimulationRun) -> Report:
    result = await session.execute(Report.__table__.select().where(Report.simulation_run_id == run.id))
    row = result.first()
    assert row is not None
    return await session.get(Report, row.id)


@pytest.fixture
async def report_context(session: AsyncSession, organization: Organization):
    project = Project(organization_id=organization.id, name=f"Proje {uuid.uuid4().hex[:6]}")
    session.add(project)
    await session.flush()

    definition = TestDefinition(
        organization_id=organization.id, project_id=project.id, name="AI aciklama testi"
    )
    session.add(definition)
    await session.flush()

    variant = TestVariant(
        organization_id=organization.id,
        test_definition_id=definition.id,
        name="Ana Senaryo",
        config={"role": "primary", "url": "https://example.com/anasayfa"},
    )
    session.add(variant)
    await session.flush()

    run = await _make_succeeded_run_with_report(session, organization, variant)
    report = await _get_report_for_run(session, run)
    return report


async def test_ai_explanation_endpoint_returns_cited_explanation(
    session: AsyncSession, organization: Organization, report_context: Report
):
    principal = Principal(user_id=None, organization_id=organization.id, role="analyst")

    output = await ai_explanations_router.generate_report_ai_explanation(
        report_context.id, principal=principal, session=session
    )

    assert output.calibration_status == "uncalibrated"
    assert output.metric_basis
    assert output.possible_explanations
    for finding in (*output.metric_basis, *output.possible_explanations):
        assert finding.metric_ids


@pytest.mark.security
async def test_ai_explanation_writes_audit_log_without_secrets(
    session: AsyncSession, organization: Organization, report_context: Report
):
    principal = Principal(user_id=None, organization_id=organization.id, role="analyst")

    await ai_explanations_router.generate_report_ai_explanation(
        report_context.id, principal=principal, session=session
    )

    result = await session.execute(
        select(AuditLog).where(
            AuditLog.organization_id == organization.id,
            AuditLog.action == "ai_explanation_generated",
        )
    )
    entry = result.scalars().first()
    assert entry is not None
    metadata = entry.entry_metadata
    assert metadata["prompt_version"] == ai.PROMPT_VERSION
    assert metadata["provider"] == "none"
    assert metadata["calibration_status"] == "uncalibrated"
    serialized = str(metadata).lower()
    for marker in ("api_key", "secret", "password", "token"):
        assert marker not in serialized


@pytest.mark.security
async def test_ai_explanation_cross_tenant_raises_not_found(
    session: AsyncSession, organization: Organization, report_context: Report
):
    from fastapi import HTTPException

    other_org = Organization(name="Other AI Org", slug=f"other-ai-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    principal = Principal(user_id=uuid.uuid4(), organization_id=other_org.id, role="analyst")

    with pytest.raises(HTTPException) as exc_info:
        await ai_explanations_router.generate_report_ai_explanation(
            report_context.id, principal=principal, session=session
        )
    assert exc_info.value.status_code == 404
