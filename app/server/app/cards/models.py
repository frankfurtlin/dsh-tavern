"""Character card storage. Cards live in the independent world schema so the
reader alembic revision does not change (``world.migrate`` creates the table)."""
from datetime import datetime

from sqlalchemy import DateTime, String, Text
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

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
