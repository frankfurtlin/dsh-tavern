"""Shared helpers for native tavern chats (serialization, lookup, import)."""
from datetime import datetime

from sqlalchemy import delete, func, select

from ..cards import parse as card_parse
from ..cards.models import CharacterCard
from ..errors import Problem
from ..models import now, uid
from ..storage import write_private
from .models import TavernChat, TavernMessage, TavernWorldInfo

POSITIONS = ("before_char", "after_char", "before_example", "at_depth")


def iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def card_or_404(db, owner, card_id):
    card = db.get(CharacterCard, card_id)
    if card is None or card.owner_id != owner or card.deleted_at is not None:
        raise Problem("RESOURCE_NOT_FOUND", "这张角色卡不存在或已删除", 404)
    return card


def chat_or_404(db, owner, chat_id, lock=False):
    statement = select(TavernChat).where(TavernChat.id == chat_id, TavernChat.owner_id == owner)
    if lock:
        statement = statement.with_for_update()
    chat = db.scalar(statement)
    if chat is None:
        raise Problem("RESOURCE_NOT_FOUND", "这段对话不存在或已删除", 404)
    return chat


def message_or_404(db, chat, message_id):
    row = db.get(TavernMessage, message_id)
    if row is None or row.chat_id != chat.id:
        raise Problem("RESOURCE_NOT_FOUND", "这条消息不存在或已删除", 404)
    return row


def swipe_content(swipes, index):
    if not isinstance(swipes, list) or not swipes:
        return ""
    item = swipes[index] if 0 <= index < len(swipes) else swipes[0]
    return (item or {}).get("content", "") if isinstance(item, dict) else str(item)


def message_out(row):
    swipes = list(row.swipes or [])
    active = int(row.active_swipe or 0)
    if swipes:
        active = max(0, min(active, len(swipes) - 1))
    return {
        "id": row.id,
        "role": row.role,
        "sequence": row.sequence,
        "swipes": swipes,
        "activeSwipe": active,
        "text": swipe_content(swipes, active),
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
    }


def chat_out(row):
    return {
        "id": row.id,
        "cardId": row.card_id,
        "title": row.title,
        "modelName": row.model_name,
        "lastMessageAt": iso(row.last_message_at),
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
    }


def card_summary(card):
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
        "hasAvatar": bool(card.avatar_path),
        "createdAt": iso(card.created_at),
        "updatedAt": iso(card.updated_at),
    }


def chat_stats(db, owner):
    """{cardId: {"chatCount": int, "lastChatAt": iso|None}} for the owner."""
    rows = db.execute(
        select(TavernChat.card_id, func.count(TavernChat.id), func.max(TavernChat.last_message_at))
        .where(TavernChat.owner_id == owner)
        .group_by(TavernChat.card_id)
    ).all()
    return {card_id: {"chatCount": count, "lastChatAt": iso(last)} for card_id, count, last in rows}


def list_messages(db, chat):
    return list(db.scalars(select(TavernMessage).where(TavernMessage.chat_id == chat.id)
                           .order_by(TavernMessage.sequence, TavernMessage.id)))


def next_sequence(db, chat):
    current = db.scalar(select(func.max(TavernMessage.sequence)).where(TavernMessage.chat_id == chat.id))
    return int(current or 0) + 1


def last_assistant(db, chat):
    return db.scalar(select(TavernMessage).where(TavernMessage.chat_id == chat.id,
                                                TavernMessage.role == "assistant")
                     .order_by(TavernMessage.sequence.desc(), TavernMessage.id.desc()))


def truncate_after(db, chat, sequence):
    db.execute(delete(TavernMessage).where(TavernMessage.chat_id == chat.id,
                                           TavernMessage.sequence > sequence))


def add_message(db, chat, owner, role, content, model=""):
    row = TavernMessage(id=uid(), owner_id=owner, chat_id=chat.id, role=role,
                        sequence=next_sequence(db, chat),
                        swipes=[{"content": content, "model": model, "createdAt": now().isoformat()}],
                        active_swipe=0, updated_at=now())
    db.add(row)
    db.flush()
    touch_chat(db, chat, model)
    return row


def touch_chat(db, chat, model=""):
    chat.last_message_at = now()
    chat.updated_at = now()
    if model:
        chat.model_name = model[:150]


def import_card(db, owner, raw, filename):
    """Parse an ST card and store it without the legacy world/line/checkpoint."""
    parsed = card_parse.parse_card(raw, filename)
    card = CharacterCard(
        id=uid(), owner_id=owner, name=parsed["name"],
        description=parsed["description"], personality=parsed["personality"],
        scenario=parsed["scenario"], first_message=parsed["first_message"],
        example_messages=parsed["example_messages"],
        alternate_greetings=parsed["alternate_greetings"],
        tags=parsed["tags"], creator=parsed["creator"],
        character_version=parsed["character_version"],
        system_prompt=parsed["system_prompt"],
        post_history_instructions=parsed["post_history_instructions"],
        source=parsed["source"], raw=parsed["raw"], prompt_settings={},
    )
    if raw.startswith(card_parse.PNG_MAGIC):
        card.avatar_path = write_private(owner, f"tavern-card-{card.id}.png", raw)
    db.add(card)
    for entry in parsed.get("lore") or []:
        db.add(TavernWorldInfo(id=uid(), owner_id=owner, card_id=card.id,
                               keys=list(entry.get("keys") or []),
                               content=str(entry.get("content") or ""),
                               position="before_char", order=int(entry.get("order") or 100),
                               enabled=bool(entry.get("enabled", True)), constant=False,
                               probability=100, source="import",
                               updated_at=now()))
    db.commit()
    return card


def world_info_rows(db, card):
    return list(db.scalars(select(TavernWorldInfo).where(TavernWorldInfo.card_id == card.id)
                           .order_by(TavernWorldInfo.order, TavernWorldInfo.created_at,
                                     TavernWorldInfo.id)))


def world_info_out(row):
    return {
        "id": row.id,
        "keys": list(row.keys or []),
        "content": row.content,
        "position": row.position,
        "depth": row.depth,
        "constant": bool(row.constant),
        "probability": row.probability,
        "order": row.order,
        "enabled": bool(row.enabled),
        "source": row.source,
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
    }
