"""page_analysis design_asset source (ortak kaynak/provenance sozlesmesi)

Revision ID: c9a2f6e1b7d4
Revises: f2c8a5d1e7b3
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9a2f6e1b7d4'
down_revision: Union[str, Sequence[str], None] = 'f2c8a5d1e7b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    source_kind_enum = sa.Enum('URL', 'DESIGN_ASSET', name='page_analysis_source_kind')
    source_kind_enum.create(op.get_bind(), checkfirst=True)

    # Eski satirlar (`source_kind` henuz yok) guvenli bicimde 'URL' olarak
    # backfill edilir - hepsi zaten dolu bir `url` alanina sahip. Sutun once
    # bir server_default ile eklenir, backfill tamamlandiktan sonra
    # server_default kaldirilir (b2d6e1a9c7f4/e5b8f1a4c9d6 ile ayni desen).
    op.add_column(
        'page_analyses',
        sa.Column('source_kind', source_kind_enum, nullable=False, server_default='URL'),
    )
    op.alter_column('page_analyses', 'source_kind', server_default=None)

    # `design_assets.id` ASLA hard-delete edilmez (yalnizca soft-delete - bkz.
    # app.services.design_assets.delete_asset); yine de ileride bir hard-delete/
    # cascade olasiligina karsi ondelete='SET NULL' kullanilir (CASCADE DEGIL) -
    # tamamlanmis bir PageAnalysis snapshot'i (`screenshot_data` zaten bagimsiz
    # bir kopya) DesignAsset satiri silinse bile ETKILENMEMELIDIR.
    op.add_column('page_analyses', sa.Column('design_asset_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_page_analyses_design_asset_id',
        'page_analyses',
        'design_assets',
        ['design_asset_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_page_analyses_design_asset_id', 'page_analyses', ['design_asset_id'], unique=False)

    # DesignAsset kaynaginda `url` gecerli degildir.
    op.alter_column('page_analyses', 'url', existing_type=sa.String(length=2048), nullable=True)

    op.add_column('page_analyses', sa.Column('screenshot_content_type', sa.String(length=50), nullable=True))
    op.add_column('page_analyses', sa.Column('image_width', sa.Integer(), nullable=True))
    op.add_column('page_analyses', sa.Column('image_height', sa.Integer(), nullable=True))
    # Eski satirlarin toplu decode edilerek hash'inin backfill edilmesi
    # GUVENLI DEGIL (bellek/performans riski); alan nullable birakilir ve
    # yalnizca YENI tamamlanan capture'lar icin doldurulur (bkz.
    # app.services.page_analysis).
    op.add_column('page_analyses', sa.Column('content_sha256', sa.String(length=64), nullable=True))

    # Kaynak sozlesmesinin veritabani seviyesinde de tutarli kalmasini
    # saglar: 'URL' iken url dolu + design_asset_id bos, 'DESIGN_ASSET' iken
    # design_asset_id dolu + url bos. Backfill edilen eski satirlarin
    # tamami bu kurala zaten uyuyor (source_kind='URL', url dolu,
    # design_asset_id NULL).
    op.create_check_constraint(
        'ck_page_analyses_source_kind_consistency',
        'page_analyses',
        "(source_kind = 'URL' AND url IS NOT NULL AND design_asset_id IS NULL) OR "
        "(source_kind = 'DESIGN_ASSET' AND design_asset_id IS NOT NULL AND url IS NULL)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_page_analyses_source_kind_consistency', 'page_analyses', type_='check')
    op.drop_column('page_analyses', 'content_sha256')
    op.drop_column('page_analyses', 'image_height')
    op.drop_column('page_analyses', 'image_width')
    op.drop_column('page_analyses', 'screenshot_content_type')
    op.alter_column('page_analyses', 'url', existing_type=sa.String(length=2048), nullable=False)
    op.drop_index('ix_page_analyses_design_asset_id', table_name='page_analyses')
    op.drop_constraint('fk_page_analyses_design_asset_id', 'page_analyses', type_='foreignkey')
    op.drop_column('page_analyses', 'design_asset_id')
    op.drop_column('page_analyses', 'source_kind')
    sa.Enum(name='page_analysis_source_kind').drop(op.get_bind(), checkfirst=True)
