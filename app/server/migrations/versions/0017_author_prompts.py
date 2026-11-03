"""Private author prompts and world selections; world schema stays independent."""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE product_author_prompts (
        id VARCHAR(32) PRIMARY KEY,
        owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
        title VARCHAR(100) NOT NULL, description VARCHAR(500) NOT NULL,
        language VARCHAR(10) NOT NULL, content TEXT NOT NULL,
        status VARCHAR(16) NOT NULL CHECK(status IN ('active','archived')),
        version INTEGER NOT NULL
    )""")
    op.create_index('ix_product_author_prompts_owner_id', 'product_author_prompts', ['owner_id'])
    op.create_index('ix_product_author_prompts_created_at', 'product_author_prompts', ['created_at'])
    op.execute("""CREATE TABLE product_world_prompts (
        world_id VARCHAR(32) PRIMARY KEY,
        owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        prompt_ids JSONB NOT NULL, version INTEGER NOT NULL
    )""")
    op.create_index('ix_product_world_prompts_owner_id', 'product_world_prompts', ['owner_id'])


def downgrade():
    op.drop_table('product_world_prompts')
    op.drop_table('product_author_prompts')
