"""design generation jobs (AI ile tasarim varyanti uretimi)

Revision ID: f2c8a5d1e7b3
Revises: a7c39f0e6b2d
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2c8a5d1e7b3'
down_revision: Union[str, Sequence[str], None] = 'a7c39f0e6b2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'design_generation_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('source_asset_id', sa.Uuid(), nullable=False),
        sa.Column('result_asset_id', sa.Uuid(), nullable=True),
        sa.Column(
            'status',
            sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', name='design_generation_status'),
            nullable=False,
            server_default='QUEUED',
        ),
        sa.Column('prompt', sa.Text(), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('model_name', sa.String(length=200), nullable=False),
        sa.Column('provider_request_id', sa.String(length=200), nullable=True),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('generation_version', sa.String(length=50), nullable=False, server_default='v1'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['source_asset_id'], ['design_assets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['result_asset_id'], ['design_assets.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.alter_column('design_generation_jobs', 'status', server_default=None)
    op.alter_column('design_generation_jobs', 'attempt_count', server_default=None)
    op.alter_column('design_generation_jobs', 'generation_version', server_default=None)
    op.create_index(
        'ix_design_generation_jobs_organization_id', 'design_generation_jobs', ['organization_id'], unique=False
    )
    op.create_index('ix_design_generation_jobs_status', 'design_generation_jobs', ['status'], unique=False)
    op.create_index(
        'ix_design_generation_jobs_expires_at', 'design_generation_jobs', ['expires_at'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_design_generation_jobs_expires_at', table_name='design_generation_jobs')
    op.drop_index('ix_design_generation_jobs_status', table_name='design_generation_jobs')
    op.drop_index('ix_design_generation_jobs_organization_id', table_name='design_generation_jobs')
    op.drop_table('design_generation_jobs')
    sa.Enum(name='design_generation_status').drop(op.get_bind(), checkfirst=True)
