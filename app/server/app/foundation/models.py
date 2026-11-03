from datetime import datetime
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base
from ..models import Identity, Owned, now


class Account(Base):
    __tablename__ = "product_accounts"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="USER")
    display_name: Mapped[str] = mapped_column(String(100), default="")
    language: Mapped[str] = mapped_column(String(10), default="zh-CN")
    avatar_id: Mapped[str | None] = mapped_column(String(32))
    memo: Mapped[str] = mapped_column(Text, default="")
    __table_args__ = (CheckConstraint("role IN ('USER','ADMIN')"),)


class Config(Base):
    __tablename__ = "product_config"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


class ModelGrant(Base):
    __tablename__ = "product_model_grants"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    text_profile_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    image_profile_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    policy_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class VisionSettings(Base):
    __tablename__ = 'product_vision_settings'
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id',ondelete='CASCADE'),primary_key=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey('model_profiles.id',ondelete='SET NULL'))
    platform_profile_id: Mapped[str | None] = mapped_column(ForeignKey('model_profiles.id',ondelete='SET NULL'))
    managed: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)


class RecoveryCode(Base):
    __tablename__ = "product_recovery_codes"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    code_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthorPrompt(Owned, Base):
    __tablename__ = "product_author_prompts"
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500), default="")
    language: Mapped[str] = mapped_column(String(10), default="zh-CN")
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("status IN ('active','archived')"),)


class WorldPromptSelection(Base):
    __tablename__ = "product_world_prompts"
    world_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    prompt_ids: Mapped[list] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Audit(Identity, Base):
    __tablename__ = "product_audit"
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(160), unique=True)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Event(Identity, Base):
    __tablename__ = "product_events"
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    event_key: Mapped[str] = mapped_column(String(180), unique=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    category: Mapped[str] = mapped_column(String(30))
    path: Mapped[str] = mapped_column(String(200), default="")
    session_id: Mapped[str] = mapped_column(String(64), default="")
    anon_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class CreditLot(Owned, Base):
    __tablename__ = "product_credit_lots"
    bucket: Mapped[str] = mapped_column(String(16))
    amount: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    frozen: Mapped[int] = mapped_column(Integer, default=0)
    grant_key: Mapped[str] = mapped_column(String(180), unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("remaining >= 0 AND frozen >= 0 AND frozen <= remaining AND amount > 0"),
                     CheckConstraint("bucket IN ('DAILY','TEMPORARY','PERMANENT')"))


class Ledger(Owned, Base):
    __tablename__ = "product_ledger"
    entry_key: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Hold(Owned, Base):
    __tablename__ = "product_holds"
    business_key: Mapped[str] = mapped_column(String(160))
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    operation: Mapped[str] = mapped_column(String(60))
    reserved: Mapped[int] = mapped_column(Integer)
    settled: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(24), default="held")
    allocations: Mapped[list] = mapped_column(JSONB, default=list)
    price_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("owner_id", "business_key"), CheckConstraint("reserved >= 0 AND settled >= 0 AND settled <= reserved"))


class Product(Identity, Base):
    __tablename__ = "product_catalog"
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    credits: Mapped[int] = mapped_column(Integer, default=0)
    member_days: Mapped[int] = mapped_column(Integer, default=0)
    daily_credits: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)
    __table_args__ = (CheckConstraint("amount_minor > 0 AND credits >= 0 AND member_days >= 0 AND daily_credits >= 0"),)


class Order(Owned, Base):
    __tablename__ = "product_orders"
    request_key: Mapped[str] = mapped_column(String(160))
    product_id: Mapped[str] = mapped_column(ForeignKey("product_catalog.id"))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    provider: Mapped[str] = mapped_column(String(20))
    environment: Mapped[str] = mapped_column(String(16))
    merchant: Mapped[str] = mapped_column(String(100))
    provider_reference: Mapped[str | None] = mapped_column(String(160), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "request_key"),)


class PaymentReceipt(Identity, Base):
    __tablename__ = "product_payment_receipts"
    receipt_key: Mapped[str] = mapped_column(String(200), unique=True)
    payment_key: Mapped[str] = mapped_column(String(220), unique=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("product_orders.id", ondelete="SET NULL"))
    body_hash: Mapped[str] = mapped_column(String(64))
    verified: Mapped[bool] = mapped_column(default=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Membership(Owned, Base):
    __tablename__ = "product_memberships"
    grant_key: Mapped[str] = mapped_column(String(180), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    daily_credits: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(30))


class RedeemCard(Identity, Base):
    __tablename__ = "product_redeem_cards"
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    request_key: Mapped[str] = mapped_column(String(180), unique=True)
    credits: Mapped[int] = mapped_column(Integer, default=0)
    member_days: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    redeemed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invite(Identity, Base):
    __tablename__ = "product_invites"
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    creator_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    max_uses: Mapped[int] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    admission: Mapped[bool] = mapped_column(default=True)
    reward: Mapped[int] = mapped_column(Integer, default=0)
    disabled: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (CheckConstraint("uses >= 0 AND uses <= max_uses"),)


class InviteClaim(Owned, Base):
    __tablename__ = "product_invite_claims"
    invite_id: Mapped[str] = mapped_column(ForeignKey("product_invites.id"))
    __table_args__ = (UniqueConstraint("owner_id"),)


class StoredObject(Owned, Base):
    __tablename__ = "product_objects"
    name: Mapped[str] = mapped_column(String(200))
    object_key: Mapped[str] = mapped_column(String(200), unique=True)
    driver: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(120))
    size: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    rights: Mapped[str] = mapped_column(String(200), default="用户提供 · 权利未核验")
    purpose: Mapped[str] = mapped_column(String(20), default="attachment")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeDoc(Owned, Base):
    __tablename__ = "product_knowledge_docs"
    title: Mapped[str] = mapped_column(String(200))
    object_id: Mapped[str] = mapped_column(ForeignKey("product_objects.id", ondelete="CASCADE"))
    source_version: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16), default="author")
    status: Mapped[str] = mapped_column(String(16), default="ready")


class KnowledgeChunk(Identity, Base):
    __tablename__ = "product_knowledge_chunks"
    doc_id: Mapped[str] = mapped_column(ForeignKey("product_knowledge_docs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("doc_id", "ordinal"),)
