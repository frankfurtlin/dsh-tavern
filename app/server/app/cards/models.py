"""Character card storage. Cards live in the independent world schema so the
reader alembic revision does not change (``world.migrate`` creates the table)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..models import now
from ..world.models import WorldBase, WorldOwned


class CharacterCard(WorldOwned, WorldBase):
    __tablename__ = "character_cards"
    __table_args__ = {"schema": "world"}

    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    personality: Mapped[str] = mapped_column(Text, default="")
    scenario: Mapped[str] = mapped_column(Text, default="")
    first_message: Mapped[str] = mapped_column(Text, default="")
    example_messages: Mapped[list] = mapped_column(JSONB, default=list)
    alternate_greetings: Mapped[list] = mapped_column(JSONB, default=list)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    creator: Mapped[str] = mapped_column(String(200), default="")
    character_version: Mapped[str] = mapped_column(String(100), default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    post_history_instructions: Mapped[str] = mapped_column(Text, default="")

    source: Mapped[str] = mapped_column(String(8))
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    avatar_path: Mapped[str | None] = mapped_column(String(200))

    world_id: Mapped[str | None] = mapped_column(String(32), index=True)
    line_id: Mapped[str | None] = mapped_column(String(32))
    checkpoint_id: Mapped[str | None] = mapped_column(String(32))
    character_id: Mapped[str | None] = mapped_column(String(80))

    preset_id: Mapped[str | None] = mapped_column(String(32), index=True)
    companion: Mapped[bool] = mapped_column(Boolean, default=False)
    memory_task_id: Mapped[str | None] = mapped_column(String(32))

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CardLore(WorldOwned, WorldBase):
    """A lorebook entry attached to one character card."""

    __tablename__ = "card_lore"
    __table_args__ = {"schema": "world"}

    card_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    keys: Mapped[list] = mapped_column(JSONB, default=list)
    content: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=100)
    source: Mapped[str] = mapped_column(String(8), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ChatPreset(WorldOwned, WorldBase):
    """A user-level snippet appended to the chat system prompt."""

    __tablename__ = "chat_presets"
    __table_args__ = {"schema": "world"}

    name: Mapped[str] = mapped_column(String(80))
    system_suffix: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RegexScript(WorldOwned, WorldBase):
    """A user-level output substitution applied to saved chat replies."""

    __tablename__ = "regex_scripts"
    __table_args__ = {"schema": "world"}

    name: Mapped[str] = mapped_column(String(80))
    pattern: Mapped[str] = mapped_column(String(500))
    replacement: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CardMemory(WorldOwned, WorldBase):
    """One long-term memory item attached to a character card."""

    __tablename__ = "card_memories"
    __table_args__ = (Index("ix_world_card_memories_card_enabled", "card_id", "enabled"), {"schema": "world"})

    card_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="fact")
    title: Mapped[str] = mapped_column(String(120), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    salience: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(8), default="manual")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
