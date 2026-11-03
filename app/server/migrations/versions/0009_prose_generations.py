"""Versioned, streaming prose chapters and atomic prefix acceptance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0009'
down_revision = '0008'

def owned():
    return [sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner_id', sa.String(32), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)]

def upgrade():
    op.create_table('generation_batches', *owned(),
        sa.Column('task_id', sa.String(32), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('book_id', sa.String(32), sa.ForeignKey('books.id', ondelete='CASCADE'), nullable=False),
        sa.Column('origin_anchor', JSONB, nullable=False), sa.Column('request', JSONB, nullable=False),
        sa.Column('profile_snapshot', JSONB, nullable=False),
        sa.Column('candidate_version', sa.Integer, nullable=False), sa.Column('accepted_prefix', sa.Integer, nullable=False),
        sa.Column('target_branch_id', sa.String(32)), sa.Column('last_accepted_revision_id', sa.String(32)),
        sa.Column('version_times', JSONB, nullable=False))
    op.create_table('generation_chapter_versions', *owned(),
        sa.Column('generation_id', sa.String(32), sa.ForeignKey('generation_batches.id', ondelete='CASCADE'), nullable=False),
        sa.Column('candidate_version', sa.Integer, nullable=False), sa.Column('chapter_index', sa.Integer, nullable=False),
        sa.Column('content_id', sa.String(32), nullable=False), sa.Column('title', sa.String(100), nullable=False),
        sa.Column('text', sa.Text, nullable=False), sa.Column('content_hash', sa.String(64)),
        sa.Column('stream_version', sa.Integer, nullable=False), sa.Column('snapshot_version', sa.Integer, nullable=False),
        sa.Column('completeness', sa.String(20), nullable=False), sa.Column('durability', sa.String(20), nullable=False),
        sa.Column('hints', JSONB, nullable=False), sa.UniqueConstraint('generation_id', 'candidate_version', 'chapter_index'))
    op.create_table('generation_acceptances', *owned(),
        sa.Column('generation_id', sa.String(32), sa.ForeignKey('generation_batches.id', ondelete='CASCADE'), nullable=False),
        sa.Column('candidate_version', sa.Integer, nullable=False), sa.Column('prefix_count', sa.Integer, nullable=False),
        sa.Column('revision_id', sa.String(32), nullable=False), sa.Column('result', JSONB, nullable=False),
        sa.UniqueConstraint('generation_id', 'candidate_version', 'prefix_count'))
    for table in ('generation_batches', 'generation_chapter_versions', 'generation_acceptances'):
        op.create_index('ix_' + table + '_created_at', table, ['created_at'])
        op.create_index('ix_' + table + '_owner_id', table, ['owner_id'])
    op.create_index('ix_generation_batches_book_id', 'generation_batches', ['book_id'])
    for table in ('generation_chapter_versions', 'generation_acceptances'):
        op.create_index('ix_' + table + '_generation_id', table, ['generation_id'])

def downgrade():
    # Old releases cannot display, recover or delete these private drafts.
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM generation_batches')):
        raise RuntimeError('Cannot downgrade while prose generations exist; retain reader0009')
    for table in ('generation_acceptances', 'generation_chapter_versions', 'generation_batches'):
        op.drop_table(table)
