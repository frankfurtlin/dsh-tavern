from sqlalchemy import String, Text, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base
from ..models import Owned, now


class Feedback(Owned, Base):
    __tablename__ = 'product_feedback'
    subject: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(20), default='open')
    reply: Mapped[str] = mapped_column(Text, default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=now)


class Notice(Owned, Base):
    __tablename__ = 'product_notices'
    title: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(String(250), default='/help/feedback')
    read: Mapped[bool] = mapped_column(Boolean, default=False)


def notify(db, owner, title, text, path='/help/feedback'):
    db.add(Notice(owner_id=owner, title=title[:160], text=text[:2000], path=path))
