from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Text, Integer, DateTime, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def uid():
    return uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Identity:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Owned(Identity):
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class User(Identity, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")


class UserSettings(Base):
    __tablename__ = "user_settings"
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    font_size: Mapped[int] = mapped_column(default=18)
    line_height: Mapped[str] = mapped_column(default="1.8")
    theme: Mapped[str] = mapped_column(default="light")
    default_profile_id: Mapped[str | None] = mapped_column(String(32))
    reading_goal_minutes: Mapped[int] = mapped_column(default=20)
    reading_goal_version: Mapped[int] = mapped_column(default=1)
    character_mode: Mapped[str] = mapped_column(String(20), default="progress")
    character_mode_version: Mapped[int] = mapped_column(default=1)
    reading_mode: Mapped[str] = mapped_column(String(20), default="scroll")
    reading_width: Mapped[int] = mapped_column(Integer, default=36)
    chapter_length: Mapped[str] = mapped_column(String(20), default="standard")
    speech_preferences: Mapped[dict] = mapped_column(JSONB, default=lambda: {
        "rate": 1, "voiceId": "", "sleepMinutes": 0, "stopAtChapterEnd": False})


class DeviceSession(Owned, Base):
    __tablename__ = "device_sessions"
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    previous_hashes: Mapped[list] = mapped_column(JSONB, default=list)
    client_type: Mapped[str] = mapped_column(String(20))
    device_name: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Book(Owned, Base):
    __tablename__ = "books"
    title: Mapped[str] = mapped_column(String(200))
    source_hash: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(32))
    original_branch_id: Mapped[str] = mapped_column(String(32))
    original_revision_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(default="active")
    version: Mapped[int] = mapped_column(default=1)
    source_label: Mapped[str] = mapped_column(default="用户提供 · 权利来源未核验")
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (Index("book_owner_hash", "owner_id", "source_hash"),)


class SourceDocument(Owned, Base):
    __tablename__ = "sources"
    book_id: Mapped[str] = mapped_column(String(32), index=True)
    file_hash: Mapped[str] = mapped_column(String(64))
    file_ref: Mapped[str] = mapped_column(String(100))
    format: Mapped[str] = mapped_column(String(10))
    encoding: Mapped[str] = mapped_column(String(30))
    normalized_ref: Mapped[str] = mapped_column(String(100))
    parser_version: Mapped[str] = mapped_column(default="import-v1")
    source_map: Mapped[dict] = mapped_column(JSONB, default=dict)


class BookDeletion(Owned, Base):
    """Committed deletion plus finite private-file cleanup, never a model task."""
    __tablename__ = "book_deletions"
    book_id: Mapped[str] = mapped_column(String(32))
    book_version: Mapped[int] = mapped_column(Integer)
    retained: Mapped[list] = mapped_column(JSONB, default=list)
    cleanup_manifest: Mapped[dict] = mapped_column(JSONB, default=dict)
    cleanup_state: Mapped[str] = mapped_column(String(20), default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "book_id"),)


class Branch(Owned, Base):
    __tablename__ = "branches"
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    parent_branch_id: Mapped[str | None] = mapped_column(String(32))
    fork_anchor: Mapped[dict | None] = mapped_column(JSONB)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(default="active")
    head_revision_id: Mapped[str] = mapped_column(String(32))
    origin_operation_id: Mapped[str | None] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(default=1)


class Revision(Owned, Base):
    __tablename__ = "revisions"
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    parent_revision_id: Mapped[str | None] = mapped_column(String(32))
    sequence: Mapped[int] = mapped_column(default=1)
    manifest: Mapped[list] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSONB)


class ContentBlock(Owned, Base):
    __tablename__ = "content_blocks"
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    html: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(default="original")


class Progress(Owned, Base):
    __tablename__ = "progress"
    book_id: Mapped[str] = mapped_column(String(32))
    branch_id: Mapped[str] = mapped_column(String(32))
    revision_id: Mapped[str] = mapped_column(String(32))
    anchor: Mapped[dict] = mapped_column(JSONB)
    max_observed_anchor: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(default=1)
    device_sequences: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("owner_id", "branch_id", "revision_id"),)


class Bookmark(Owned, Base):
    __tablename__ = "bookmarks"
    book_id: Mapped[str] = mapped_column(String(32), index=True)
    branch_id: Mapped[str] = mapped_column(String(32))
    revision_id: Mapped[str] = mapped_column(String(32))
    anchor: Mapped[dict] = mapped_column(JSONB)
    note: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="valid")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ResumeTarget(Owned, Base):
    """Navigation only: never an observed boundary or an authoritative world head."""
    __tablename__ = "resume_targets"
    story_id: Mapped[str] = mapped_column(String(38))
    mode: Mapped[str] = mapped_column(String(10))
    context: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("owner_id", "story_id", "mode"),)


