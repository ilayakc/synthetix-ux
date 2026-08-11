"""ai interaction heatmap table and reservation link

Revision ID: f9b3c2e7a1d8
Revises: e91c5a7b2d40
Create Date: 2026-08-11 09:00:00.000000

`ai_interaction_heatmap` modulunu (AI etkilesim isi haritasi / AI tiklama
tahmini) tasiyan sema degisiklikleri:

1. `interaction_heatmaps` tablosu (bir `SimulationRun` icin TEK, dogrulanmis AI
   tiklama tahmini isi + sonucu; bkz. app.models.interaction_heatmap). Sonuc,
   rapor goruntulenirken lazy hesaplanmaz - ayri bir worker gercek OpenAI
   provider'ini cagirir ve buraya yazar; GET /api/reports/{id} yalnizca OKUR.
2. `simulation_runs.heatmap_chip_reservation_id` (nullable) - `ai_report`
   rezervasyonundan (`ai_chip_reservation_id`) BAGIMSIZ, launch grubu basina TEK
   heatmap Chip rezervasyonu (bkz. app.services.test_wizard.launch_draft). Mevcut
   `ai_chip_reservation_id` kolonu birebir ornek alinir: plain `sa.Uuid()`,
   nullable, FK/index YOK (kanonik durum `chip_reservations` tablosunda).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9b3c2e7a1d8"
down_revision: str | Sequence[str] | None = "e91c5a7b2d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `create_table` sutun tipi olarak verilen `sa.Enum(...)`u kendisi otomatik
    # olusturur (bkz. d62d90ed418a); ayrica `.create()` cagrilmaz.
    interaction_heatmap_status_enum = sa.Enum(
        "QUEUED", "RUNNING", "SUCCEEDED", "FAILED", name="interaction_heatmap_status"
    )

    op.create_table(
        "interaction_heatmaps",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("simulation_run_id", sa.Uuid(), nullable=False),
        sa.Column("status", interaction_heatmap_status_enum, nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["simulation_run_id"], ["simulation_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("simulation_run_id", name="uq_interaction_heatmaps_simulation_run_id"),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_interaction_heatmaps_attempt_count_non_negative"
        ),
    )
    op.alter_column("interaction_heatmaps", "attempt_count", server_default=None)
    op.create_index(
        "ix_interaction_heatmaps_organization_id", "interaction_heatmaps", ["organization_id"], unique=False
    )
    op.create_index("ix_interaction_heatmaps_status", "interaction_heatmaps", ["status"], unique=False)
    op.create_index(
        "ix_interaction_heatmaps_fingerprint", "interaction_heatmaps", ["fingerprint"], unique=False
    )

    op.add_column(
        "simulation_runs",
        sa.Column("heatmap_chip_reservation_id", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("simulation_runs", "heatmap_chip_reservation_id")
    op.drop_index("ix_interaction_heatmaps_fingerprint", table_name="interaction_heatmaps")
    op.drop_index("ix_interaction_heatmaps_status", table_name="interaction_heatmaps")
    op.drop_index("ix_interaction_heatmaps_organization_id", table_name="interaction_heatmaps")
    op.drop_table("interaction_heatmaps")
    sa.Enum(name="interaction_heatmap_status").drop(op.get_bind(), checkfirst=True)
