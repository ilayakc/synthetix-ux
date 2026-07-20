"""simulation engine state, progress, result and reservation linkage

Revision ID: a1c4e7f2b9d3
Revises: 3f2b8a6c1d4e
Create Date: 2026-07-16 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c4e7f2b9d3'
down_revision: Union[str, Sequence[str], None] = '3f2b8a6c1d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'simulation_runs',
        sa.Column('progress_percent', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('simulation_runs', 'progress_percent', server_default=None)
    op.add_column('simulation_runs', sa.Column('progress_message', sa.String(length=500), nullable=True))
    op.add_column('simulation_runs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('simulation_runs', sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'simulation_runs',
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.alter_column('simulation_runs', 'attempt_count', server_default=None)
    op.add_column(
        'simulation_runs',
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('simulation_runs', 'cancel_requested', server_default=None)

    op.add_column('simulation_runs', sa.Column('rules_version', sa.String(length=50), nullable=True))
    op.add_column('simulation_runs', sa.Column('fixture_version', sa.String(length=50), nullable=True))
    op.add_column('simulation_runs', sa.Column('result', sa.JSON(), nullable=True))

    op.add_column('simulation_runs', sa.Column('launch_run_id', sa.Uuid(), nullable=True))
    op.add_column(
        'simulation_runs', sa.Column('free_entitlement_feature_key', sa.String(length=100), nullable=True)
    )
    op.add_column('simulation_runs', sa.Column('chip_reservation_id', sa.Uuid(), nullable=True))

    op.create_index(
        'ix_simulation_runs_launch_run_id', 'simulation_runs', ['launch_run_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_simulation_runs_launch_run_id', table_name='simulation_runs')
    op.drop_column('simulation_runs', 'chip_reservation_id')
    op.drop_column('simulation_runs', 'free_entitlement_feature_key')
    op.drop_column('simulation_runs', 'launch_run_id')
    op.drop_column('simulation_runs', 'result')
    op.drop_column('simulation_runs', 'fixture_version')
    op.drop_column('simulation_runs', 'rules_version')
    op.drop_column('simulation_runs', 'cancel_requested')
    op.drop_column('simulation_runs', 'attempt_count')
    op.drop_column('simulation_runs', 'finished_at')
    op.drop_column('simulation_runs', 'started_at')
    op.drop_column('simulation_runs', 'progress_message')
    op.drop_column('simulation_runs', 'progress_percent')
