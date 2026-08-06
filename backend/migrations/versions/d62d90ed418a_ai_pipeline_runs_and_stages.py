"""ai pipeline runs and stages

Revision ID: d62d90ed418a
Revises: 69f144bf6b7f
Create Date: 2026-07-31 08:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd62d90ed418a'
down_revision: Union[str, Sequence[str], None] = '69f144bf6b7f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # NOT: `op.add_column` (bkz. 8fb5df830eac) ENUM turunu otomatik
    # OLUSTURMAZ ve orada acikca `.create(bind, checkfirst=True)` cagirmak
    # gerekir - ama `op.create_table` TAM TERSINE, sutun tipi olarak
    # verilen bir `sa.Enum(...)`u kendisi otomatik olusturur. Burada HER
    # UC enum de yalnizca `create_table` ile YENI olusturulan tablolarda
    # kullanildigi icin (mevcut bir tabloya `add_column` YOK), ayrica bir
    # `.create()` cagrisi YAPILMAZ - aksi halde (denendi, dogrulandi)
    # "type already exists" hatasiyla cakisir.
    ai_pipeline_status_enum = sa.Enum(
        'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PARTIAL', 'CANCELLED',
        name='ai_pipeline_status',
    )
    ai_pipeline_stage_type_enum = sa.Enum(
        'EVIDENCE_PREPARATION',
        'PERSONA_BATCH_PREPARATION',
        'SCENARIO_INTERPRETATION',
        'PERSONA_BEHAVIOR',
        'AGGREGATION',
        'UX_REPORT',
        name='ai_pipeline_stage_type',
    )
    ai_pipeline_stage_status_enum = sa.Enum(
        'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'SKIPPED',
        name='ai_pipeline_stage_status',
    )

    op.create_table(
        'ai_pipeline_runs',
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('simulation_run_id', sa.Uuid(), nullable=False),
        sa.Column('status', ai_pipeline_status_enum, nullable=False),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('total_input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost', sa.Float(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['simulation_run_id'], ['simulation_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('simulation_run_id', name='uq_ai_pipeline_runs_simulation_run_id'),
        sa.CheckConstraint(
            'total_input_tokens >= 0', name='ck_ai_pipeline_runs_total_input_tokens_non_negative'
        ),
        sa.CheckConstraint(
            'total_output_tokens >= 0', name='ck_ai_pipeline_runs_total_output_tokens_non_negative'
        ),
    )
    op.alter_column('ai_pipeline_runs', 'cancel_requested', server_default=None)
    op.alter_column('ai_pipeline_runs', 'total_input_tokens', server_default=None)
    op.alter_column('ai_pipeline_runs', 'total_output_tokens', server_default=None)
    op.create_index(
        'ix_ai_pipeline_runs_organization_id', 'ai_pipeline_runs', ['organization_id'], unique=False
    )
    op.create_index('ix_ai_pipeline_runs_status', 'ai_pipeline_runs', ['status'], unique=False)

    op.create_table(
        'ai_pipeline_stages',
        sa.Column('ai_pipeline_run_id', sa.Uuid(), nullable=False),
        sa.Column('stage_type', ai_pipeline_stage_type_enum, nullable=False),
        sa.Column('batch_index', sa.Integer(), nullable=True),
        sa.Column('status', ai_pipeline_stage_status_enum, nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('model_name', sa.String(length=200), nullable=True),
        sa.Column('prompt_version', sa.String(length=100), nullable=True),
        sa.Column('prompt_hash', sa.String(length=64), nullable=True),
        sa.Column('input_hash', sa.String(length=64), nullable=False),
        sa.Column('output_hash', sa.String(length=64), nullable=True),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('validated_output', sa.JSON(), nullable=True),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost', sa.Float(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['ai_pipeline_run_id'], ['ai_pipeline_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key', name='uq_ai_pipeline_stages_idempotency_key'),
        sa.CheckConstraint('attempt_count >= 0', name='ck_ai_pipeline_stages_attempt_count_non_negative'),
        sa.CheckConstraint('input_tokens >= 0', name='ck_ai_pipeline_stages_input_tokens_non_negative'),
        sa.CheckConstraint('output_tokens >= 0', name='ck_ai_pipeline_stages_output_tokens_non_negative'),
        sa.CheckConstraint(
            'batch_index IS NULL OR batch_index >= 0',
            name='ck_ai_pipeline_stages_batch_index_non_negative',
        ),
    )
    op.alter_column('ai_pipeline_stages', 'attempt_count', server_default=None)
    op.alter_column('ai_pipeline_stages', 'input_tokens', server_default=None)
    op.alter_column('ai_pipeline_stages', 'output_tokens', server_default=None)
    op.create_index(
        'ix_ai_pipeline_stages_ai_pipeline_run_id',
        'ai_pipeline_stages',
        ['ai_pipeline_run_id'],
        unique=False,
    )
    op.create_index('ix_ai_pipeline_stages_status', 'ai_pipeline_stages', ['status'], unique=False)
    op.create_index(
        'ix_ai_pipeline_stages_run_stage_type',
        'ai_pipeline_stages',
        ['ai_pipeline_run_id', 'stage_type'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ai_pipeline_stages_run_stage_type', table_name='ai_pipeline_stages')
    op.drop_index('ix_ai_pipeline_stages_status', table_name='ai_pipeline_stages')
    op.drop_index('ix_ai_pipeline_stages_ai_pipeline_run_id', table_name='ai_pipeline_stages')
    op.drop_table('ai_pipeline_stages')

    op.drop_index('ix_ai_pipeline_runs_status', table_name='ai_pipeline_runs')
    op.drop_index('ix_ai_pipeline_runs_organization_id', table_name='ai_pipeline_runs')
    op.drop_table('ai_pipeline_runs')

    bind = op.get_bind()
    sa.Enum(name='ai_pipeline_stage_status').drop(bind, checkfirst=True)
    sa.Enum(name='ai_pipeline_stage_type').drop(bind, checkfirst=True)
    sa.Enum(name='ai_pipeline_status').drop(bind, checkfirst=True)
