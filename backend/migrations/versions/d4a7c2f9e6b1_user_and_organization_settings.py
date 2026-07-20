"""user and organization settings

Revision ID: d4a7c2f9e6b1
Revises: c3f9a6e1b7d2
Create Date: 2026-07-19 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a7c2f9e6b1'
down_revision: Union[str, Sequence[str], None] = 'c3f9a6e1b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'user_preferences',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('language', sa.String(length=10), nullable=False, server_default='tr'),
        sa.Column('timezone', sa.String(length=50), nullable=False, server_default='Europe/Istanbul'),
        sa.Column('theme', sa.String(length=10), nullable=False, server_default='system'),
        sa.Column('compact_view', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('notify_simulation_completed', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notify_simulation_failed', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notify_report_ready', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notify_low_chip_balance', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('low_chip_balance_threshold', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )

    op.create_table(
        'organization_settings',
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='TRY'),
        sa.Column('default_persona_count', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('default_persona_preset_id', sa.String(length=255), nullable=True),
        sa.Column('default_device_profile', sa.String(length=50), nullable=True),
        sa.Column('default_modules', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('default_target_audience', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('organization_id'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('organization_settings')
    op.drop_table('user_preferences')
