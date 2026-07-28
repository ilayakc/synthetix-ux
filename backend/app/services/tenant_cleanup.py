"""Dev/test ortaminda disposable (smoke) organizasyonlarin GUVENLI silinmesi.

Arka plan (bkz. docs/security.md "Dev/test ortaminda disposable (smoke)
tenant temizligi"): bir onceki turda `DELETE ... WHERE name LIKE 'Smoke%'`
(prefix/LIKE tabanli toplu silme) ile temizlik yapilirken, bu turda
olusturulmamis iki-uc organizasyon da yanlislikla silindi ve backup/WAL
arsivi olmadigi icin GERI ALINAMADI. Bu modul, ayni hatanin bir daha
olusmamasi icin TEK guvenli silme yolunu saglar:

- Prefix/LIKE/pattern tabanli hicbir parametre KABUL EDILMEZ - yalnizca
  tam (exact) `organization_id` (UUID) ile calisir.
- Silmeden once `name`/`slug`/(varsa) sahip kullanici `email`/`created_at`
  KAYDEDILEN beklenen degerlerle TAM eslesmelidir; herhangi biri
  eslesmezse `TenantCleanupGuardError` firlatilir ve HICBIR satir
  silinmez (cagiran taraf transaction'i rollback etmelidir).
- Silmeden once bagli kayit sayilari (`count_related_rows`) raporlanir -
  bu sayilar silme SONRASI bir daha asla elde edilemez, bu yuzden
  cagiran taraf bunlari loglamalidir.
- Organizasyon FK'leri zaten `ondelete="CASCADE"` (bkz. ilgili modeller);
  `session.delete(organization)` tek satirlik, exact-PK bir silme islemidir.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import ChipLedgerEntry, ChipReservation, Entitlement
from app.models.design_assets import DesignAsset
from app.models.design_generation import DesignGenerationJob
from app.models.page_analysis import PageAnalysis
from app.models.projects import Project
from app.models.reports import Report
from app.models.simulations import SimulationRun
from app.models.tenancy import Membership, Organization, User
from app.models.test_wizard import TestWizardDraft
from app.models.tests import TestDefinition, TestVariant


class TenantCleanupGuardError(ValueError):
    """Silinecek organizasyonun beklenen kimligi (id/name/slug/email/created_at)
    ile GERCEK DB durumu eslesmedigi icin firlatilir - guard failed, hicbir
    satir silinmedi. Cagiran taraf transaction'i rollback etmelidir."""


@dataclass(frozen=True)
class RelatedRowCounts:
    memberships: int
    projects: int
    test_wizard_drafts: int
    test_definitions: int
    test_variants: int
    simulation_runs: int
    reports: int
    page_analyses: int
    design_assets: int
    design_generation_jobs: int
    chip_reservations: int
    chip_ledger_entries: int
    entitlements: int

    def total(self) -> int:
        return (
            self.memberships
            + self.projects
            + self.test_wizard_drafts
            + self.test_definitions
            + self.test_variants
            + self.simulation_runs
            + self.reports
            + self.page_analyses
            + self.design_assets
            + self.design_generation_jobs
            + self.chip_reservations
            + self.chip_ledger_entries
            + self.entitlements
        )


async def _count(session: AsyncSession, model, organization_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count()).select_from(model).where(model.organization_id == organization_id)
    )
    return int(result.scalar_one())


async def count_related_rows(session: AsyncSession, organization_id: uuid.UUID) -> RelatedRowCounts:
    """Silmeden ONCE cagirilmalidir - bu sayilar silme sonrasi kurtarilamaz."""

    return RelatedRowCounts(
        memberships=await _count(session, Membership, organization_id),
        projects=await _count(session, Project, organization_id),
        test_wizard_drafts=await _count(session, TestWizardDraft, organization_id),
        test_definitions=await _count(session, TestDefinition, organization_id),
        test_variants=await _count(session, TestVariant, organization_id),
        simulation_runs=await _count(session, SimulationRun, organization_id),
        reports=await _count(session, Report, organization_id),
        page_analyses=await _count(session, PageAnalysis, organization_id),
        design_assets=await _count(session, DesignAsset, organization_id),
        design_generation_jobs=await _count(session, DesignGenerationJob, organization_id),
        chip_reservations=await _count(session, ChipReservation, organization_id),
        chip_ledger_entries=await _count(session, ChipLedgerEntry, organization_id),
        entitlements=await _count(session, Entitlement, organization_id),
    )


async def delete_disposable_organization_by_exact_id(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    expected_name: str,
    expected_slug: str,
    expected_owner_email: str | None = None,
    expected_created_at: datetime | None = None,
) -> RelatedRowCounts:
    """Tek bir organizasyonu, TAM kimlik dogrulamasindan gectikten sonra siler.

    Prefix/LIKE/pattern parametresi YOKTUR ve olamaz - imza yalnizca exact
    `organization_id` kabul eder. `expected_*` alanlarindan HERHANGI biri
    GERCEK DB satiriyla eslesmezse `TenantCleanupGuardError` firlatilir,
    HICBIR satir silinmez (ne bu fonksiyon ne cagiran taraf commit
    etmemelidir - guard failed = rollback).

    Donen `RelatedRowCounts`, silme ONCESI anlik goruntudur; cagiran taraf
    bunu silme islemi commit edilmeden once loglamalidir (bkz. modul
    dokstring'i - bu veri silme sonrasi bir daha elde edilemez).
    """

    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise TenantCleanupGuardError(f"Guard failed: organizasyon bulunamadi (id={organization_id})")
    if organization.name != expected_name:
        raise TenantCleanupGuardError(
            f"Guard failed: name eslesmiyor (beklenen={expected_name!r}, gercek={organization.name!r})"
        )
    if organization.slug != expected_slug:
        raise TenantCleanupGuardError(
            f"Guard failed: slug eslesmiyor (beklenen={expected_slug!r}, gercek={organization.slug!r})"
        )
    if expected_created_at is not None and organization.created_at != expected_created_at:
        raise TenantCleanupGuardError(
            "Guard failed: created_at eslesmiyor "
            f"(beklenen={expected_created_at!r}, gercek={organization.created_at!r})"
        )

    if expected_owner_email is not None:
        membership_result = await session.execute(
            select(User.email)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.organization_id == organization_id)
        )
        member_emails = {row[0] for row in membership_result.all()}
        if expected_owner_email not in member_emails:
            raise TenantCleanupGuardError(
                f"Guard failed: beklenen sahip email'i ({expected_owner_email!r}) "
                f"bu organizasyonun uyeleri arasinda yok (gercek uyeler: {sorted(member_emails)})"
            )

    counts = await count_related_rows(session, organization_id)

    await session.delete(organization)
    await session.flush()

    return counts


__all__ = [
    "TenantCleanupGuardError",
    "RelatedRowCounts",
    "count_related_rows",
    "delete_disposable_organization_by_exact_id",
]
