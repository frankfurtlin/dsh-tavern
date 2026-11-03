"""Versioned editorial memory and world-external character sessions."""
from alembic import op
revision='0020'
down_revision='0019'
branch_labels=None
depends_on=None

def upgrade():
    # World IDs are authorized against the separate world schema. Reader
    # migrations must also run before that schema exists on a fresh install.
    op.execute('''CREATE TABLE play_controls (
      id VARCHAR(32) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      scope_kind VARCHAR(20) NOT NULL, scope_id VARCHAR(32) NOT NULL, world_id VARCHAR(32) NOT NULL,
      profile_id VARCHAR(32) REFERENCES model_profiles(id) ON DELETE SET NULL, version INTEGER NOT NULL, memory JSONB NOT NULL,
      UNIQUE(owner_id,scope_kind,scope_id)); CREATE INDEX ix_play_controls_world_id ON play_controls(world_id);
      CREATE INDEX ix_play_controls_owner_id ON play_controls(owner_id); CREATE INDEX ix_play_controls_created_at ON play_controls(created_at);
      CREATE TABLE play_memory_revisions (
      id VARCHAR(32) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      control_id VARCHAR(32) NOT NULL REFERENCES play_controls(id) ON DELETE CASCADE, version INTEGER NOT NULL, memory JSONB NOT NULL,
      UNIQUE(control_id,version)); CREATE INDEX ix_play_memory_revisions_control_id ON play_memory_revisions(control_id);
      CREATE INDEX ix_play_memory_revisions_owner_id ON play_memory_revisions(owner_id); CREATE INDEX ix_play_memory_revisions_created_at ON play_memory_revisions(created_at);
      CREATE TABLE world_chat_sessions (
      id VARCHAR(32) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      world_id VARCHAR(32) NOT NULL, line_id VARCHAR(32) NOT NULL, checkpoint_id VARCHAR(32) NOT NULL,
      character_id VARCHAR(80) NOT NULL, character_name VARCHAR(100) NOT NULL, name VARCHAR(120) NOT NULL, version INTEGER NOT NULL,
      snapshot JSONB NOT NULL, denied_phrases JSONB NOT NULL, pending_task_id VARCHAR(32));
      CREATE INDEX ix_world_chat_sessions_owner_id ON world_chat_sessions(owner_id); CREATE INDEX ix_world_chat_sessions_created_at ON world_chat_sessions(created_at);
      CREATE INDEX ix_world_chat_sessions_world_id ON world_chat_sessions(world_id); CREATE INDEX ix_world_chat_sessions_line_id ON world_chat_sessions(line_id);
      CREATE TABLE world_chat_messages (
      id VARCHAR(32) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, owner_id VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      session_id VARCHAR(32) NOT NULL REFERENCES world_chat_sessions(id) ON DELETE CASCADE, task_id VARCHAR(32) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
      turn_key VARCHAR(100) NOT NULL, sequence INTEGER NOT NULL, role VARCHAR(20) NOT NULL, text TEXT NOT NULL, status VARCHAR(20) NOT NULL,
      checkpoint_id VARCHAR(32) NOT NULL, model_name VARCHAR(150) NOT NULL, UNIQUE(session_id,turn_key,role), UNIQUE(session_id,sequence));
      CREATE INDEX ix_world_chat_messages_owner_id ON world_chat_messages(owner_id); CREATE INDEX ix_world_chat_messages_created_at ON world_chat_messages(created_at);
      CREATE INDEX ix_world_chat_messages_session_id ON world_chat_messages(session_id); CREATE INDEX ix_world_chat_messages_task_id ON world_chat_messages(task_id);''')

def downgrade():
    for table in ('world_chat_messages','world_chat_sessions','play_memory_revisions','play_controls'):op.drop_table(table)
