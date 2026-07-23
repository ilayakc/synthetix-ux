"""design assets expires_at index (purge sorgusu icin)

Revision ID: a7c39f0e6b2d
Revises: e5b8f1a4c9d6
Create Date: 2026-07-20 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c39f0e6b2d'
down_revision: Union[str, Sequence[str], None] = 'e5b8f1a4c9d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # `purge_expired_design_assets` (bkz. app.services.design_assets) her
    # calistiginda `expires_at < now()` araligini tarar; bu sutunda index
    # olmadan tam tablo taramasi yapar. Tablo/enum semasinda baska hicbir
    # degisiklik yapilmaz.
    op.create_index('ix_design_assets_expires_at', 'design_assets', ['expires_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_design_assets_expires_at', table_name='design_assets')
