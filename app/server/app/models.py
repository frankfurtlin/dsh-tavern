"""Reader-domain ORM models kept by the native tavern build.

Only accounts, sessions, model profiles, idempotency and the small account
maintenance records remain. Story/reading tables stay in the database but are
no longer mapped by this application.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, UniqueConstraint
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


class CleanupReceipt(Identity, Base):
    __tablename__ = "cleanup_receipts"
    task_id: Mapped[str] = mapped_column(String(32), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="queued")
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
