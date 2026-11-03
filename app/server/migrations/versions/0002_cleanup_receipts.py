"""Keep a content-free deletion receipt after the user's cascading cleanup."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.create_table("cleanup_receipts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.String(32), nullable=False, unique=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_cleanup_receipts_created_at", "cleanup_receipts", ["created_at"])


def downgrade():
    op.drop_table("cleanup_receipts")
