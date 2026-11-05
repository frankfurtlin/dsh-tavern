from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import User, Identity


class WorldBase(DeclarativeBase):
    pass


# Reference-only copies resolve mixin foreign keys in this independent metadata.
# The world migrator explicitly excludes these reader tables from DDL.
User.__table__.to_metadata(WorldBase.metadata)


class WorldOwned(Identity):
    owner_id: Mapped[str] = mapped_column(ForeignKey(User.__table__.c.id, ondelete="CASCADE"), index=True)
