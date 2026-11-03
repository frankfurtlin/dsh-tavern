"""Private-content-free bookmark graves and queryable versioned delete receipts."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'

def upgrade():
    op.add_column('bookmarks', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('bookmarks', sa.Column('deletion_id', sa.String(32), nullable=True))

def downgrade():
    # Old code includes all bookmarks in lists and can replay successful create
    # keys. Never expose an empty-anchor grave or revive a deleted private note.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM bookmarks WHERE status='deleted' "
            'OR deleted_at IS NOT NULL OR deletion_id IS NOT NULL')):
        raise RuntimeError('Cannot downgrade while bookmark deletion tombstones exist; retain reader0008')
    op.drop_column('bookmarks', 'deletion_id')
    op.drop_column('bookmarks', 'deleted_at')
