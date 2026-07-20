import uuid
from asyncio import gather

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ChipReservationStatus, EntitlementStatus, Organization
from app.services import chip_ledger, entitlements, quotes
from app.services.exceptions import (
    ChipReservationNotFoundError,
    EntitlementNotFoundError,
    EntitlementUnavailableError,
    InsufficientChipBalanceError,
    InvalidChipReservationStateError,
    InvalidEntitlementStateError,
    OrganizationNotFoundError,
)
from app.services.pricing import (
    FEATURE_ACCESSIBILITY_PRECHECK,
    FEATURE_BASIC_UX_TEST,
    FREE_BASIC_UX_TEST_PERSONA_LIMIT,
)

# `test_engine`, `session` ve `organization` fixture'lari artik tests/conftest.py'de
# paylasilan altyapidan gelir (izole test veritabani + her testte rollback).

pytestmark = pytest.mark.integration


# --- 0 bakiye -------------------------------------------------------------


async def test_new_organization_starts_with_zero_chip_balance(
    session: AsyncSession, organization: Organization
):
    balance = await chip_ledger.get_chip_balance(session, organization.id)
    assert balance == 0


async def test_new_organization_has_no_promotional_credit(session: AsyncSession, organization: Organization):
    # Yeni sirket kayitta 10.000 veya baska bir promosyon bakiyesiyle
    # BASLAMAMALIDIR; hicbir ledger satiri olusturulmamis olmalidir.
    balance = await chip_ledger.get_chip_balance(session, organization.id)
    assert balance == 0


async def test_new_organization_has_one_of_each_free_entitlement(
    session: AsyncSession, organization: Organization
):
    free_entitlements = await entitlements.list_free_entitlements(session, organization.id)
    by_key = {e.feature_key: e for e in free_entitlements}

    assert set(by_key) == {FEATURE_BASIC_UX_TEST, FEATURE_ACCESSIBILITY_PRECHECK}
    for entitlement in by_key.values():
        assert entitlement.status == EntitlementStatus.AVAILABLE
        assert entitlement.quantity == 1


# --- 1.000 persona siniri --------------------------------------------------


async def test_quote_uses_free_entitlement_at_persona_limit(
    session: AsyncSession, organization: Organization
):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=FREE_BASIC_UX_TEST_PERSONA_LIMIT,
        test_type=FEATURE_BASIC_UX_TEST,
    )

    assert quote.free_entitlement_applicable is True
    assert quote.free_entitlement_feature_key == FEATURE_BASIC_UX_TEST
    assert quote.required_chips == 0


async def test_quote_rejects_free_entitlement_over_persona_limit(
    session: AsyncSession, organization: Organization
):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=FREE_BASIC_UX_TEST_PERSONA_LIMIT + 1,
        test_type=FEATURE_BASIC_UX_TEST,
    )

    assert quote.free_entitlement_applicable is False
    assert quote.free_entitlement_feature_key is None
    assert quote.required_chips > 0


async def test_quote_over_limit_label_notes_persona_limit_exceeded(
    session: AsyncSession, organization: Organization
):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=FREE_BASIC_UX_TEST_PERSONA_LIMIT + 1,
        test_type=FEATURE_BASIC_UX_TEST,
    )
    assert "1.000 persona sinirini asiyor" in quote.line_items[0].label


async def test_quote_within_limit_but_entitlement_consumed_requires_chips(
    session: AsyncSession, organization: Organization
):
    run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)
    await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)

    quote = await quotes.build_quote(
        session, organization.id, persona_count=500, test_type=FEATURE_BASIC_UX_TEST
    )

    assert quote.free_entitlement_applicable is False
    assert quote.required_chips == 500
    assert "ucretsiz hak zaten kullanildi" in quote.line_items[0].label


async def test_quote_accessibility_precheck_uses_free_entitlement(
    session: AsyncSession, organization: Organization
):
    quote = await quotes.build_quote(
        session, organization.id, persona_count=100, test_type=FEATURE_ACCESSIBILITY_PRECHECK
    )
    assert quote.free_entitlement_applicable is True
    assert quote.required_chips == 0


