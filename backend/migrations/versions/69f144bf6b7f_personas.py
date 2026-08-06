"""personas

Revision ID: 69f144bf6b7f
Revises: e8a1f4c7d3b6
Create Date: 2026-07-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69f144bf6b7f'
down_revision: Union[str, Sequence[str], None] = 'e8a1f4c7d3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'personas',
        sa.Column('simulation_run_id', sa.Uuid(), nullable=False),
        sa.Column('index', sa.Integer(), nullable=False),
        sa.Column('label', sa.String(length=500), nullable=False),
        sa.Column('attributes', sa.JSON(), nullable=False),
        sa.Column('population_weight', sa.Integer(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['simulation_run_id'], ['simulation_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('simulation_run_id', 'index', name='uq_personas_run_index'),
        sa.CheckConstraint('population_weight > 0', name='ck_personas_population_weight_positive'),
    )
    op.create_index(
        'ix_personas_simulation_run_id',
        'personas',
        ['simulation_run_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_personas_simulation_run_id', table_name='personas')
    op.drop_table('personas')
