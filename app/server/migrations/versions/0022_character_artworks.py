"""Portable character avatars and full-body artwork pairs."""
from alembic import op
import sqlalchemy as sa

revision='0022'
down_revision='0021'
branch_labels=None
depends_on=None

def upgrade():
    from app.scenes.models import CharacterArtwork,CharacterLook
    bind=op.get_bind()
    # Interactive-world schema is optional and is also installed independently.
    if not bind.scalar(sa.text("SELECT to_regclass('world.instances')")):return
    CharacterArtwork.__table__.create(bind,checkfirst=True)
    CharacterLook.__table__.create(bind,checkfirst=True)
    columns={c['name'] for c in sa.inspect(bind).get_columns('character_looks',schema='world')}
    if 'selected_artwork_id' not in columns:
        op.add_column('character_looks',sa.Column('selected_artwork_id',sa.String(32),nullable=True),schema='world')
        op.create_foreign_key('fk_character_look_artwork','character_looks','character_artworks',['selected_artwork_id'],['id'],source_schema='world',referent_schema='world',ondelete='SET NULL')

def downgrade():
    bind=op.get_bind()
    if not bind.scalar(sa.text("SELECT to_regclass('world.character_artworks')")):return
    if bind.scalar(sa.text('SELECT count(*) FROM world.character_artworks')):
        raise RuntimeError('Cannot downgrade saved character artworks; export them first')
    if bind.scalar(sa.text("SELECT to_regclass('world.character_looks')")):
        op.drop_column('character_looks','selected_artwork_id',schema='world')
    op.drop_table('character_artworks',schema='world')