async def test_quote_accessibility_precheck_requires_chips_once_consumed(
    session: AsyncSession, organization: Organization
):
    run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, run_id)
    await entitlements.consume_entitlement(session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, run_id)

    quote = await quotes.build_quote(
        session, organization.id, persona_count=100, test_type=FEATURE_ACCESSIBILITY_PRECHECK
    )
    assert quote.free_entitlement_applicable is False
    assert quote.required_chips > 0


async def test_quote_includes_advanced_module_cost(session: AsyncSession, organization: Organization):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=100,
        test_type=FEATURE_ACCESSIBILITY_PRECHECK,
        modules=["advanced_simulation"],
    )
    module_line = next(item for item in quote.line_items if item.key == "advanced_simulation")
    assert module_line.chip_cost == 50
    assert quote.required_chips >= 50


async def test_quote_rejects_negative_persona_count(session: AsyncSession, organization: Organization):
    with pytest.raises(ValueError):
        await quotes.build_quote(session, organization.id, persona_count=-1, test_type=FEATURE_BASIC_UX_TEST)


async def test_quote_rejects_unknown_test_type(session: AsyncSession, organization: Organization):
    with pytest.raises(ValueError):
        await quotes.build_quote(session, organization.id, persona_count=100, test_type="unknown_test_type")


@pytest.mark.unit
def test_pricing_module_cost_rejects_unknown_module():
    from app.services.pricing import get_pricing_config

    with pytest.raises(ValueError):
        get_pricing_config().module_cost("unknown_module")


@pytest.mark.unit
def test_pricing_get_pricing_config_rejects_unknown_version():
    from app.services.pricing import get_pricing_config

    with pytest.raises(ValueError):
        get_pricing_config("9999.99-does-not-exist")


@pytest.mark.unit
def test_pricing_version_2026_1_is_pinned_and_unaffected_by_new_modules():
    """`2026.1` surumune ("2026.2" guncel olsa bile) eski quote/run/raporlarin
    hala erisebilmesi gerekir; yeni gelismis moduller yalnizca "2026.2"de vardir.
    """

    from app.services.pricing import CURRENT_PRICING_VERSION, get_pricing_config

    assert CURRENT_PRICING_VERSION == "2026.2"

    old = get_pricing_config("2026.1")
    assert old.basic_ux_test_chip_per_persona == 1
    assert old.accessibility_precheck_chip_cost == 30
    assert old.advanced_module_chip_costs == {"advanced_simulation": 50, "extended_reporting": 20}
    with pytest.raises(ValueError):
        old.module_cost("network_device_test")

    current = get_pricing_config()
    assert current.version == "2026.2"
    assert current.module_cost("network_device_test") == 40
    assert current.module_cost("campaign_cta_test") == 35
    assert current.module_cost("synthetic_attention_estimate") == 25
    # Eski moduller de yeni surumde ayni fiyatla korunur.
    assert current.module_cost("advanced_simulation") == 50
    assert current.module_cost("extended_reporting") == 20


async def test_quote_can_be_explicitly_pinned_to_old_pricing_version(
    session: AsyncSession, organization: Organization
):
    quote = await quotes.build_quote(
        session,
        organization.id,
        persona_count=100,
        test_type=FEATURE_ACCESSIBILITY_PRECHECK,
        modules=["advanced_simulation"],
        pricing_version="2026.1",
    )
    assert quote.pricing_version == "2026.1"
    module_line = next(item for item in quote.line_items if item.key == "advanced_simulation")
    assert module_line.chip_cost == 50


# --- Tek kullanim -----------------------------------------------------------


async def test_entitlement_cannot_be_used_twice(session: AsyncSession, organization: Organization):
    first_run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, first_run_id)
    await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, first_run_id)

    second_run_id = uuid.uuid4()
    with pytest.raises(EntitlementUnavailableError):
        await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, second_run_id)


