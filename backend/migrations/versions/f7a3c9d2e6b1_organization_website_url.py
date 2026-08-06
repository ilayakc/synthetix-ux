"""organization website url

Revision ID: f7a3c9d2e6b1
Revises: d68747f9e724
Create Date: 2026-08-06

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f7a3c9d2e6b1"
down_revision: Union[str, Sequence[str], None] = "d68747f9e724"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("website_url", sa.String(length=2048), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "website_url")
