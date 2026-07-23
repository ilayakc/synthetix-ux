"""design assets (kullanici tarafindan yuklenen tasarim ekran goruntuleri)

Revision ID: e5b8f1a4c9d6
Revises: d4a7c2f9e6b1
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5b8f1a4c9d6'
down_revision: Union[str, Sequence[str], None] = 'd4a7c2f9e6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'design_assets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('uploaded_by_user_id', sa.Uuid(), nullable=True),
        sa.Column(
            'content_type',
            sa.Enum('PNG', 'JPEG', 'WEBP', name='design_asset_content_type'),
            nullable=False,
        ),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=True),
        sa.Column(
            'status',
            sa.Enum('ACTIVE', 'DELETED', name='design_asset_status'),
            nullable=False,
            server_default='ACTIVE',
        ),
        sa.Column('image_data', sa.LargeBinary(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('purged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['uploaded_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.alter_column('design_assets', 'status', server_default=None)
    op.create_index('ix_design_assets_organization_id', 'design_assets', ['organization_id'], unique=False)
    op.create_index('ix_design_assets_status', 'design_assets', ['status'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_design_assets_status', table_name='design_assets')
    op.drop_index('ix_design_assets_organization_id', table_name='design_assets')
    op.drop_table('design_assets')
    sa.Enum(name='design_asset_status').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='design_asset_content_type').drop(op.get_bind(), checkfirst=True)
