"""ai_pipeline_stage_provider_configuration_hash

Revision ID: b1f4d8e29a3c
Revises: ab507b226d36
Create Date: 2026-08-03 18:40:00.000000

Faz 3D.1: `ai_pipeline_stages`a provider'in SEMANTIK yapilandirma kimligini
(model + reasoning effort + structured-output modu/surumu + semantik uretim
ayarlari - bkz. app.services.ai_pipeline.provider.compute_configuration_
fingerprint) tasiyan yeni bir `provider_configuration_hash` kolonu ekler.
Yalnizca provider (LLM) asamalarinda (3/4/6) ilk execution pinlemesinde
doldurulur; saf asamalarda (1/2/5) ve mevcut satirlarda NULL kalir. API
key/timeout/secret/pid/timestamp ASLA icermez.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1f4d8e29a3c"
down_revision: Union[str, Sequence[str], None] = "ab507b226d36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ai_pipeline_stages",
        sa.Column("provider_configuration_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("ai_pipeline_stages", "provider_configuration_hash")
