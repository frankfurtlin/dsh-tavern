"""Character-card storage helpers (native tavern keeps cards, not worlds)."""
from pathlib import Path

from sqlalchemy import select

from ..domain import owned
from ..errors import Problem
from ..models import now, uid
from ..storage import private_path, write_private
from . import space
from .models import CharacterCard


def import_card(db, owner, parsed, png=None):
    card = CharacterCard(id=uid(), owner_id=owner, name=parsed["name"],
        description=parsed["description"], personality=parsed["personality"],
        scenario=parsed["scenario"], first_message=parsed["first_message"],
        example_messages=parsed["example_messages"], alternate_greetings=parsed["alternate_greetings"],
        tags=parsed["tags"], creator=parsed["creator"], character_version=parsed["character_version"],
        system_prompt=parsed["system_prompt"],
        post_history_instructions=parsed["post_history_instructions"],
        source=parsed["source"], raw=parsed["raw"], updated_at=now())
    if png:
        # private_path only accepts files directly under the owner root, so the
        # avatar lives at the top level rather than in a cards/ subdirectory.
        card.avatar_path = write_private(owner, f"card-avatar-{card.id}.png", png)
    db.add(card)
    db.flush()
    space.import_lore(db, owner, card.id, parsed.get("lore"))
    db.commit()
    return card


def card_out(card):
    target = None
    if card.world_id and card.line_id and card.checkpoint_id and card.character_id:
        target = {"worldId": card.world_id, "lineId": card.line_id,
                  "checkpointId": card.checkpoint_id, "characterId": card.character_id}
    return {
        "id": card.id,
        "name": card.name,
        "description": card.description,
        "personality": card.personality,
        "scenario": card.scenario,
        "firstMessage": card.first_message,
        "alternateGreetings": list(card.alternate_greetings or []),
        "tags": list(card.tags or []),
        "creator": card.creator,
        "source": card.source,
        "worldId": card.world_id,
        "lineId": card.line_id,
        "checkpointId": card.checkpoint_id,
        "characterId": card.character_id,
        "hasAvatar": bool(card.avatar_path),
        "presetId": card.preset_id,
        "companion": bool(card.companion),
        "builtin": bool(card.builtin_slug),
        "builtinSlug": card.builtin_slug,
        "chatTarget": target,
        "createdAt": card.created_at.isoformat(),
        "updatedAt": card.updated_at.isoformat(),
    }


def update_card(db, owner, card, values):
    for field in ("name", "description", "personality", "scenario", "system_prompt",
                  "post_history_instructions"):
        if values.get(field) is not None:
            setattr(card, field, values[field])
    if values.get("firstMessage") is not None:
        card.first_message = values["firstMessage"]
    if values.get("tags") is not None:
        card.tags = list(values["tags"])[:200]
    if "alternateGreetings" in values:
        card.alternate_greetings = [item for item in (values["alternateGreetings"] or []) if item][:10]
    if "presetId" in values:
        space.bind_preset(db, owner, card, values["presetId"])
    if values.get("companion") is not None:
        card.companion = bool(values["companion"])
    card.updated_at = now()
    db.commit()
    return card


def list_cards(db, owner, q=""):
    from . import presets
    presets.ensure_preset_cards(db, owner)
    query = select(CharacterCard).where(CharacterCard.owner_id == owner, CharacterCard.deleted_at.is_(None))
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(CharacterCard.name.ilike(pattern))
    return list(db.scalars(query.order_by(CharacterCard.created_at.desc(), CharacterCard.id.desc())))


def get_card(db, owner, card_id):
    card = owned(db, CharacterCard, card_id, owner)
    if card.deleted_at is not None:
        raise Problem("RESOURCE_NOT_FOUND", "这张角色卡不存在或已删除", 404)
    return card


def avatar_file(card, owner) -> Path:
    if not card.avatar_path:
        raise Problem("RESOURCE_NOT_FOUND", "这张角色卡没有头像", 404)
    path = private_path(owner, card.avatar_path)
    if not path.is_file():
        raise Problem("RESOURCE_NOT_FOUND", "这张角色卡没有头像", 404)
    return path


def delete_card(db, card):
    card.deleted_at = now()
    card.updated_at = now()
    db.commit()
