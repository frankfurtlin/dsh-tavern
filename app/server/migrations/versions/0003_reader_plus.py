"""Account default model, private reading notes, bookshelf and real activity."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column("user_settings", sa.Column("default_profile_id", sa.String(32)))
    op.add_column("user_settings", sa.Column("reading_goal_minutes", sa.Integer(), nullable=False, server_default="20"))
    op.add_column("user_settings", sa.Column("reading_goal_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("books", sa.Column("favorite", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("books", sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.alter_column("bookmarks", "note", type_=sa.Text(), existing_type=sa.String(200), existing_nullable=False)
    op.add_column("bookmarks", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("reading_activity",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.String(32), nullable=False),
        sa.Column("revision_id", sa.String(32), nullable=False),
        sa.Column("event_id", sa.String(100), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "event_id"))
    op.create_index("ix_reading_activity_owner_id", "reading_activity", ["owner_id"])
    op.create_index("ix_reading_activity_book_id", "reading_activity", ["book_id"])
    op.create_index("ix_reading_activity_created_at", "reading_activity", ["created_at"])


def downgrade():
    op.drop_table("reading_activity")
    op.drop_column("bookmarks", "version")
    # Retain existing long notes; narrowing would destroy user data.
    op.drop_column("books", "tags")
    op.drop_column("books", "favorite")
    op.drop_column("user_settings", "reading_goal_version")
    op.drop_column("user_settings", "reading_goal_minutes")
    op.drop_column("user_settings", "default_profile_id")
