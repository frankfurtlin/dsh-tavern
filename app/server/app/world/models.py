from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import User, Task, Identity, now


class WorldBase(DeclarativeBase):
    pass


# Reference-only copies resolve mixin foreign keys in this independent metadata.
# The world migrator explicitly excludes these reader tables from DDL.
User.__table__.to_metadata(WorldBase.metadata)
Task.__table__.to_metadata(WorldBase.metadata)


class WorldOwned(Identity):
    owner_id: Mapped[str] = mapped_column(ForeignKey(User.__table__.c.id, ondelete="CASCADE"), index=True)


class World(WorldOwned, WorldBase):
    __tablename__ = "instances"
    __table_args__ = {"schema": "world"}
    name: Mapped[str] = mapped_column(String(160))
    source_book_id: Mapped[str | None] = mapped_column(String(32), index=True)
    source_title: Mapped[str] = mapped_column(String(200))
    source: Mapped[dict] = mapped_column(JSONB)
    definition: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="preparing")
    version: Mapped[int] = mapped_column(Integer, default=1)
    active_line_id: Mapped[str | None] = mapped_column(String(32))
    prepare_task_id: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class WorldLine(WorldOwned, WorldBase):
    __tablename__ = "lines"
    __table_args__ = {"schema": "world"}
    world_id: Mapped[str] = mapped_column(ForeignKey("world.instances.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_line_id: Mapped[str | None] = mapped_column(String(32))
    fork_checkpoint_id: Mapped[str | None] = mapped_column(String(32))
    head_checkpoint_id: Mapped[str] = mapped_column(String(32))
    pending_task_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    reading_position: Mapped[dict] = mapped_column(JSONB, default=dict)
    camera: Mapped[dict] = mapped_column(JSONB, default=dict)


class WorldCheckpoint(WorldOwned, WorldBase):
    __tablename__ = "checkpoints"
    __table_args__ = {"schema": "world"}
    world_id: Mapped[str] = mapped_column(ForeignKey("world.instances.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("world.lines.id", ondelete="CASCADE"), index=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(32), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text, default="")
    action: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[dict] = mapped_column(JSONB)
    definition: Mapped[dict] = mapped_column(JSONB)
    options: Mapped[list] = mapped_column(JSONB, default=list)
    changes: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    memory: Mapped[dict] = mapped_column(JSONB, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20), default="turn")


class WorldTurn(WorldOwned, WorldBase):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("owner_id", "line_id", "turn_key"), {"schema": "world"})
    world_id: Mapped[str] = mapped_column(ForeignKey("world.instances.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[str] = mapped_column(ForeignKey("world.lines.id", ondelete="CASCADE"), index=True)
    parent_checkpoint_id: Mapped[str] = mapped_column(String(32))
    turn_key: Mapped[str] = mapped_column(String(100))
    task_id: Mapped[str] = mapped_column(ForeignKey(Task.__table__.c.id, ondelete="CASCADE"), unique=True)
    action: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20), default="action")
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    draft_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    checkpoint_id: Mapped[str | None] = mapped_column(String(32))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)


class WorldEvent(WorldOwned, WorldBase):
    __tablename__ = "events"
    __table_args__ = {"schema": "world"}
    world_id: Mapped[str] = mapped_column(ForeignKey("world.instances.id", ondelete="CASCADE"), index=True)
    checkpoint_id: Mapped[str] = mapped_column(ForeignKey("world.checkpoints.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(80))
    type: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSONB)
    visible_to: Mapped[list] = mapped_column(JSONB, default=list)
    minute: Mapped[int] = mapped_column(Integer)


class WorldMemory(WorldOwned, WorldBase):
    __tablename__ = "memories"
    __table_args__ = {"schema": "world"}
    world_id: Mapped[str] = mapped_column(ForeignKey("world.instances.id", ondelete="CASCADE"), index=True)
    line_id: Mapped[str] = mapped_column(String(32), index=True)
    checkpoint_id: Mapped[str] = mapped_column(String(32))
    first_checkpoint_id: Mapped[str] = mapped_column(String(32))
    last_checkpoint_id: Mapped[str] = mapped_column(String(32))
    level: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    event_ids: Mapped[list] = mapped_column(JSONB, default=list)


class WorldPublication(WorldOwned, WorldBase):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("owner_id", "checkpoint_id"), {"schema": "world"})
    world_id: Mapped[str] = mapped_column(ForeignKey("world.instances.id", ondelete="CASCADE"))
    checkpoint_id: Mapped[str] = mapped_column(String(32))
    book_id: Mapped[str] = mapped_column(String(32))
    branch_id: Mapped[str] = mapped_column(String(32))
    revision_id: Mapped[str] = mapped_column(String(32))
    anchor: Mapped[dict] = mapped_column(JSONB)