class PlaybackPosition(Owned, Base):
    """Completed public paragraph only; never audio, world knowledge or a head."""
    __tablename__ = "playback_positions"
    scope_key: Mapped[str] = mapped_column(String(64))
    story_id: Mapped[str] = mapped_column(String(38))
    kind: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(20))
    line_id: Mapped[str] = mapped_column(String(32))
    content_revision: Mapped[str] = mapped_column(String(80))
    context: Mapped[dict] = mapped_column(JSONB)
    paragraph_id: Mapped[str] = mapped_column(String(100))
    last_completed_anchor: Mapped[dict] = mapped_column(JSONB)
    max_heard_anchor: Mapped[dict] = mapped_column(JSONB)
    ended: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    book_id: Mapped[str | None] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    world_id: Mapped[str | None] = mapped_column(String(32), index=True)
    generation_id: Mapped[str | None] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"))
    candidate_version: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("owner_id", "scope_key"),)


class ReadingActivity(Owned, Base):
    __tablename__ = "reading_activity"
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[str] = mapped_column(String(32))
    revision_id: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(100))
    seconds: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("owner_id", "event_id"),)


class ModelProfile(Owned, Base):
    __tablename__ = "model_profiles"
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(300))
    model_id: Mapped[str] = mapped_column(String(100))
    key_cipher: Mapped[str] = mapped_column(Text)
    key_tail: Mapped[str] = mapped_column(String(4))
    context_window: Mapped[int] = mapped_column(Integer)
    output_parameter: Mapped[str] = mapped_column(default="max_tokens")
    supports_json: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="active")
    call_budget: Mapped[int] = mapped_column(default=100)
    input_budget: Mapped[int] = mapped_column(default=1000000)
    output_budget: Mapped[int] = mapped_column(default=100000)
    calls_reserved: Mapped[int] = mapped_column(default=0)
    input_reserved: Mapped[int] = mapped_column(default=0)
    output_reserved: Mapped[int] = mapped_column(default=0)


class OperationPlan(Owned, Base):
    __tablename__ = "operation_plans"
    profile_id: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[int] = mapped_column(Integer)
    plan_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    details: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Consent(Owned, Base):
    __tablename__ = "consents"
    plan_id: Mapped[str] = mapped_column(String(32), unique=True)
    profile_id: Mapped[str] = mapped_column(String(32))
    profile_version: Mapped[int] = mapped_column(Integer)
    plan_hash: Mapped[str] = mapped_column(String(64))
    max_calls: Mapped[int] = mapped_column(Integer)
    input_limit: Mapped[int] = mapped_column(Integer)
    output_limit: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[str | None] = mapped_column(String(32))
    calls_reserved: Mapped[int] = mapped_column(default=0)
    input_reserved: Mapped[int] = mapped_column(default=0)
    output_reserved: Mapped[int] = mapped_column(default=0)


class Task(Owned, Base):
    __tablename__ = "tasks"
    task_type: Mapped[str] = mapped_column(String(30), index=True)
    state: Mapped[str] = mapped_column(default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    checkpoint: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    provider_state: Mapped[str] = mapped_column(default="not_started")
    persistence_state: Mapped[str] = mapped_column(default="pending")
    result_outcome: Mapped[str | None] = mapped_column(String(30))
    usage_state: Mapped[str] = mapped_column(default="estimated")
    lease_owner: Mapped[str | None] = mapped_column(String(32))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_seq: Mapped[int] = mapped_column(default=0)


class TaskEvent(Identity, Base):
    __tablename__ = "task_events"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("task_id", "seq"),)


class Candidate(Owned, Base):
    __tablename__ = "candidates"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), unique=True)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    partial: Mapped[bool] = mapped_column(default=False)
    persistence_state: Mapped[str] = mapped_column(default="saved")
    validation_status: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(default="pending")
    accepted_revision_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    accept_result: Mapped[dict | None] = mapped_column(JSONB)


class GenerationBatch(Owned, Base):
    """Pinned prose request. Private model credentials never enter its DTO."""
    __tablename__ = "generation_batches"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), unique=True)
    book_id: Mapped[str] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    origin_anchor: Mapped[dict] = mapped_column(JSONB)
    request: Mapped[dict] = mapped_column(JSONB)
    profile_snapshot: Mapped[dict] = mapped_column(JSONB)
    candidate_version: Mapped[int] = mapped_column(Integer, default=1)
    accepted_prefix: Mapped[int] = mapped_column(Integer, default=0)
    target_branch_id: Mapped[str | None] = mapped_column(String(32))
    last_accepted_revision_id: Mapped[str | None] = mapped_column(String(32))
    version_times: Mapped[dict] = mapped_column(JSONB, default=dict)


class GenerationChapterVersion(Owned, Base):
    __tablename__ = "generation_chapter_versions"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer)
    chapter_index: Mapped[int] = mapped_column(Integer)
    content_id: Mapped[str] = mapped_column(String(32), default=uid)
    title: Mapped[str] = mapped_column(String(100), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64))
    stream_version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_version: Mapped[int] = mapped_column(Integer, default=0)
    completeness: Mapped[str] = mapped_column(String(20), default="partial")
    durability: Mapped[str] = mapped_column(String(20), default="unconfirmed")
    hints: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint("generation_id", "candidate_version", "chapter_index"),)


