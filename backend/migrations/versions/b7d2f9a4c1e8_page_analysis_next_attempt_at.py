"""page_analyses.next_attempt_at (kalici gecikmeli yeniden deneme)

Revision ID: b7d2f9a4c1e8
Revises: c1a5e7d9f3b2
Create Date: 2026-08-19 10:00:00.000000

URL analiz islerinde gecici analyzer hatalarindan (429/timeout/5xx/baglanti)
sonra KALICI, gecikmeli yeniden deneme icin `next_attempt_at` kolonu eklenir.
`queued` bir is yalnizca `next_attempt_at` NULL ya da gecmisteyse alinir
(bkz. app.services.page_analysis.claim_next_queued); boylece worker
restart/redeploy olsa bile geri-cekilme suresi kaybolmaz. Nullable ve
server_default YOK: mevcut satirlar NULL (hemen alinabilir) olur, davranis
degismez.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f9a4c1e8"
down_revision: str | Sequence[str] | None = "c1a5e7d9f3b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "page_analyses",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    # `queued` + `next_attempt_at` uzerinden yapilan claim sorgusu icin kismi
    # indeks; yalnizca beklemede olan az sayida satiri kapsar.
    op.create_index(
        "ix_page_analyses_next_attempt_at",
        "page_analyses",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'QUEUED'"),
    )


def downgrade() -> None:
    op.drop_index("ix_page_analyses_next_attempt_at", table_name="page_analyses")
    op.drop_column("page_analyses", "next_attempt_at")
