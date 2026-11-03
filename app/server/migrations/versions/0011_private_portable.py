"""Owner-bound private backup, restore and reading-export receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0011'
down_revision = '0010'


def upgrade():
    op.create_table('portable_operations',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner_id', sa.String(32), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('request_key', sa.String(160), nullable=False),
        sa.Column('payload_hash', sa.String(64), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='preparing'),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('stage_name', sa.String(100)), sa.Column('manifest_hash', sa.String(64)),
        sa.Column('counts', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('result', JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('error_code', sa.String(100)),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('owner_id', 'kind', 'request_key'))
    for name in ('created_at', 'owner_id', 'expires_at'):
        op.create_index('ix_portable_operations_' + name, 'portable_operations', [name])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM portable_operations')):
        raise RuntimeError('Cannot downgrade while private transfer receipts exist; retain reader0011')
    op.drop_table('portable_operations')
