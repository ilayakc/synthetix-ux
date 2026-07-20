"""project status archive and test wizard drafts

Revision ID: 8ce9e82c11eb
Revises: 8043ef4faf35
Create Date: 2026-07-16 12:27:10.101094

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8ce9e82c11eb'
down_revision: Union[str, Sequence[str], None] = '8043ef4faf35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('test_wizard_drafts',
    sa.Column('organization_id', sa.Uuid(), nullable=False),
    sa.Column('created_by_user_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'LAUNCHED', name='test_wizard_draft_status'), nullable=False),
    sa.Column('current_step', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('launch_run_id', sa.Uuid(), nullable=True),
    sa.Column('launch_result', sa.JSON(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_test_wizard_drafts_organization_id', 'test_wizard_drafts', ['organization_id'], unique=False)

    # `status` mevcut satirlar icin bir varsayilan gerektirir (once dolu
    # olarak eklenir, sonra varsayilan kaldirilir ki yeni satirlar ORM
    # tarafindan acikca belirtilmek zorunda kalsin) - bkz. 8fb5df830eac.
    bind = op.get_bind()
    project_status_enum = sa.Enum('ACTIVE', 'ARCHIVED', name='project_status')
    project_status_enum.create(bind, checkfirst=True)
    op.add_column(
        'projects',
        sa.Column('status', project_status_enum, nullable=False, server_default='ACTIVE'),
    )
    op.alter_column('projects', 'status', server_default=None)
    op.add_column('projects', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'archived_at')
    op.drop_column('projects', 'status')
    sa.Enum(name='project_status').drop(op.get_bind(), checkfirst=True)
    op.drop_index('ix_test_wizard_drafts_organization_id', table_name='test_wizard_drafts')
    op.drop_table('test_wizard_drafts')
    sa.Enum(name='test_wizard_draft_status').drop(op.get_bind(), checkfirst=True)
