"""CAS reading/speech preferences and completed public-paragraph positions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0012'
down_revision = '0011'
DEFAULT = '{"rate": 1, "voiceId": "", "sleepMinutes": 0, "stopAtChapterEnd": false}'


def upgrade():
    op.add_column('user_settings', sa.Column('speech_preferences', JSONB, nullable=False,
        server_default=sa.text("'" + DEFAULT + "'::jsonb")))
    op.create_table('playback_positions',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('owner_id', sa.String(32), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('scope_key', sa.String(64), nullable=False),
        sa.Column('story_id', sa.String(38), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False), sa.Column('mode', sa.String(20), nullable=False),
        sa.Column('line_id', sa.String(32), nullable=False), sa.Column('content_revision', sa.String(80), nullable=False),
        sa.Column('context', JSONB, nullable=False), sa.Column('paragraph_id', sa.String(100), nullable=False),
        sa.Column('last_completed_anchor', JSONB, nullable=False), sa.Column('max_heard_anchor', JSONB, nullable=False),
        sa.Column('ended', sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('book_id', sa.String(32), sa.ForeignKey('books.id', ondelete='CASCADE')),
        sa.Column('world_id', sa.String(32)),
        sa.Column('generation_id', sa.String(32), sa.ForeignKey('generation_batches.id', ondelete='CASCADE')),
        sa.Column('candidate_version', sa.Integer), sa.UniqueConstraint('owner_id', 'scope_key'))
    for name in ('created_at', 'owner_id', 'book_id', 'world_id'):
        op.create_index('ix_playback_positions_' + name, 'playback_positions', [name])


def downgrade():
    db = op.get_bind()
    if db.scalar(sa.text('SELECT count(*) FROM playback_positions')) or db.scalar(sa.text(
            'SELECT count(*) FROM user_settings WHERE speech_preferences != CAST(:value AS jsonb)'), {'value': DEFAULT}):
        raise RuntimeError('Cannot downgrade completed playback or speech preferences; retain reader0012')
    op.drop_table('playback_positions')
    op.drop_column('user_settings', 'speech_preferences')
