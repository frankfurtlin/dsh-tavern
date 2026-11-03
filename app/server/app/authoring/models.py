from sqlalchemy import String, Text, Integer, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base
from ..models import Owned, now


class AuthorDraft(Base):
    __tablename__ = 'product_author_drafts'
    world_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Publication(Owned, Base):
    __tablename__ = 'product_publications'
    world_id: Mapped[str] = mapped_column(String(32), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(20), default='pending')
    live: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    submitted: Mapped[dict] = mapped_column(JSONB)
    approved: Mapped[dict] = mapped_column(JSONB, default=dict)
    note: Mapped[str] = mapped_column(Text, default='')
    reviewer_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class PublicFavorite(Owned, Base):
    __tablename__ = 'product_public_favorites'
    publication_id: Mapped[str] = mapped_column(ForeignKey('product_publications.id', ondelete='CASCADE'), index=True)
    __table_args__ = (UniqueConstraint('owner_id', 'publication_id'),)


class PublicReport(Owned, Base):
    __tablename__ = 'product_public_reports'
    publication_id: Mapped[str] = mapped_column(ForeignKey('product_publications.id', ondelete='CASCADE'), index=True)
    reason: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), default='open')
    __table_args__ = (UniqueConstraint('owner_id', 'publication_id'),)
