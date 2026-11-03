"""Additive, independent world schema; never changes reader alembic_version."""
from sqlalchemy import text
from ..db import engine
from .models import WorldBase
from ..cards.models import CharacterCard  # noqa: F401  (registers world.character_cards)

SCHEMA_VERSION = 1


def migrate():
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(82370218)"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS world"))
        WorldBase.metadata.create_all(conn, tables=[t for t in WorldBase.metadata.tables.values() if t.schema == "world"])
        conn.execute(text("CREATE TABLE IF NOT EXISTS world.schema_version (version integer PRIMARY KEY)"))
        conn.execute(text("INSERT INTO world.schema_version(version) VALUES (:version) ON CONFLICT DO NOTHING"),
                     {"version": SCHEMA_VERSION})


def ready(db):
    return db.scalar(text("SELECT to_regclass('world.schema_version')")) is not None and db.scalar(text("SELECT max(version) FROM world.schema_version")) == SCHEMA_VERSION


if __name__ == "__main__":
    migrate()
