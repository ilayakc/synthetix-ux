"""simulation_runs ai_chip_reservation_id

Revision ID: c7b2f4e91a5d
Revises: a3e9c1d75f28
Create Date: 2026-08-02 12:00:00.000000

Faz 3C.2B1: `SimulationRun`a, `ai_report` (AI raporu) modulu seciliyse launch
grubu basina rezerve edilen AYRI AI Chip rezervasyonunu isaret eden yeni bir
nullable `ai_chip_reservation_id` kolonu ekler. Mevcut `chip_reservation_id`
(baseline/modul Chip'i) semantigi VE kolonu DEGISMEZ.

Karar (mevcut `chip_reservation_id` kolonu birebir ornek alinarak): plain
`sa.Uuid()`, nullable, FK YOK, ondelete YOK, index YOK - rezervasyonun kanonik
durumu `chip_reservations` tablosunda tutulur; bu yalnizca zayif bir
referanstir (bkz. app.models.simulations.SimulationRun). Eski satirlar NULL
kalir (backfill/placeholder YOK - AI secilmemis run'larda dogal olarak NULL).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c7b2f4e91a5d"
down_revision: Union[str, Sequence[str], None] = "a3e9c1d75f28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "simulation_runs",
        sa.Column("ai_chip_reservation_id", sa.Uuid(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("simulation_runs", "ai_chip_reservation_id")
