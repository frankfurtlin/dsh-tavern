"""Durable owner-scoped source deletion receipts and cleanup outbox."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"


def upgrade():
    op.create_table("book_deletions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("book_id", sa.String(32), nullable=False),
        sa.Column("book_version", sa.Integer(), nullable=False),
        sa.Column("retained", postgresql.JSONB(), nullable=False),
        sa.Column("cleanup_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("cleanup_state", sa.String(20), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("owner_id", "book_id"))
    op.create_index("ix_book_deletions_owner_id", "book_deletions", ["owner_id"])
    op.create_index("ix_book_deletions_created_at", "book_deletions", ["created_at"])


def downgrade():
    op.drop_table("book_deletions")
