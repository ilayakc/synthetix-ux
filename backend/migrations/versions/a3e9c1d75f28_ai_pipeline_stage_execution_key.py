"""ai pipeline stage execution idempotency key

Revision ID: a3e9c1d75f28
Revises: d62d90ed418a
Create Date: 2026-08-02 09:00:00.000000

Faz 3B.2C: `AIPipelineStage`e, MANTIKSAL (logical) `idempotency_key`den KASITLI
olarak AYRI olan, GERCEK stage EXECUTION kimligini tasiyan yeni bir nullable
`execution_idempotency_key` kolonu ekler (provider/model'e bagli). Mevcut
`idempotency_key` UNIQUE kisitina DOKUNULMAZ.

UNIQUE (nullable) karari: deger deterministik bir hash oldugundan (run/stage/
batch/prompt/provider/model/input) iki FARKLI stage satirinin ayni execution
identity'yi paylasmasi DAG butunlugu acisindan anlamsizdir; retry AYNI satiri
gunceller ve bu key'i DEGISTIRMEZ. Postgres birden fazla NULL'a UNIQUE altinda
izin verir - mevcut/eski satirlar ve henuz pinlenmemis satirlar NULL kalir
(backfill/placeholder YOK).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a3e9c1d75f28"
down_revision: Union[str, Sequence[str], None] = "d62d90ed418a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "ai_pipeline_stages",
        sa.Column("execution_idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "uq_ai_pipeline_stages_execution_idempotency_key",
        "ai_pipeline_stages",
        ["execution_idempotency_key"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_ai_pipeline_stages_execution_idempotency_key",
        "ai_pipeline_stages",
        type_="unique",
    )
    op.drop_column("ai_pipeline_stages", "execution_idempotency_key")