class GenerationAcceptance(Owned, Base):
    __tablename__ = "generation_acceptances"
    generation_id: Mapped[str] = mapped_column(ForeignKey("generation_batches.id", ondelete="CASCADE"), index=True)
    candidate_version: Mapped[int] = mapped_column(Integer)
    prefix_count: Mapped[int] = mapped_column(Integer)
    revision_id: Mapped[str] = mapped_column(String(32))
    result: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("generation_id", "candidate_version", "prefix_count"),)


class KnowledgeSnapshot(Owned, Base):
    __tablename__ = "knowledge_snapshots"
    book_id: Mapped[str] = mapped_column(String(32))
    branch_id: Mapped[str] = mapped_column(String(32))
    revision_id: Mapped[str] = mapped_column(String(32))
    boundary: Mapped[dict] = mapped_column(JSONB)
    scope_hash: Mapped[str] = mapped_column(String(64), index=True)
    analyzer_version: Mapped[str] = mapped_column(default="analysis-v17")
    profile_version: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSONB)
    completeness: Mapped[str] = mapped_column(default="partial")


class AnalysisChunk(Owned, Base):
    __tablename__ = "analysis_chunks"
    book_id: Mapped[str] = mapped_column(String(32))
    branch_id: Mapped[str] = mapped_column(String(32))
    cache_hash: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[dict] = mapped_column(JSONB)


class CharacterIdentity(Owned, Base):
    __tablename__ = "character_identities"
    origin_key: Mapped[str] = mapped_column(String(256))
    source_ref: Mapped[dict] = mapped_column(JSONB, default=dict)
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("owner_id", "origin_key"),)


class CharacterAlias(Owned, Base):
    __tablename__ = "character_aliases"
    identity_id: Mapped[str] = mapped_column(ForeignKey("character_identities.id", ondelete="CASCADE"), index=True)
    scope_kind: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(100))
    __table_args__ = (UniqueConstraint("owner_id", "scope_kind", "scope_id", "entity_id"),)


class CharacterScopeRevision(Owned, Base):
    __tablename__ = "character_scope_revisions"
    identity_id: Mapped[str] = mapped_column(ForeignKey("character_identities.id", ondelete="CASCADE"), index=True)
    scope_key: Mapped[str] = mapped_column(String(100))
    scope_kind: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[str] = mapped_column(String(32), index=True)
    context: Mapped[dict] = mapped_column(JSONB)
    visible_data: Mapped[dict] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("owner_id", "identity_id", "scope_key"),)


class RoleSession(Owned, Base):
    __tablename__ = "role_sessions"
    book_id: Mapped[str] = mapped_column(String(32))
    branch_id: Mapped[str] = mapped_column(String(32), index=True)
    pinned_revision_id: Mapped[str] = mapped_column(String(32))
    pinned_anchor: Mapped[dict] = mapped_column(JSONB)
    character_id: Mapped[str] = mapped_column(String(100))
    character_name: Mapped[str] = mapped_column(String(100))
    character_identity_id: Mapped[str | None] = mapped_column(ForeignKey("character_identities.id", ondelete="SET NULL"), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(32))
    identity_mode: Mapped[str] = mapped_column(default="reader_out_of_story")
    status: Mapped[str] = mapped_column(default="active")
    pending_task_id: Mapped[str | None] = mapped_column(String(32))
    follow_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    knowledge_mode: Mapped[str] = mapped_column(String(20), default="progress")
    card_id: Mapped[str | None] = mapped_column(String(100))


class RoleMessage(Owned, Base):
    __tablename__ = "role_messages"
    session_id: Mapped[str] = mapped_column(ForeignKey("role_sessions.id", ondelete="CASCADE"))
    turn_id: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    task_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(default="saved")
    __table_args__ = (UniqueConstraint("session_id", "turn_id", "role"), UniqueConstraint("session_id", "sequence"))


class UsageRecord(Owned, Base):
    __tablename__ = "usage"
    task_id: Mapped[str] = mapped_column(String(32), index=True)
    provider_call_id: Mapped[str] = mapped_column(String(32), unique=True)
    profile_id: Mapped[str] = mapped_column(String(32))
    consent_id: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(30))
    reserved_input: Mapped[int] = mapped_column(Integer)
    reserved_output: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_state: Mapped[str] = mapped_column(default="unknown")
    status: Mapped[str] = mapped_column(default="intent")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    billing_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class Idempotency(Owned, Base):
    __tablename__ = "idempotency"
    scope: Mapped[str] = mapped_column(String(160))
    key: Mapped[str] = mapped_column(String(160))
    payload_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "scope", "key"),)


class RateLimit(Base):
    __tablename__ = "rate_limits"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    count: Mapped[int] = mapped_column(default=0)
    window_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Owned, Base):
    __tablename__ = "outbox"
    event_type: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="pending")


class CleanupReceipt(Identity, Base):
    __tablename__ = "cleanup_receipts"
    task_id: Mapped[str] = mapped_column(String(32), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
