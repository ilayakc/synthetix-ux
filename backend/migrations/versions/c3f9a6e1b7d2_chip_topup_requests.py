"""chip topup requests

Revision ID: c3f9a6e1b7d2
Revises: b2d6e1a9c7f4
Create Date: 2026-07-17 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f9a6e1b7d2'
down_revision: Union[str, Sequence[str], None] = 'b2d6e1a9c7f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'chip_topup_requests',
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('requested_by_user_id', sa.Uuid(), nullable=False),
        sa.Column('package_key', sa.String(length=100), nullable=False),
        sa.Column('chip_amount', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('PENDING', name='chip_topup_request_status'),
            nullable=False,
            server_default='PENDING',
        ),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.alter_column('chip_topup_requests', 'status', server_default=None)
    op.create_index(
        'ix_chip_topup_requests_organization_id', 'chip_topup_requests', ['organization_id'], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_chip_topup_requests_organization_id', table_name='chip_topup_requests')
    op.drop_table('chip_topup_requests')
    sa.Enum(name='chip_topup_request_status').drop(op.get_bind(), checkfirst=True)
