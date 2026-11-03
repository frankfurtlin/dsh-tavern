"""Owner-authorized image bytes and exact-scope placements."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0010'
down_revision = '0009'


def owned():
    return [sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner_id', sa.String(32), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)]


def upgrade():
    op.create_table('image_assets', *owned(),
        sa.Column('purpose', sa.String(20), nullable=False),
        sa.Column('source_kind', sa.String(40), nullable=False, server_default='user_upload'),
        sa.Column('license_note', sa.String(1000), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('mime', sa.String(40), nullable=False),
        sa.Column('width', sa.Integer, nullable=False), sa.Column('height', sa.Integer, nullable=False),
        sa.Column('origin_scope', JSONB, nullable=False), sa.Column('crop', JSONB, nullable=False),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.UniqueConstraint('id', 'owner_id'))
    op.create_table('image_asset_variants',
        sa.Column('asset_id', sa.String(32), sa.ForeignKey('image_assets.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('name', sa.String(24), primary_key=True),
        sa.Column('width', sa.Integer, nullable=False), sa.Column('height', sa.Integer, nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False), sa.Column('mime', sa.String(40), nullable=False),
        sa.Column('data', sa.LargeBinary, nullable=False))
    op.create_table('image_asset_placements', *owned(),
        sa.Column('asset_id', sa.String(32), sa.ForeignKey('image_assets.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('scope', JSONB, nullable=False), sa.Column('purpose', sa.String(20), nullable=False),
        sa.Column('content_id', sa.String(32)), sa.Column('paragraph_id', sa.String(80)),
        sa.Column('side', sa.String(16), nullable=False, server_default='after'),
        sa.Column('order', sa.Integer, nullable=False, server_default='0'),
        sa.Column('alt', sa.String(300), nullable=False, server_default=''),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'))
    for table in ('image_assets', 'image_asset_placements'):
        op.create_index('ix_' + table + '_created_at', table, ['created_at'])
        op.create_index('ix_' + table + '_owner_id', table, ['owner_id'])
    op.create_index('ix_image_asset_placements_asset_id', 'image_asset_placements', ['asset_id'])


def downgrade():
    for table in ('image_assets', 'image_asset_variants', 'image_asset_placements'):
        if op.get_bind().scalar(sa.text('SELECT count(*) FROM ' + table)):
            raise RuntimeError('Cannot downgrade while private images or placements exist; retain reader0010')
    for table in ('image_asset_placements', 'image_asset_variants', 'image_assets'):
        op.drop_table(table)
