"""page analyses (SSRF-guvenli analyzer sonuclarinin kalici kaydi)

Revision ID: b2d6e1a9c7f4
Revises: a1c4e7f2b9d3
Create Date: 2026-07-16 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2d6e1a9c7f4'
down_revision: Union[str, Sequence[str], None] = 'a1c4e7f2b9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'page_analyses',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=True),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('authorization_confirmed', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            'status',
            sa.Enum('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', name='page_analysis_status'),
            nullable=False,
            server_default='QUEUED',
        ),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('snapshot_version', sa.String(length=100), nullable=True),
        sa.Column('analyzer_version', sa.String(length=100), nullable=True),
        sa.Column('source', sa.String(length=50), nullable=True),
        sa.Column('features', sa.JSON(), nullable=True),
        sa.Column('screenshot_data', sa.LargeBinary(), nullable=True),
        sa.Column('screenshot_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.alter_column('page_analyses', 'authorization_confirmed', server_default=None)
    op.alter_column('page_analyses', 'status', server_default=None)
    op.alter_column('page_analyses', 'attempt_count', server_default=None)
    op.create_index('ix_page_analyses_organization_id', 'page_analyses', ['organization_id'], unique=False)
    op.create_index('ix_page_analyses_status', 'page_analyses', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_page_analyses_status', table_name='page_analyses')
    op.drop_index('ix_page_analyses_organization_id', table_name='page_analyses')
    op.drop_table('page_analyses')
    sa.Enum(name='page_analysis_status').drop(op.get_bind(), checkfirst=True)