@pytest.mark.security
async def test_second_organization_still_has_its_own_free_entitlement(
    session: AsyncSession, organization: Organization
):
    other_org = Organization(name="Other Org", slug=f"other-org-{uuid.uuid4().hex[:8]}")
    session.add(other_org)
    await session.flush()

    run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)
    await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)

    other_entitlement = await entitlements.get_or_create_entitlement(
        session, other_org.id, FEATURE_BASIC_UX_TEST
    )
    assert other_entitlement.status == EntitlementStatus.AVAILABLE


# --- Reserve / consume / release --------------------------------------------


async def test_reserve_then_consume_entitlement(session: AsyncSession, organization: Organization):
    run_id = uuid.uuid4()
    reserved = await entitlements.reserve_entitlement(
        session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, run_id
    )
    assert reserved.status == EntitlementStatus.RESERVED
    assert reserved.reserved_run_id == run_id

    consumed = await entitlements.consume_entitlement(
        session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, run_id
    )
    assert consumed.status == EntitlementStatus.CONSUMED


async def test_failed_or_cancelled_run_releases_entitlement_reservation(
    session: AsyncSession, organization: Organization
):
    """Basarisiz/iptal edilen/hic baslamayan is, rezerve hakki serbest birakmalidir."""

    run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, run_id)

    released = await entitlements.release_entitlement(
        session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, run_id
    )
    assert released.status == EntitlementStatus.AVAILABLE
    assert released.reserved_run_id is None

    # Serbest kalan hak, baska bir is icin tekrar rezerve edilebilir olmalidir.
    other_run_id = uuid.uuid4()
    reserved_again = await entitlements.reserve_entitlement(
        session, organization.id, FEATURE_ACCESSIBILITY_PRECHECK, other_run_id
    )
    assert reserved_again.status == EntitlementStatus.RESERVED


async def test_entitlement_release_after_consume_is_rejected(
    session: AsyncSession, organization: Organization
):
    run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)
    await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)

    with pytest.raises(InvalidEntitlementStateError):
        await entitlements.release_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)


async def test_reserve_entitlement_is_idempotent_for_same_run_id(
    session: AsyncSession, organization: Organization
):
    run_id = uuid.uuid4()
    first = await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)
    second = await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)

    assert first.id == second.id
    assert second.status == EntitlementStatus.RESERVED


@pytest.mark.security
async def test_reserve_entitlement_held_by_another_run_is_rejected(
    session: AsyncSession, organization: Organization
):
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4())

    with pytest.raises(EntitlementUnavailableError):
        await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4())


async def test_reserve_entitlement_reclaims_after_expiry(session: AsyncSession, organization: Organization):
    from datetime import UTC, datetime, timedelta

    first_run_id = uuid.uuid4()
    entitlement = await entitlements.reserve_entitlement(
        session, organization.id, FEATURE_BASIC_UX_TEST, first_run_id
    )
    # Rezervasyonun suresi gecmis gibi davranmasi icin dogrudan gecmise cekilir
    # (gercek bir bekleme yapmadan expiry dalini tetiklemek icin).
    entitlement.reserved_until = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    second_run_id = uuid.uuid4()
    reclaimed = await entitlements.reserve_entitlement(
        session, organization.id, FEATURE_BASIC_UX_TEST, second_run_id
    )

    assert reclaimed.reserved_run_id == second_run_id


@pytest.mark.security
async def test_consume_entitlement_unknown_is_rejected(session: AsyncSession, organization: Organization):
    with pytest.raises(EntitlementNotFoundError):
        await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4())


async def test_consume_entitlement_is_idempotent_once_consumed(
    session: AsyncSession, organization: Organization
):
    run_id = uuid.uuid4()
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)
    first = await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)
    second = await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, run_id)

    assert first.id == second.id
    assert second.status == EntitlementStatus.CONSUMED


@pytest.mark.security
async def test_consume_entitlement_by_wrong_run_is_rejected(
    session: AsyncSession, organization: Organization
):
    await entitlements.reserve_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4())

    with pytest.raises(InvalidEntitlementStateError):
        await entitlements.consume_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4())


