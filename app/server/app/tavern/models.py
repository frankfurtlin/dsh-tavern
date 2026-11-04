"""Native tavern storage: chats, swipe messages and ST-style world info.

These tables live in the independent ``world`` schema so no reader alembic
revision is needed; ``app.world.migrate`` creates them via ``create_all``.
"""
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, Index, Integer, String, Text,
                        UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..models import now
from ..world.models import WorldBase, WorldOwned


class TavernChat(WorldOwned, WorldBase):
    __tablename__ = "tavern_chats"
    __table_args__ = {"schema": "world"}

    card_id: Mapped[str] = mapped_column(String(32), index=True)
    preset_id: Mapped[str | None] = mapped_column(String(32), index=True)
    sampling: Mapped[dict | None] = mapped_column(JSONB)
    title: Mapped[str] = mapped_column(String(160), default="")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_name: Mapped[str] = mapped_column(String(150), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TavernMessage(WorldOwned, WorldBase):
    __tablename__ = "tavern_messages"
    __table_args__ = (
        UniqueConstraint("chat_id", "sequence", name="uq_tavern_messages_chat_sequence"),
        {"schema": "world"},
    )

    chat_id: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(12))
    sequence: Mapped[int] = mapped_column(Integer)
    swipes: Mapped[list] = mapped_column(JSONB, default=list)
    active_swipe: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TavernWorldInfo(WorldOwned, WorldBase):
    __tablename__ = "tavern_world_info"
    __table_args__ = {"schema": "world"}

    card_id: Mapped[str] = mapped_column(String(32), index=True)
    keys: Mapped[list] = mapped_column(JSONB, default=list)
    content: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[str] = mapped_column(String(20), default="before_char")
    depth: Mapped[int] = mapped_column(Integer, default=4)
    constant: Mapped[bool] = mapped_column(Boolean, default=False)
    probability: Mapped[int] = mapped_column(Integer, default=100)
    order: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(12), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TavernPreset(WorldOwned, WorldBase):
    """User-level SillyTavern-style prompt preset (one built-in default)."""

    __tablename__ = "tavern_presets"
    __table_args__ = (
        Index("uq_world_tavern_presets_builtin", "owner_id", unique=True,
              postgresql_where=text("builtin")),
        Index("uq_world_tavern_presets_default", "owner_id", unique=True,
              postgresql_where=text("is_default")),
        {"schema": "world"},
    )

    name: Mapped[str] = mapped_column(String(80), default="")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    system: Mapped[str] = mapped_column(Text, default="")
    main: Mapped[str] = mapped_column(Text, default="")
    jailbreak: Mapped[str] = mapped_column(Text, default="")
    post_history: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float | None] = mapped_column(Float)
    top_p: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    stop: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
