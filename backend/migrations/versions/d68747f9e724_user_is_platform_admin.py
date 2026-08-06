"""user is platform admin

Revision ID: d68747f9e724
Revises: b1f4d8e29a3c
Create Date: 2026-08-04 14:43:28.240079

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd68747f9e724'
down_revision: Union[str, Sequence[str], None] = 'b1f4d8e29a3c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Mevcut satirlar icin gecici bir server_default gerekir; yeni satirlar
    # ORM tarafindan (User.is_platform_admin default=False) acikca
    # belirtilecegi icin varsayilan sonradan kaldirilir (bkz. 964f322e9e83
    # ile ayni desen).
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("users", "is_platform_admin", server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "is_platform_admin")
