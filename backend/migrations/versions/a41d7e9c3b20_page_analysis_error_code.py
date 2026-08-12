"""add typed page analysis error code

Revision ID: a41d7e9c3b20
Revises: f9b3c2e7a1d8
Create Date: 2026-08-12 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a41d7e9c3b20"
down_revision: str | Sequence[str] | None = "f9b3c2e7a1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("page_analyses", sa.Column("error_code", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("page_analyses", "error_code")
