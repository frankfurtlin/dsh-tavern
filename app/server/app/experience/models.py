from sqlalchemy import String, Text, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from ..db import Base
from ..models import Owned, now


class Notice(Owned, Base):
    __tablename__ = 'product_notices'
    title: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(String(250), default='/explore')
    read: Mapped[bool] = mapped_column(Boolean, default=False)


def notify(db, owner, title, text, path='/explore'):
    db.add(Notice(owner_id=owner, title=title[:160], text=text[:2000], path=path))
