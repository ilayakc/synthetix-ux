"""simulation run page analysis link (Paket 4B)

Revision ID: b6f1c4d8a2e7
Revises: d1e4a8c2f6b9
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6f1c4d8a2e7'
down_revision: Union[str, Sequence[str], None] = 'd1e4a8c2f6b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('simulation_runs', sa.Column('page_analysis_id', sa.Uuid(), nullable=True))
    op.create_index(
        'ix_simulation_runs_page_analysis_id', 'simulation_runs', ['page_analysis_id'], unique=False
    )
    op.create_foreign_key(
        'fk_simulation_runs_page_analysis_id',
        'simulation_runs',
        'page_analyses',
        ['page_analysis_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_simulation_runs_page_analysis_id', 'simulation_runs', type_='foreignkey')
    op.drop_index('ix_simulation_runs_page_analysis_id', table_name='simulation_runs')
    op.drop_column('simulation_runs', 'page_analysis_id')
