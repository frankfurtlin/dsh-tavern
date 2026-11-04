"""Additive, independent world schema; never changes reader alembic_version."""
from sqlalchemy import text
from ..db import engine
from .models import WorldBase
from ..cards.models import CharacterCard, CardLore, ChatPreset, RegexScript  # noqa: F401  (registers world.character_cards and card space tables)

SCHEMA_VERSION = 1


def migrate():
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(82370218)"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS world"))
        WorldBase.metadata.create_all(conn, tables=[t for t in WorldBase.metadata.tables.values() if t.schema == "world"])
        # create_all only creates missing tables; add the B1 card column explicitly.
        conn.execute(text("ALTER TABLE world.character_cards ADD COLUMN IF NOT EXISTS preset_id varchar(32)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS world.schema_version (version integer PRIMARY KEY)"))
        conn.execute(text("INSERT INTO world.schema_version(version) VALUES (:version) ON CONFLICT DO NOTHING"),
                     {"version": SCHEMA_VERSION})


def ready(db):
    return db.scalar(text("SELECT to_regclass('world.schema_version')")) is not None and db.scalar(text("SELECT max(version) FROM world.schema_version")) == SCHEMA_VERSION


if __name__ == "__main__":
    migrate()
