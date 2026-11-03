"""Explicit path-comparison view ranges, independent of story advancement."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0013"
down_revision = "0012"


def upgrade():
    op.create_table("comparison_exposures",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_id", sa.String(32), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.String(32), nullable=False),
        sa.Column("line_id", sa.String(32), nullable=False),
        sa.Column("node_id", sa.String(32), nullable=False),
        sa.Column("anchor", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_id", "scope_key"))
    for column in ("created_at", "owner_id", "resource_id", "line_id"):
        op.create_index("ix_comparison_exposures_"+column, "comparison_exposures", [column])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM comparison_exposures")):
        raise RuntimeError("Cannot downgrade saved comparison ranges; retain reader0013")
    op.drop_table("comparison_exposures")
