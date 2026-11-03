"""Versioned author configuration and moderated public scenario snapshots."""
from alembic import op
revision='0018'
down_revision='0017'
branch_labels=None
depends_on=None


def upgrade():
    op.execute('''CREATE TABLE product_author_drafts (
        world_id VARCHAR(32) PRIMARY KEY, owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        version INTEGER NOT NULL, value JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL)''')
    op.create_index('ix_product_author_drafts_owner_id','product_author_drafts',['owner_id'])
    op.execute('''CREATE TABLE product_publications (
        id VARCHAR(32) PRIMARY KEY, owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL, world_id VARCHAR(32) NOT NULL UNIQUE, version INTEGER NOT NULL,
        state VARCHAR(20) NOT NULL CHECK(state IN ('pending','published','rejected','withdrawn','removed')),
        live BOOLEAN NOT NULL, submitted JSONB NOT NULL, approved JSONB NOT NULL, note TEXT NOT NULL,
        reviewer_id VARCHAR(32) REFERENCES users(id) ON DELETE SET NULL, updated_at TIMESTAMPTZ NOT NULL)''')
    op.create_index('ix_product_publications_owner_id','product_publications',['owner_id'])
    op.create_index('ix_product_publications_live','product_publications',['live'])
    for name,extra in [('favorites',''),('reports',", reason TEXT NOT NULL, state VARCHAR(20) NOT NULL")]:
        table='product_public_'+name
        op.execute(f'''CREATE TABLE {table} (id VARCHAR(32) PRIMARY KEY,
            owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE, created_at TIMESTAMPTZ NOT NULL,
            publication_id VARCHAR(32) NOT NULL REFERENCES product_publications(id) ON DELETE CASCADE
            {extra}, UNIQUE(owner_id,publication_id))''')
        op.create_index('ix_'+table+'_owner_id',table,['owner_id'])
        op.create_index('ix_'+table+'_publication_id',table,['publication_id'])


def downgrade():
    for name in ('product_public_reports','product_public_favorites','product_publications','product_author_drafts'):op.drop_table(name)
