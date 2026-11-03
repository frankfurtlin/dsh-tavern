from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from .config import settings

# Stable API timestamps on Windows and Linux; business-day reports set their
# own explicit Asia/Shanghai boundary rather than inheriting host timezone.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=12, max_overflow=8,
                       connect_args={"options": "-c timezone=UTC"})
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as db:
        yield db
