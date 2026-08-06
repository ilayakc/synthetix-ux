"""Faz 3C.2B1: `ai_report` quote/fiyat ayrimi testleri (QUOTE 8-12, 18).

`ai_report`in bedeli baseline `required_chips`ten AYRI tutulur (ayri
rezervasyon); quote'ta ayri bir line item olarak gorunur ve `total_chips`
ikisinin toplamidir.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenancy import Organization
from app.services import quotes
from app.services.pricing import FEATURE_BASIC_UX_TEST

pytestmark = pytest.mark.integration


async def _quote(session, org, *, modules):
    return await quotes.build_quote(
        session,
        org.id,
        persona_count=1000,  # <=1000 + ucretsiz hak -> baseline temel test 0 Chip
        test_type=FEATURE_BASIC_UX_TEST,
        modules=modules,
    )


async def test_ai_report_adds_separate_50_chip_line_item(session: AsyncSession, organization: Organization):
    quote = await _quote(session, organization, modules=["ai_report"])
    ai_lines = [li for li in quote.line_items if li.key == "ai_report"]
    assert len(ai_lines) == 1
    assert ai_lines[0].chip_cost == 50
    assert ai_lines[0].unit_chip_cost == 50


async def test_ai_report_chips_field_is_exactly_50(session: AsyncSession, organization: Organization):
    quote = await _quote(session, organization, modules=["ai_report"])
    assert quote.ai_report_chips == 50


async def test_baseline_required_chips_excludes_ai_report(session: AsyncSession, organization: Organization):
    # campaign_cta_test (35 Chip) baseline'a girer; ai_report (50) GIRMEZ.
    quote = await _quote(session, organization, modules=["campaign_cta_test", "ai_report"])
    assert quote.required_chips == 35  # yalnizca campaign_cta_test
    assert quote.ai_report_chips == 50


async def test_total_chips_is_baseline_plus_ai(session: AsyncSession, organization: Organization):
    quote = await _quote(session, organization, modules=["campaign_cta_test", "ai_report"])
    assert quote.total_chips == quote.required_chips + quote.ai_report_chips == 85


async def test_only_ai_scenario_has_zero_baseline(session: AsyncSession, organization: Organization):
    """Baseline tutari 0 (ucretsiz temel test) + yalnizca AI 50 -> required_chips=0."""

    quote = await _quote(session, organization, modules=["ai_report"])
    assert quote.required_chips == 0
    assert quote.ai_report_chips == 50
    assert quote.total_chips == 50


async def test_without_ai_report_quote_behaviour_unchanged(session: AsyncSession, organization: Organization):
    quote = await _quote(session, organization, modules=["campaign_cta_test"])
    assert quote.ai_report_chips == 0
    assert quote.required_chips == 35
    assert quote.total_chips == 35
    assert all(li.key != "ai_report" for li in quote.line_items)


async def test_line_items_sum_equals_total_chips(session: AsyncSession, organization: Organization):
    """Frontend'in "Toplam"/"Gerekli Chip" olarak GOSTERDIGI deger, tum line
    item'larin (baseline + gelismis moduller + ai_report) toplamina esittir ve
    bu `total_chips`e denk gelir - `required_chips` (ai_report HARIC) DEGIL. Bu,
    "gosterilen 500 ama 550 dusuldu" hatasinin kok invariant'idir; ayni 50 Chip
    iki kez de sayilmaz (ai_report tam olarak BIR line item)."""

    quote = await _quote(session, organization, modules=["campaign_cta_test", "ai_report"])
    line_items_sum = sum(li.chip_cost for li in quote.line_items)
    assert line_items_sum == quote.total_chips == 85
    # `required_chips` (baseline) bilerek daha kucuktur (AI ayri rezervasyon);
    # frontend bunu TOPLAM olarak gostermemelidir.
    assert quote.required_chips == 35
    # ai_report tam olarak BIR kez sayilir (cift ucret/cift rezervasyon yok).
    assert sum(1 for li in quote.line_items if li.key == "ai_report") == 1