@pytest.mark.security
async def test_release_entitlement_unknown_is_rejected(session: AsyncSession, organization: Organization):
    with pytest.raises(EntitlementNotFoundError):
        await entitlements.release_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4())


async def test_release_entitlement_is_idempotent_when_already_available(
    session: AsyncSession, organization: Organization
):
    # `get_or_create_entitlement` hakki AVAILABLE olarak olusturur; hic
    # rezerve edilmemis bir hakki serbest birakmak guvenli bicimde no-op olmali.
    await entitlements.get_or_create_entitlement(session, organization.id, FEATURE_BASIC_UX_TEST)

    released = await entitlements.release_entitlement(
        session, organization.id, FEATURE_BASIC_UX_TEST, uuid.uuid4()
    )
    assert released.status == EntitlementStatus.AVAILABLE


async def test_reserve_chips_then_consume_does_not_change_balance_again(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 100, "test credit")
    reservation = await chip_ledger.reserve_chips(session, organization.id, 40, "reserve for run")
    assert await chip_ledger.get_chip_balance(session, organization.id) == 60

    consumed = await chip_ledger.consume_reservation(
        session, organization.id, reservation.id, "run succeeded"
    )
    assert consumed.status == ChipReservationStatus.CONSUMED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 60


async def test_reserve_chips_then_release_restores_balance(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 100, "test credit")
    reservation = await chip_ledger.reserve_chips(session, organization.id, 40, "reserve for run")
    assert await chip_ledger.get_chip_balance(session, organization.id) == 60

    released = await chip_ledger.release_reservation(session, organization.id, reservation.id, "run failed")
    assert released.status == ChipReservationStatus.RELEASED
    assert await chip_ledger.get_chip_balance(session, organization.id) == 100


async def test_consume_after_release_is_rejected(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 50, "test credit")
    reservation = await chip_ledger.reserve_chips(session, organization.id, 20, "reserve")
    await chip_ledger.release_reservation(session, organization.id, reservation.id, "cancelled")

    with pytest.raises(InvalidChipReservationStateError):
        await chip_ledger.consume_reservation(session, organization.id, reservation.id, "too late")


# --- Negatif bakiye engeli ---------------------------------------------------


@pytest.mark.security
async def test_reserve_chips_insufficient_balance_is_rejected(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 5, "small credit")

    with pytest.raises(InsufficientChipBalanceError):
        await chip_ledger.reserve_chips(session, organization.id, 10, "too much")

    # Basarisiz rezervasyon denemesi bakiyeyi degistirmemis olmali.
    assert await chip_ledger.get_chip_balance(session, organization.id) == 5


@pytest.mark.security
async def test_balance_never_goes_negative_across_multiple_operations(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 30, "credit")
    reservation = await chip_ledger.reserve_chips(session, organization.id, 30, "reserve all")
    assert await chip_ledger.get_chip_balance(session, organization.id) == 0

    with pytest.raises(InsufficientChipBalanceError):
        await chip_ledger.reserve_chips(session, organization.id, 1, "over balance")

    assert await chip_ledger.get_chip_balance(session, organization.id) >= 0

    await chip_ledger.release_reservation(session, organization.id, reservation.id, "give back")
    assert await chip_ledger.get_chip_balance(session, organization.id) == 30


# --- Girdi dogrulama ve hata durumlari -----------------------------------------


async def test_credit_rejects_non_positive_amount(session: AsyncSession, organization: Organization):
    with pytest.raises(ValueError):
        await chip_ledger.credit(session, organization.id, 0, "gecersiz")


async def test_reserve_chips_rejects_non_positive_amount(session: AsyncSession, organization: Organization):
    with pytest.raises(ValueError):
        await chip_ledger.reserve_chips(session, organization.id, 0, "gecersiz")


@pytest.mark.security
async def test_credit_unknown_organization_is_rejected(session: AsyncSession):
    with pytest.raises(OrganizationNotFoundError):
        await chip_ledger.credit(session, uuid.uuid4(), 10, "bilinmeyen organizasyon")


