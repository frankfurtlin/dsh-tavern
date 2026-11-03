from sqlalchemy import String, Text, Integer, ForeignKey, UniqueConstraint, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base
from ..models import Owned, now


class PlayControl(Owned, Base):
    __tablename__='play_controls'
    __table_args__=(UniqueConstraint('owner_id','scope_kind','scope_id'),)
    scope_kind: Mapped[str]=mapped_column(String(20))
    scope_id: Mapped[str]=mapped_column(String(32))
    world_id: Mapped[str]=mapped_column(String(32),index=True)
    profile_id: Mapped[str|None]=mapped_column(ForeignKey('model_profiles.id',ondelete='SET NULL'))
    version: Mapped[int]=mapped_column(Integer,default=1)
    memory: Mapped[dict]=mapped_column(JSONB,default=dict)


class MemoryRevision(Owned, Base):
    __tablename__='play_memory_revisions'
    __table_args__=(UniqueConstraint('control_id','version'),)
    control_id: Mapped[str]=mapped_column(ForeignKey('play_controls.id',ondelete='CASCADE'),index=True)
    version: Mapped[int]=mapped_column(Integer)
    memory: Mapped[dict]=mapped_column(JSONB)


class WorldChatSession(Owned, Base):
    __tablename__='world_chat_sessions'
    world_id: Mapped[str]=mapped_column(String(32),index=True)
    line_id: Mapped[str]=mapped_column(String(32),index=True)
    checkpoint_id: Mapped[str]=mapped_column(String(32))
    character_id: Mapped[str]=mapped_column(String(80))
    character_name: Mapped[str]=mapped_column(String(100))
    name: Mapped[str]=mapped_column(String(120))
    version: Mapped[int]=mapped_column(Integer,default=1)
    snapshot: Mapped[dict]=mapped_column(JSONB)
    denied_phrases: Mapped[list]=mapped_column(JSONB,default=list)
    pending_task_id: Mapped[str|None]=mapped_column(String(32))
    status: Mapped[str]=mapped_column(String(20),default='active')
    updated_at: Mapped[object]=mapped_column(DateTime(timezone=True),default=now,index=True)


class WorldChatMessage(Owned, Base):
    __tablename__='world_chat_messages'
    __table_args__=(UniqueConstraint('session_id','turn_key','role'),UniqueConstraint('session_id','sequence'),)
    session_id: Mapped[str]=mapped_column(ForeignKey('world_chat_sessions.id',ondelete='CASCADE'),index=True)
    task_id: Mapped[str]=mapped_column(ForeignKey('tasks.id',ondelete='CASCADE'),index=True)
    turn_key: Mapped[str]=mapped_column(String(100))
    sequence: Mapped[int]=mapped_column(Integer)
    role: Mapped[str]=mapped_column(String(20))
    text: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String(20))
    checkpoint_id: Mapped[str]=mapped_column(String(32))
    model_name: Mapped[str]=mapped_column(String(150),default='')
