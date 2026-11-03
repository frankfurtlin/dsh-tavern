"""Private support receipts and manageable independent conversations."""
from alembic import op

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('''CREATE TABLE product_feedback (
      id VARCHAR(32) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL,
      owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      subject VARCHAR(120) NOT NULL, description TEXT NOT NULL, context JSONB NOT NULL,
      state VARCHAR(20) NOT NULL, reply TEXT NOT NULL, version INTEGER NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL);
      CREATE INDEX ix_product_feedback_owner_id ON product_feedback(owner_id);
      CREATE INDEX ix_product_feedback_created_at ON product_feedback(created_at);
      CREATE TABLE product_notices (
      id VARCHAR(32) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL,
      owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      title VARCHAR(160) NOT NULL, text TEXT NOT NULL, path VARCHAR(250) NOT NULL, read BOOLEAN NOT NULL);
      CREATE INDEX ix_product_notices_owner_id ON product_notices(owner_id);
      CREATE INDEX ix_product_notices_created_at ON product_notices(created_at);
      ALTER TABLE world_chat_sessions ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'active';
      ALTER TABLE world_chat_sessions ADD COLUMN updated_at TIMESTAMPTZ;
      UPDATE world_chat_sessions SET updated_at=created_at;
      ALTER TABLE world_chat_sessions ALTER COLUMN updated_at SET NOT NULL;
      CREATE INDEX ix_world_chat_sessions_updated_at ON world_chat_sessions(updated_at);
    ''')


def downgrade():
    op.drop_index('ix_world_chat_sessions_updated_at', 'world_chat_sessions')
    op.drop_column('world_chat_sessions', 'updated_at')
    op.drop_column('world_chat_sessions', 'status')
    op.drop_table('product_notices')
    op.drop_table('product_feedback')
