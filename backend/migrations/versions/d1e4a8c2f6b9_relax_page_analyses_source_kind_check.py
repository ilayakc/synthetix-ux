"""relax page_analyses source_kind check for FK ON DELETE SET NULL (Paket 4A hardening)

Revision ID: d1e4a8c2f6b9
Revises: c9a2f6e1b7d4
Create Date: 2026-07-24 00:00:00.000000

`c9a2f6e1b7d4`'teki orijinal CHECK constraint (DEGISTIRILMEDI - uygulanmis
migration dosyalari geriye donuk duzenlenmez):

    (source_kind = 'URL' AND url IS NOT NULL AND design_asset_id IS NULL) OR
    (source_kind = 'DESIGN_ASSET' AND design_asset_id IS NOT NULL AND url IS NULL)

Bu, `fk_page_analyses_design_asset_id`'nin `ondelete='SET NULL'` davranisiyla
CELISIYORDU: bir DesignAsset satiri gercekten hard-delete edilirse, FK
`design_asset_id`'yi NULL'a ceker ama `source_kind` hala 'DESIGN_ASSET'
kalir - bu, YUKARIDAKI CHECK constraint'i ihlal eder ve Postgres, hard-delete
DELETE ifadesinin KENDISINI bir constraint-violation hatasiyla REDDEDER
(bkz. bu revizyonun docstring'inde aciklanan gercek DB testi).

Duzeltme: constraint'i, 'DESIGN_ASSET' durumunda `design_asset_id`'nin NULL
OLABILECEGI sekilde GEVSETIR (yalnizca DB-seviyesinde; uygulama/servis
katmani - `app.services.page_analysis.create_analysis` - normal olusturma
sirasinda `design_asset_id`'yi HER ZAMAN doldurur, bu asla API uzerinden
NULL birakilamaz). `source_kind='DESIGN_ASSET' AND design_asset_id IS NULL`
durumu yalnizca "kaynak DesignAsset sonradan hard-delete edildi ama
tamamlanmis PageAnalysis snapshot'i (screenshot_data bagimsiz bir kopya
oldugu icin) korunuyor" anlamina gelir - bkz. app.routers.page_analysis
`design_asset_still_linked` yaniti.

URL kaynaginin invariant'i DEGISMEDEN korunur (`url IS NOT NULL AND
design_asset_id IS NULL`).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'd1e4a8c2f6b9'
down_revision: Union[str, Sequence[str], None] = 'c9a2f6e1b7d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_CONSTRAINT_EXPR = (
    "(source_kind = 'URL' AND url IS NOT NULL AND design_asset_id IS NULL) OR "
    "(source_kind = 'DESIGN_ASSET' AND design_asset_id IS NOT NULL AND url IS NULL)"
)

_NEW_CONSTRAINT_EXPR = (
    "(source_kind = 'URL' AND url IS NOT NULL AND design_asset_id IS NULL) OR "
    "(source_kind = 'DESIGN_ASSET' AND url IS NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint('ck_page_analyses_source_kind_consistency', 'page_analyses', type_='check')
    op.create_check_constraint(
        'ck_page_analyses_source_kind_consistency',
        'page_analyses',
        _NEW_CONSTRAINT_EXPR,
    )


def downgrade() -> None:
    """Downgrade schema.

    UYARI: eski (siki) constraint'e geri donmek, hard-delete sonrasi
    `design_asset_id=NULL` kalmis herhangi bir 'DESIGN_ASSET' satiri VARSA
    basarisiz olur (bu, orijinal migration'in zaten sahip oldugu kisitla
    ayni durum - downgrade kasitli olarak veriyi "duzeltmez").
    """
    op.drop_constraint('ck_page_analyses_source_kind_consistency', 'page_analyses', type_='check')
    op.create_check_constraint(
        'ck_page_analyses_source_kind_consistency',
        'page_analyses',
        _OLD_CONSTRAINT_EXPR,
    )
