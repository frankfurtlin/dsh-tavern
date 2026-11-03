"""A distinct, account-scoped vision channel; never replace the story model."""
from alembic import op
revision='0019'
down_revision='0018'
branch_labels=None
depends_on=None
def upgrade():
    op.execute('''CREATE TABLE product_vision_settings (
        owner_id VARCHAR(32) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        profile_id VARCHAR(32) REFERENCES model_profiles(id) ON DELETE SET NULL,
        platform_profile_id VARCHAR(32) REFERENCES model_profiles(id) ON DELETE SET NULL,
        managed BOOLEAN NOT NULL, version INTEGER NOT NULL)''')
def downgrade():op.drop_table('product_vision_settings')
