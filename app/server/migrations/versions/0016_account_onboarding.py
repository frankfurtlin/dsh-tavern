"""Account recovery and ownership of platform-provided model channels."""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE product_model_grants (
        owner_id VARCHAR(32) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        text_profile_id VARCHAR(32) UNIQUE,
        image_profile_id VARCHAR(32) UNIQUE,
        policy_version INTEGER NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL
    )""")
    op.execute("""CREATE TABLE product_recovery_codes (
        owner_id VARCHAR(32) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        code_hash VARCHAR(64),
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        used_at TIMESTAMP WITH TIME ZONE
    )""")


def downgrade():
    op.drop_table("product_recovery_codes")
    op.drop_table("product_model_grants")
