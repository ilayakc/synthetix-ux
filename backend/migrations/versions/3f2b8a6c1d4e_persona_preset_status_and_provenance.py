"""persona preset status and provenance

Revision ID: 3f2b8a6c1d4e
Revises: 8ce9e82c11eb
Create Date: 2026-07-16 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f2b8a6c1d4e'
down_revision: Union[str, Sequence[str], None] = '8ce9e82c11eb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    persona_preset_status_enum = sa.Enum('ACTIVE', 'ARCHIVED', name='persona_preset_status')
    persona_preset_status_enum.create(bind, checkfirst=True)
    op.add_column(
        'persona_presets',
        sa.Column('status', persona_preset_status_enum, nullable=False, server_default='ACTIVE'),
    )
    op.alter_column('persona_presets', 'status', server_default=None)
    op.add_column('persona_presets', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('persona_presets', sa.Column('source_builtin_key', sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('persona_presets', 'source_builtin_key')
    op.drop_column('persona_presets', 'archived_at')
    op.drop_column('persona_presets', 'status')
    sa.Enum(name='persona_preset_status').drop(op.get_bind(), checkfirst=True)
