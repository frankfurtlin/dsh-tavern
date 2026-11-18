"""Additive, independent world schema; never changes reader alembic_version."""
from sqlalchemy import text
from ..db import engine
from .models import WorldBase
from ..cards.models import (  # noqa: F401  (registers the world.card_* tables)
    CardLore,
    CharacterCard,
    ChatPreset,
    RegexScript,
    TavernPersona,
)
from ..tavern.models import (  # noqa: F401  (registers the native tavern tables)
    TavernChat,
    TavernMessage,
    TavernPreset,
    TavernWorldInfo,
)

SCHEMA_VERSION = 1


def migrate():
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(82370218)"))
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS world"))
        WorldBase.metadata.create_all(conn, tables=[t for t in WorldBase.metadata.tables.values() if t.schema == "world"])
        # create_all only creates missing tables; add the B1 card column explicitly.
        conn.execute(text("ALTER TABLE world.character_cards ADD COLUMN IF NOT EXISTS preset_id varchar(32)"))
        # C companions add two more card columns for the same reason.
        conn.execute(text("ALTER TABLE world.character_cards ADD COLUMN IF NOT EXISTS companion boolean NOT NULL DEFAULT false"))
        conn.execute(text("ALTER TABLE world.character_cards ADD COLUMN IF NOT EXISTS memory_task_id varchar(32)"))
        # P1 personas: world_chat_sessions gains an optional persona reference.
        # P3: the legacy world-chat stack (and its table) has been removed; keep the
        # statement only when the old table still exists so the migration stays idempotent
        # both on fresh databases and on databases that still carry pre-P3 tables.
        if conn.scalar(text("SELECT to_regclass('world.world_chat_sessions')")) is not None:
            conn.execute(text("ALTER TABLE world_chat_sessions ADD COLUMN IF NOT EXISTS persona_id varchar(32)"))
        # Native tavern: per-card prompt settings live on the card row.
        conn.execute(text("ALTER TABLE world.character_cards ADD COLUMN IF NOT EXISTS prompt_settings jsonb NOT NULL DEFAULT '{}'::jsonb"))
        # P2 presets: chats may bind one preset; presets themselves are created by create_all.
        conn.execute(text("ALTER TABLE world.tavern_chats ADD COLUMN IF NOT EXISTS preset_id varchar(32)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_world_tavern_chats_preset_id ON world.tavern_chats (preset_id)"))
        conn.execute(text("ALTER TABLE world.tavern_chats ADD COLUMN IF NOT EXISTS sampling jsonb"))
        # P3 group chats: chats may have multiple members (card_id becomes null).
        conn.execute(text("ALTER TABLE world.tavern_chats ADD COLUMN IF NOT EXISTS mode varchar(8) NOT NULL DEFAULT 'single'"))
        conn.execute(text("ALTER TABLE world.tavern_chats ADD COLUMN IF NOT EXISTS members jsonb NOT NULL DEFAULT '[]'::jsonb"))
        conn.execute(text("ALTER TABLE world.tavern_chats ALTER COLUMN card_id DROP NOT NULL"))
        conn.execute(text("ALTER TABLE world.tavern_messages ADD COLUMN IF NOT EXISTS speaker_card_id varchar(32)"))
        # P0-B quote replies: the quoted snippet is copied onto the user message.
        conn.execute(text("ALTER TABLE world.tavern_messages ADD COLUMN IF NOT EXISTS quoted_text text"))
        # P4 preset cards: bundled cards are seeded per owner and marked by slug;
        # the partial unique index keeps the lazy seed idempotent (soft-deleted
        # rows still occupy their slug so a deleted preset card is not re-seeded).
        conn.execute(text("ALTER TABLE world.character_cards ADD COLUMN IF NOT EXISTS builtin_slug varchar(40)"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_character_cards_builtin_slug "
            "ON world.character_cards (owner_id, builtin_slug) WHERE builtin_slug IS NOT NULL"
        ))
        # P2 presets: one built-in 「默认」 per owner; duplicated rows from the
        # earlier lazy-create race are collapsed here (idempotent).
        conn.execute(text("ALTER TABLE world.tavern_presets ADD COLUMN IF NOT EXISTS builtin boolean NOT NULL DEFAULT false"))
        deduped = conn.execute(text("""
            WITH ranked AS (
                SELECT id, row_number() OVER (
                    PARTITION BY owner_id ORDER BY is_default DESC, created_at, id
                ) AS rn
                FROM world.tavern_presets WHERE is_default
            )
            UPDATE world.tavern_presets p SET is_default = false
            FROM ranked r WHERE p.id = r.id AND r.rn > 1
        """)).rowcount
        marked = conn.execute(text("""
            WITH ranked AS (
                SELECT id, row_number() OVER (
                    PARTITION BY owner_id
                    ORDER BY (is_default AND name = '默认') DESC, is_default DESC, created_at, id
                ) AS rn
                FROM world.tavern_presets WHERE builtin = false AND name = '默认'
            )
            UPDATE world.tavern_presets p SET builtin = true
            FROM ranked r WHERE p.id = r.id AND r.rn = 1
        """)).rowcount
        conn.execute(text("""
            UPDATE world.tavern_chats c SET preset_id = keep.id
            FROM world.tavern_presets p
            JOIN world.tavern_presets keep
              ON keep.owner_id = p.owner_id AND keep.builtin
            WHERE c.preset_id = p.id AND p.name = '默认' AND p.id <> keep.id
        """))
        removed = conn.execute(text("""
            DELETE FROM world.tavern_presets p
            USING world.tavern_presets keep
            WHERE p.name = '默认' AND keep.builtin
              AND p.owner_id = keep.owner_id AND p.id <> keep.id
        """)).rowcount
        conn.execute(text("""
            UPDATE world.tavern_presets b SET is_default = true
            WHERE b.builtin = true AND NOT EXISTS (
                SELECT 1 FROM world.tavern_presets d
                WHERE d.owner_id = b.owner_id AND d.is_default = true
            )
        """))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_world_tavern_presets_builtin "
                          "ON world.tavern_presets (owner_id) WHERE builtin"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_world_tavern_presets_default "
                          "ON world.tavern_presets (owner_id) WHERE is_default"))
        if deduped or marked or removed:
            print(f"tavern_presets: reset_default={deduped} marked_builtin={marked} removed_duplicates={removed}")
        # P2 world exports use source='world-export', wider than the original varchar(8).
        width = conn.execute(text("SELECT character_maximum_length FROM information_schema.columns "
                                 "WHERE table_schema='world' AND table_name='character_cards' AND column_name='source'")).scalar()
        if width is not None and width < 32:
            conn.execute(text("ALTER TABLE world.character_cards ALTER COLUMN source TYPE varchar(32)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS world.schema_version (version integer PRIMARY KEY)"))
        conn.execute(text("INSERT INTO world.schema_version(version) VALUES (:version) ON CONFLICT DO NOTHING"),
                     {"version": SCHEMA_VERSION})


def ready(db):
    return db.scalar(text("SELECT to_regclass('world.schema_version')")) is not None and db.scalar(text("SELECT max(version) FROM world.schema_version")) == SCHEMA_VERSION


if __name__ == "__main__":
    migrate()
