"""calibration observations

Revision ID: e8a1f4c7d3b6
Revises: b6f1c4d8a2e7
Create Date: 2026-07-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8a1f4c7d3b6'
down_revision: Union[str, Sequence[str], None] = 'b6f1c4d8a2e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'calibration_observations',
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('simulation_run_id', sa.Uuid(), nullable=False),
        sa.Column('recorded_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('consent_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('real_task_completion_rate', sa.Float(), nullable=True),
        sa.Column('real_median_task_duration_seconds', sa.Float(), nullable=True),
        sa.Column('real_misclick_rate', sa.Float(), nullable=True),
        sa.Column('real_abandonment_rate', sa.Float(), nullable=True),
        sa.Column('sample_size', sa.Integer(), nullable=True),
        sa.Column('source_note', sa.Text(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['simulation_run_id'], ['simulation_runs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.alter_column('calibration_observations', 'consent_confirmed', server_default=None)
    op.create_index(
        'ix_calibration_observations_organization_id',
        'calibration_observations',
        ['organization_id'],
        unique=False,
    )
    op.create_index(
        'ix_calibration_observations_simulation_run_id',
        'calibration_observations',
        ['simulation_run_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_calibration_observations_simulation_run_id', table_name='calibration_observations')
    op.drop_index('ix_calibration_observations_organization_id', table_name='calibration_observations')
    op.drop_table('calibration_observations')
