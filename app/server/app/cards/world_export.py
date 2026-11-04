"""Export a character defined by an interactive world as a standalone card.

The card keeps pointing at the source world line and checkpoint, so the usual
``chatTarget`` path can open a conversation with that character immediately.
"""
from sqlalchemy import select

from ..errors import Problem
from ..models import now, uid
from ..world.models import WorldLine
from ..world.state import ancestry, public_view
from .models import CharacterCard

SOURCE = "world-export"


def _entity(definition, character_id):
    for entity in (definition or {}).get("entities", []):
        if entity.get("id") == character_id and entity.get("type") == "character":
            return entity
    raise Problem("CARD_CHARACTER_UNAVAILABLE", "这个世界里没有该人物", 404)


def _traits(entity):
    value = entity.get("traits")
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())[:2000]
    return str(value or "").strip()[:2000]


def _active_line(db, owner, world):
    if world.active_line_id:
        line = db.scalar(select(WorldLine).where(WorldLine.id == world.active_line_id,
            WorldLine.owner_id == owner, WorldLine.status == "active"))
        if line is not None:
            return line
    return db.scalar(select(WorldLine).where(WorldLine.owner_id == owner,
        WorldLine.world_id == world.id, WorldLine.status == "active").order_by(WorldLine.created_at.asc()).limit(1))


def _checkpoint_for(db, owner, world, line, character_id):
    """First checkpoint on the active line where the character is visible."""
    from ..world.service import checked_checkpoint
    head = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
    for checkpoint in reversed(list(ancestry(db, head, owner))):
        view = public_view(checkpoint.state, checkpoint.definition)
        if any(entity.get("id") == character_id for entity in view.get("entities", [])):
            return checkpoint
    return head


def export_world_character(db, owner, world, character_id):
    entity = _entity(world.definition, character_id)
    line = _active_line(db, owner, world)
    if line is None:
        raise Problem("CARD_SOURCE_UNAVAILABLE", "这个世界还没有故事线，暂时不能导出角色卡", 409)
    checkpoint = _checkpoint_for(db, owner, world, line, character_id)
    existing = db.scalar(select(CharacterCard).where(
        CharacterCard.owner_id == owner, CharacterCard.world_id == world.id,
        CharacterCard.character_id == character_id, CharacterCard.source == SOURCE,
        CharacterCard.deleted_at.is_(None),
    ).order_by(CharacterCard.updated_at.desc()).limit(1))
    if existing is not None:
        existing.line_id, existing.checkpoint_id = line.id, checkpoint.id
        existing.updated_at = now()
        db.commit()
        return existing
    name = str(entity.get("name") or "未命名角色").strip()[:160]
    card = CharacterCard(
        id=uid(), owner_id=owner, name=name,
        description=str(entity.get("description") or "")[:20000],
        personality=_traits(entity),
        scenario=f"来自互动世界《{world.name}》",
        first_message="", example_messages=[], alternate_greetings=[], tags=["世界导出"],
        creator="", character_version="", system_prompt="", post_history_instructions="",
        source=SOURCE,
        raw={"worldId": world.id, "worldName": world.name, "lineId": line.id,
             "checkpointId": checkpoint.id, "characterId": character_id,
             "entity": {key: entity.get(key) for key in ("id", "name", "description", "traits")}},
        world_id=world.id, line_id=line.id, checkpoint_id=checkpoint.id,
        character_id=character_id, updated_at=now())
    db.add(card)
    db.commit()
    return card
