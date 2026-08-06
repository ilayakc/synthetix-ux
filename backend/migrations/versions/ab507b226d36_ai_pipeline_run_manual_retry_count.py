"""ai_pipeline_run_manual_retry_count

Revision ID: ab507b226d36
Revises: c7b2f4e91a5d
Create Date: 2026-08-03 17:55:48.335606

Faz 3C.3: `ai_pipeline_runs`a MANUEL retry jenerasyonunu izleyen yeni bir
`manual_retry_count` kolonu ekler (bkz. app.models.ai_pipeline.AIPipelineRun,
app.services.ai_pipeline.mutations.retry_ai_pipeline_group). 0 = ilk ucretli
AI calismasi; her BASARILI manuel retry transaction'i +1 yapar. Otomatik worker
retry'lari bu alani ARTIRMAZ. Negatif olamaz (check constraint). Mevcut
satirlar `server_default="0"` ile 0 alir.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ab507b226d36"
down_revision: Union[str, Sequence[str], None] = "c7b2f4e91a5d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ai_pipeline_runs",
        sa.Column("manual_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_ai_pipeline_runs_manual_retry_count_non_negative",
        "ai_pipeline_runs",
        "manual_retry_count >= 0",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_ai_pipeline_runs_manual_retry_count_non_negative",
        "ai_pipeline_runs",
        type_="check",
    )
    op.drop_column("ai_pipeline_runs", "manual_retry_count")
