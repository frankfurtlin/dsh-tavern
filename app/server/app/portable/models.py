"""Private portable-operation receipts; registered by an additive migration."""
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models import Identity, User


class PortableBase(DeclarativeBase):
    pass


User.__table__.to_metadata(PortableBase.metadata)


class PortableOperation(Identity, PortableBase):
    __tablename__ = "portable_operations"
    owner_id: Mapped[str] = mapped_column(ForeignKey(User.__table__.c.id, ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    request_key: Mapped[str] = mapped_column(String(160))
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="preparing")
    version: Mapped[int] = mapped_column(Integer, default=1)
    stage_name: Mapped[str | None] = mapped_column(String(100))
    manifest_hash: Mapped[str | None] = mapped_column(String(64))
    counts: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (UniqueConstraint("owner_id", "kind", "request_key"),)


PORTABLE_TABLES = [PortableOperation.__table__]