@pytest.mark.security
async def test_consume_reservation_unknown_id_is_rejected(session: AsyncSession, organization: Organization):
    with pytest.raises(ChipReservationNotFoundError):
        await chip_ledger.consume_reservation(session, organization.id, uuid.uuid4(), "bilinmeyen")


@pytest.mark.security
async def test_release_after_consume_is_rejected(session: AsyncSession, organization: Organization):
    await chip_ledger.credit(session, organization.id, 20, "credit")
    reservation = await chip_ledger.reserve_chips(session, organization.id, 10, "reserve")
    await chip_ledger.consume_reservation(session, organization.id, reservation.id, "basarili")

    with pytest.raises(InvalidChipReservationStateError):
        await chip_ledger.release_reservation(session, organization.id, reservation.id, "gec kalindi")


# --- Idempotency --------------------------------------------------------------


@pytest.mark.security
async def test_credit_idempotency_key_prevents_duplicate_credit(
    session: AsyncSession, organization: Organization
):
    idempotency_key = f"credit-{uuid.uuid4()}"

    first = await chip_ledger.credit(session, organization.id, 25, "promo", idempotency_key=idempotency_key)
    second = await chip_ledger.credit(session, organization.id, 25, "promo", idempotency_key=idempotency_key)

    assert first.id == second.id
    assert await chip_ledger.get_chip_balance(session, organization.id) == 25


@pytest.mark.security
async def test_reserve_idempotency_key_returns_same_reservation(
    session: AsyncSession, organization: Organization
):
    await chip_ledger.credit(session, organization.id, 50, "credit")
    idempotency_key = f"reserve-{uuid.uuid4()}"

    first = await chip_ledger.reserve_chips(
        session, organization.id, 20, "reserve", idempotency_key=idempotency_key
    )
    second = await chip_ledger.reserve_chips(
        session, organization.id, 20, "reserve", idempotency_key=idempotency_key
    )

    assert first.id == second.id
    # Ayni idempotency_key ile ikinci cagri bakiyeyi tekrar dusurmemis olmali.
    assert await chip_ledger.get_chip_balance(session, organization.id) == 30


# --- Eszamanli istek ----------------------------------------------------------


@pytest.mark.security
async def test_concurrent_reservations_do_not_oversell_balance(test_engine):
    """Ayni organizasyon icin yarisan iki rezervasyon isteginden yalnizca biri
    basarili olmali; toplam rezervasyon miktari mevcut bakiyeyi asmamalidir.
    """

    setup_session = async_sessionmaker(test_engine, expire_on_commit=False)()
    org = Organization(name="Concurrency Org", slug=f"concurrency-org-{uuid.uuid4().hex[:8]}")
    setup_session.add(org)
    await setup_session.flush()
    await chip_ledger.credit(setup_session, org.id, 30, "seed balance for concurrency test")
    await setup_session.commit()
    await setup_session.close()

    async def attempt_reserve(amount: int) -> str:
        session = async_sessionmaker(test_engine, expire_on_commit=False)()
        try:
            await chip_ledger.reserve_chips(session, org.id, amount, "concurrent reserve")
            await session.commit()
            return "success"
        except InsufficientChipBalanceError:
            await session.rollback()
            return "insufficient"
        finally:
            await session.close()

    try:
        results = await gather(attempt_reserve(30), attempt_reserve(30))

        assert sorted(results) == ["insufficient", "success"]

        verify_session = async_sessionmaker(test_engine, expire_on_commit=False)()
        try:
            balance = await chip_ledger.get_chip_balance(verify_session, org.id)
        finally:
            await verify_session.close()
        assert balance == 0
    finally:
        cleanup_session = async_sessionmaker(test_engine, expire_on_commit=False)()
        try:
            db_org = await cleanup_session.get(Organization, org.id)
            if db_org is not None:
                await cleanup_session.delete(db_org)
                await cleanup_session.commit()
        finally:
            await cleanup_session.close()
