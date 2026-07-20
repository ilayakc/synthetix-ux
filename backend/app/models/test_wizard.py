import enum
import uuid

from sqlalchemy import JSON, ForeignKey, Index, Integer
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TestWizardDraftStatus(str, enum.Enum):
    DRAFT = "draft"
    LAUNCHED = "launched"


class TestWizardDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Yeni test kurulum sihirbazinin adimlar arasi kalici taslagi.

    `payload`, sihirbazin tum adimlarindan toplanan (kismi doldurulmus
    olabilecek) alanlari tek bir JSON belgede tutar; sayfa yenilenince veya
    geri/ileri gidilince kaybolmamasi icin her adim degisikliginde sunucuya
    PATCH edilir. `launch_run_id`/`launch_result`, baslatma (launch)
    islemini idempotent kilmak icin kullanilir (bkz.
    `app.services.test_wizard.launch_draft`): taslak bir kez `launched`
    olduktan sonra tekrar baslatma denemeleri yeni bir yan etki uretmeden
    ayni sonucu dondurur.
    """

    __tablename__ = "test_wizard_drafts"
    __table_args__ = (Index("ix_test_wizard_drafts_organization_id", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[TestWizardDraftStatus] = mapped_column(
        SqlEnum(TestWizardDraftStatus, name="test_wizard_draft_status"),
        nullable=False,
        default=TestWizardDraftStatus.DRAFT,
    )
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    launch_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    launch_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
