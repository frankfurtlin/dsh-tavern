"""Import character cards into independent worlds and open card chats."""
from pathlib import Path

from sqlalchemy import select

from ..domain import owned
from ..errors import Problem
from ..models import now, uid
from ..play_controls import chat
from ..play_controls.models import WorldChatSession
from ..storage import private_path, write_private
from ..world.models import World
from ..world.schemas import WorldRules
from ..world.service import require_line, start_line
from ..world.state import initial_state, prepared_definition
from . import space
from .models import CharacterCard

CARD_SOURCE = {"schemaVersion": 2, "kind": "character-card", "templateId": None, "blocks": []}


def _clip(value, limit):
    return (value or "").strip()[:limit]


def _definition(card):
    character_id = "card-" + card.id
    place_id = "place"
    name = _clip(card.name, 100)
    description = _clip(card.scenario or card.description or card.personality, 1000)
    raw = {
        "title": _clip(card.name, 160),
        "description": _clip(card.scenario or card.description, 1200),
        "timeLabel": "现在",
        "location": place_id,
        "entities": [
            {"id": place_id, "name": _clip(f"{name}所在的地方", 100), "type": "place",
             "description": _clip(card.scenario, 1000) or "故事开始的地方", "traits": "", "source": "user"},
            {"id": character_id, "name": name, "type": "character", "description": description,
             "traits": _clip(card.personality, 500), "source": "user"},
        ],
        "initial": {character_id: {"alive": True, "location": place_id, "inventory": {}, "fields": {},
                                   "relations": {}, "goal": "", "known": []}},
        "lore": [],
        "unresolved": [],
        "narrativeStyle": _clip(card.personality, 400),
    }
    definition = prepared_definition(raw, [], WorldRules().model_dump())
    definition, state = initial_state(definition, None, "旅人", _clip(f"与{name}相遇的人", 800))
    return definition, state, character_id


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
    definition, state, character_id = _definition(card)
    world = World(id=uid(), owner_id=owner, name=_clip(card.name, 160), source_title=_clip(card.name, 200),
                  source=dict(CARD_SOURCE), definition=definition, status="active")
    db.add(world)
    db.flush()
    line, root = start_line(db, owner, world, definition, state, line_name="卡片对话",
                            summary=f"与{_clip(card.name, 120)}相遇的起点")
    card.world_id, card.line_id = world.id, line.id
    card.checkpoint_id, card.character_id = root.id, character_id
    db.add(card)
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
        "tags": list(card.tags or []),
        "creator": card.creator,
        "source": card.source,
        "hasAvatar": bool(card.avatar_path),
        "presetId": card.preset_id,
        "companion": bool(card.companion),
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
    if "presetId" in values:
        space.bind_preset(db, owner, card, values["presetId"])
    if values.get("companion") is not None:
        card.companion = bool(values["companion"])
    card.updated_at = now()
    db.commit()
    return card


def list_cards(db, owner, q=""):
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


def open_chat(db, owner, card):
    # Serialize concurrent "open chat" calls for the same card so they reuse a
    # single active session instead of racing two inserts.
    db.execute(select(CharacterCard.id).where(CharacterCard.id == card.id).with_for_update())
    existing = db.scalar(select(WorldChatSession).where(
        WorldChatSession.owner_id == owner, WorldChatSession.world_id == card.world_id,
        WorldChatSession.character_id == card.character_id, WorldChatSession.status == "active",
    ).order_by(WorldChatSession.updated_at.desc(), WorldChatSession.id.desc()).limit(1))
    if existing is not None:
        return existing
    if not (card.world_id and card.line_id and card.checkpoint_id and card.character_id):
        raise Problem("CARD_INVALID", "这张角色卡还没有可用的对话世界", 409)
    world, line = require_line(db, owner, card.world_id, card.line_id, lock=True)
    if world.status != "active":
        raise Problem("WORLD_ARCHIVED", "请先恢复世界，再开始新的对话", 409)
    value, denied = chat.snapshot(db, owner, world, line, card.checkpoint_id, card.character_id)
    session = WorldChatSession(id=uid(), owner_id=owner, world_id=world.id, line_id=line.id,
        checkpoint_id=card.checkpoint_id, character_id=card.character_id,
        character_name=value["character"]["name"], name=value["character"]["name"] + " · 单独聊聊",
        snapshot=value, denied_phrases=denied, version=1)
    db.add(session)
    db.flush()
    return session
