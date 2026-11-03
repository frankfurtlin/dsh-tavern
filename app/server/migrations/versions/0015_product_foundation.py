"""Pinned product schema; preserves existing user/story/model columns."""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

STATEMENTS = [
    """CREATE TABLE product_catalog (
	sku VARCHAR(64) NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	kind VARCHAR(16) NOT NULL, 
	amount_minor INTEGER NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	credits INTEGER NOT NULL, 
	member_days INTEGER NOT NULL, 
	daily_credits INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (amount_minor > 0 AND credits >= 0 AND member_days >= 0 AND daily_credits >= 0), 
	UNIQUE (sku)
)""",
    """CREATE INDEX ix_product_catalog_created_at ON product_catalog (created_at)""",
    """CREATE TABLE product_config (
	key VARCHAR(80) NOT NULL, 
	value JSONB NOT NULL, 
	PRIMARY KEY (key)
)""",
    """CREATE TABLE product_accounts (
	owner_id VARCHAR(32) NOT NULL, 
	role VARCHAR(16) NOT NULL, 
	display_name VARCHAR(100) NOT NULL, 
	language VARCHAR(10) NOT NULL, 
	avatar_id VARCHAR(32), 
	memo TEXT NOT NULL, 
	PRIMARY KEY (owner_id), 
	CHECK (role IN ('USER','ADMIN')), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE TABLE product_audit (
	actor_id VARCHAR(32), 
	action VARCHAR(80) NOT NULL, 
	target_id VARCHAR(64), 
	request_key VARCHAR(160) NOT NULL, 
	details JSONB NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL, 
	UNIQUE (request_key)
)""",
    """CREATE INDEX ix_product_audit_actor_id ON product_audit (actor_id)""",
    """CREATE INDEX ix_product_audit_created_at ON product_audit (created_at)""",
    """CREATE TABLE product_credit_lots (
	bucket VARCHAR(16) NOT NULL, 
	amount INTEGER NOT NULL, 
	remaining INTEGER NOT NULL, 
	frozen INTEGER NOT NULL, 
	grant_key VARCHAR(180) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (remaining >= 0 AND frozen >= 0 AND frozen <= remaining AND amount > 0), 
	CHECK (bucket IN ('DAILY','TEMPORARY','PERMANENT')), 
	UNIQUE (grant_key), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_credit_lots_created_at ON product_credit_lots (created_at)""",
    """CREATE INDEX ix_product_credit_lots_owner_id ON product_credit_lots (owner_id)""",
    """CREATE TABLE product_events (
	owner_id VARCHAR(32), 
	event_key VARCHAR(180) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	category VARCHAR(30) NOT NULL, 
	path VARCHAR(200) NOT NULL, 
	session_id VARCHAR(64) NOT NULL, 
	anon_id VARCHAR(64) NOT NULL, 
	data JSONB NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE SET NULL, 
	UNIQUE (event_key)
)""",
    """CREATE INDEX ix_product_events_created_at ON product_events (created_at)""",
    """CREATE INDEX ix_product_events_name ON product_events (name)""",
    """CREATE INDEX ix_product_events_owner_id ON product_events (owner_id)""",
    """CREATE TABLE product_holds (
	business_key VARCHAR(160) NOT NULL, 
	task_id VARCHAR(32) NOT NULL, 
	operation VARCHAR(60) NOT NULL, 
	reserved INTEGER NOT NULL, 
	settled INTEGER NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	allocations JSONB NOT NULL, 
	price_snapshot JSONB NOT NULL, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (owner_id, business_key), 
	CHECK (reserved >= 0 AND settled >= 0 AND settled <= reserved), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_holds_created_at ON product_holds (created_at)""",
    """CREATE INDEX ix_product_holds_owner_id ON product_holds (owner_id)""",
    """CREATE INDEX ix_product_holds_task_id ON product_holds (task_id)""",
    """CREATE TABLE product_invites (
	code_hash VARCHAR(64) NOT NULL, 
	label VARCHAR(100) NOT NULL, 
	creator_id VARCHAR(32), 
	max_uses INTEGER NOT NULL, 
	uses INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	admission BOOLEAN NOT NULL, 
	reward INTEGER NOT NULL, 
	disabled BOOLEAN NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CHECK (uses >= 0 AND uses <= max_uses), 
	UNIQUE (code_hash), 
	FOREIGN KEY(creator_id) REFERENCES users (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_product_invites_created_at ON product_invites (created_at)""",
    """CREATE TABLE product_ledger (
	entry_key VARCHAR(180) NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	amount INTEGER NOT NULL, 
	reference VARCHAR(100) NOT NULL, 
	details JSONB NOT NULL, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (entry_key), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_ledger_created_at ON product_ledger (created_at)""",
    """CREATE INDEX ix_product_ledger_owner_id ON product_ledger (owner_id)""",
    """CREATE TABLE product_memberships (
	grant_key VARCHAR(180) NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	ends_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	daily_credits INTEGER NOT NULL, 
	source VARCHAR(30) NOT NULL, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (grant_key), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_memberships_created_at ON product_memberships (created_at)""",
    """CREATE INDEX ix_product_memberships_owner_id ON product_memberships (owner_id)""",
    """CREATE TABLE product_objects (
	name VARCHAR(200) NOT NULL, 
	object_key VARCHAR(200) NOT NULL, 
	driver VARCHAR(16) NOT NULL, 
	content_type VARCHAR(120) NOT NULL, 
	size INTEGER NOT NULL, 
	checksum VARCHAR(64) NOT NULL, 
	rights VARCHAR(200) NOT NULL, 
	purpose VARCHAR(20) NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (object_key), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_objects_created_at ON product_objects (created_at)""",
    """CREATE INDEX ix_product_objects_owner_id ON product_objects (owner_id)""",
    """CREATE TABLE product_orders (
	request_key VARCHAR(160) NOT NULL, 
	product_id VARCHAR(32) NOT NULL, 
	snapshot JSONB NOT NULL, 
	state VARCHAR(24) NOT NULL, 
	provider VARCHAR(20) NOT NULL, 
	environment VARCHAR(16) NOT NULL, 
	merchant VARCHAR(100) NOT NULL, 
	provider_reference VARCHAR(160), 
	checkout_url TEXT, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (owner_id, request_key), 
	FOREIGN KEY(product_id) REFERENCES product_catalog (id), 
	UNIQUE (provider_reference), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_orders_created_at ON product_orders (created_at)""",
    """CREATE INDEX ix_product_orders_owner_id ON product_orders (owner_id)""",
    """CREATE TABLE product_redeem_cards (
	code_hash VARCHAR(64) NOT NULL, 
	request_key VARCHAR(180) NOT NULL, 
	credits INTEGER NOT NULL, 
	member_days INTEGER NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	redeemed_by VARCHAR(32), 
	redeemed_at TIMESTAMP WITH TIME ZONE, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code_hash), 
	UNIQUE (request_key), 
	FOREIGN KEY(redeemed_by) REFERENCES users (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_product_redeem_cards_created_at ON product_redeem_cards (created_at)""",
    """CREATE TABLE product_invite_claims (
	invite_id VARCHAR(32) NOT NULL, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (owner_id), 
	FOREIGN KEY(invite_id) REFERENCES product_invites (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_invite_claims_created_at ON product_invite_claims (created_at)""",
    """CREATE INDEX ix_product_invite_claims_owner_id ON product_invite_claims (owner_id)""",
    """CREATE TABLE product_knowledge_docs (
	title VARCHAR(200) NOT NULL, 
	object_id VARCHAR(32) NOT NULL, 
	source_version VARCHAR(64) NOT NULL, 
	scope VARCHAR(16) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	owner_id VARCHAR(32) NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(object_id) REFERENCES product_objects (id) ON DELETE CASCADE, 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_knowledge_docs_created_at ON product_knowledge_docs (created_at)""",
    """CREATE INDEX ix_product_knowledge_docs_owner_id ON product_knowledge_docs (owner_id)""",
    """CREATE TABLE product_payment_receipts (
	receipt_key VARCHAR(200) NOT NULL, 
	payment_key VARCHAR(220) NOT NULL, 
	order_id VARCHAR(32), 
	body_hash VARCHAR(64) NOT NULL, 
	verified BOOLEAN NOT NULL, 
	details JSONB NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (receipt_key), 
	UNIQUE (payment_key), 
	FOREIGN KEY(order_id) REFERENCES product_orders (id) ON DELETE SET NULL
)""",
    """CREATE INDEX ix_product_payment_receipts_created_at ON product_payment_receipts (created_at)""",
    """CREATE TABLE product_knowledge_chunks (
	doc_id VARCHAR(32) NOT NULL, 
	ordinal INTEGER NOT NULL, 
	text TEXT NOT NULL, 
	id VARCHAR(32) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (doc_id, ordinal), 
	FOREIGN KEY(doc_id) REFERENCES product_knowledge_docs (id) ON DELETE CASCADE
)""",
    """CREATE INDEX ix_product_knowledge_chunks_created_at ON product_knowledge_chunks (created_at)""",
    """CREATE INDEX ix_product_knowledge_chunks_doc_id ON product_knowledge_chunks (doc_id)""",
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    bind = op.get_bind()
    for name in ("product_ledger", "product_orders", "product_memberships", "product_payment_receipts"):
        if bind.scalar(sa.text("SELECT count(*) FROM " + name)):
            raise RuntimeError("Cannot discard product financial records; retain reader0015")
    op.drop_table('product_knowledge_chunks')
    op.drop_table('product_payment_receipts')
    op.drop_table('product_knowledge_docs')
    op.drop_table('product_invite_claims')
    op.drop_table('product_redeem_cards')
    op.drop_table('product_orders')
    op.drop_table('product_objects')
    op.drop_table('product_memberships')
    op.drop_table('product_ledger')
    op.drop_table('product_invites')
    op.drop_table('product_holds')
    op.drop_table('product_events')
    op.drop_table('product_credit_lots')
    op.drop_table('product_audit')
    op.drop_table('product_accounts')
    op.drop_table('product_config')
    op.drop_table('product_catalog')
