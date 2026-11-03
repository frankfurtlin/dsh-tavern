"""Safe numeric accounting only; no provider text or secrets."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0014'
down_revision='0013'


def upgrade():
    op.add_column('usage',sa.Column('billing_metadata',JSONB,nullable=False,server_default=sa.text("'{}'::jsonb")))


def downgrade():
    # Unknown-only legacy calls remain compatible with earlier roundtrips.
    # Refuse before DDL whenever a received numeric detail would be lost.
    if op.get_bind().scalar(sa.text("""SELECT count(*) FROM usage WHERE
        (billing_metadata->'billing'->'reasoningTokens' IS NOT NULL AND
         billing_metadata->'billing'->'reasoningTokens' <> 'null'::jsonb) OR
        (billing_metadata->'billing'->'cost' IS NOT NULL AND
         billing_metadata->'billing'->'cost' <> 'null'::jsonb)""")):
        raise RuntimeError('Cannot discard received accounting metadata; retain reader0014')
    op.drop_column('usage','billing_metadata')
