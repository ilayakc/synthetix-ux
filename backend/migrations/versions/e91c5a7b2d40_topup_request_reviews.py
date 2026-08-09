"""topup request reviews

Revision ID: e91c5a7b2d40
Revises: f7a3c9d2e6b1
Create Date: 2026-08-06 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e91c5a7b2d40"
down_revision: str | Sequence[str] | None = "f7a3c9d2e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE chip_topup_request_status ADD VALUE IF NOT EXISTS 'APPROVED'")
    op.execute("ALTER TYPE chip_topup_request_status ADD VALUE IF NOT EXISTS 'REJECTED'")
    op.add_column("chip_topup_requests", sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("chip_topup_requests", sa.Column("review_note", sa.String(length=500), nullable=True))
    op.add_column("chip_topup_requests", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_chip_topup_requests_reviewed_by_user_id_users",
        "chip_topup_requests",
        "users",
        ["reviewed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chip_topup_requests_status", "chip_topup_requests", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_chip_topup_requests_status", table_name="chip_topup_requests")
    op.drop_constraint(
        "fk_chip_topup_requests_reviewed_by_user_id_users",
        "chip_topup_requests",
        type_="foreignkey",
    )
    op.drop_column("chip_topup_requests", "reviewed_at")
    op.drop_column("chip_topup_requests", "review_note")
    op.drop_column("chip_topup_requests", "reviewed_by_user_id")

    # PostgreSQL enum degerlerini dogrudan silemez. Geri donuste islenmis
    # talepleri tekrar beklemeye alip enum tipini yalnizca PENDING ile kurariz.
    op.execute("UPDATE chip_topup_requests SET status = 'PENDING' WHERE status <> 'PENDING'")
    op.execute("ALTER TABLE chip_topup_requests ALTER COLUMN status TYPE VARCHAR USING status::text")
    op.execute("DROP TYPE chip_topup_request_status")
    pending_status = sa.Enum("PENDING", name="chip_topup_request_status")
    pending_status.create(op.get_bind(), checkfirst=False)
    op.execute(
        "ALTER TABLE chip_topup_requests ALTER COLUMN status "
        "TYPE chip_topup_request_status USING status::chip_topup_request_status"
    )
