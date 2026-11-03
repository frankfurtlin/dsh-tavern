"""Independent story navigation and compatible reader preferences."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"


def upgrade():
    op.add_column("user_settings", sa.Column("reading_mode", sa.String(20), nullable=False, server_default="scroll"))
    op.add_column("user_settings", sa.Column("reading_width", sa.Integer(), nullable=False, server_default="36"))
    op.add_column("user_settings", sa.Column("chapter_length", sa.String(20), nullable=False, server_default="standard"))
    op.create_table("resume_targets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("story_id", sa.String(38), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "story_id", "mode"))
    op.create_index("ix_resume_targets_owner_id", "resume_targets", ["owner_id"])
    op.create_index("ix_resume_targets_created_at", "resume_targets", ["created_at"])


def downgrade():
    op.drop_table("resume_targets")
    op.drop_column("user_settings", "chapter_length")
    op.drop_column("user_settings", "reading_width")
    op.drop_column("user_settings", "reading_mode")
