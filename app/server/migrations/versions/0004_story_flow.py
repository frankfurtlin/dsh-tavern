"""Reader story flow and independently versioned spoiler preference."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"


def upgrade():
    op.add_column("user_settings", sa.Column("character_mode", sa.String(20), nullable=False, server_default="progress"))
    op.add_column("user_settings", sa.Column("character_mode_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("role_sessions", sa.Column("follow_progress", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("role_sessions", sa.Column("knowledge_mode", sa.String(20), nullable=False, server_default="progress"))
    op.add_column("role_sessions", sa.Column("card_id", sa.String(100)))


def downgrade():
    op.drop_column("role_sessions", "card_id")
    op.drop_column("role_sessions", "knowledge_mode")
    op.drop_column("role_sessions", "follow_progress")
    op.drop_column("user_settings", "character_mode_version")
    op.drop_column("user_settings", "character_mode")
