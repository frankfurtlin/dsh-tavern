"""Bundled preset character cards.

The app ships a handful of CC BY 4.0 cards (see ``presets/MANIFEST.json``) so a
fresh account has something to open immediately. They are seeded lazily on the
first card-library read instead of during registration, which also means the
assets stay optional for deployments that strip them.
"""
import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select, text

from .models import CharacterCard

logger = logging.getLogger("reader")
PRESET_DIR = Path(__file__).with_name("presets")
MANIFEST = PRESET_DIR / "MANIFEST.json"


def _enabled() -> bool:
    """Kill switch: PRESET_CARDS_ENABLED=0/off keeps a deployment asset-free."""
    value = os.environ.get("PRESET_CARDS_ENABLED", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


@lru_cache(maxsize=1)
def _manifest() -> tuple[dict, ...]:
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("preset_cards_manifest_unavailable path=%s", MANIFEST)
        return ()
    cards = payload.get("cards")
    if not isinstance(cards, list):
        logger.warning("preset_cards_manifest_invalid path=%s", MANIFEST)
        return ()
    entries = []
    for entry in cards:
        slug = entry.get("slug") if isinstance(entry, dict) else None
        json_name = entry.get("json") if isinstance(entry, dict) else None
        avatar_name = entry.get("avatar") if isinstance(entry, dict) else None
        if not isinstance(slug, str) or not slug or not isinstance(json_name, str):
            logger.warning("preset_cards_manifest_entry_invalid entry=%s", entry)
            continue
        entries.append({"slug": slug, "json": json_name,
                        "avatar": avatar_name if isinstance(avatar_name, str) else None})
    return tuple(entries)


def _lock_id(owner: str) -> int:
    digest = hashlib.sha1(f"preset-cards:{owner}".encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def ensure_preset_cards(db, owner: str) -> list[CharacterCard]:
    """Seed any missing bundled cards for ``owner``; return the ones created.

    Existing rows are matched by ``builtin_slug`` *including soft-deleted ones*,
    so a card the user deleted stays deleted. A session-scoped advisory lock
    serialises concurrent first reads; the partial unique index is the final
    guard if the lock cannot be taken (e.g. a rolled-back transaction).
    """
    if not _enabled():
        return []
    entries = _manifest()
    if not entries:
        return []
    # Imported lazily to avoid a service <-> presets import cycle.
    from . import parse, service

    created: list[CharacterCard] = []
    lock_id = _lock_id(owner)
    try:
        db.execute(text("SELECT pg_advisory_lock(:id)"), {"id": lock_id})
    except Exception as error:  # noqa: BLE001 - seeding must never break reads
        db.rollback()
        logger.warning("preset_cards_lock_failed owner=%s error=%s",
                       owner, type(error).__name__)
        return []
    try:
        for entry in entries:
            slug = entry["slug"]
            try:
                existing = db.scalar(select(CharacterCard.id).where(
                    CharacterCard.owner_id == owner,
                    CharacterCard.builtin_slug == slug,
                ))
                if existing is not None:
                    continue
                data = (PRESET_DIR / entry["json"]).read_bytes()
                parsed = parse.parse_card(data, entry["json"])
                png = None
                if entry["avatar"]:
                    png = (PRESET_DIR / entry["avatar"]).read_bytes()
                card = service.import_card(db, owner, parsed, png)
                card.builtin_slug = slug
                db.commit()
                created.append(card)
            except Exception as error:  # noqa: BLE001 - seeding must never break reads
                db.rollback()
                logger.warning("preset_card_seed_failed owner=%s slug=%s error=%s",
                               owner, slug, type(error).__name__)
    finally:
        try:
            db.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
            db.commit()
        except Exception as error:  # noqa: BLE001 - never mask the read path
            db.rollback()
            logger.warning("preset_cards_unlock_failed owner=%s error=%s",
                           owner, type(error).__name__)
    return created
