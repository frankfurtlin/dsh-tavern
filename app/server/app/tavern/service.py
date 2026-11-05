"""Shared helpers for native tavern chats (serialization, lookup, import)."""
from datetime import datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError

from ..cards import parse as card_parse
from ..cards.models import CharacterCard
from ..errors import Problem
from ..models import now, uid
from ..storage import write_private
from .models import TavernChat, TavernMessage, TavernPreset, TavernWorldInfo

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


def message_out(row, names=None):
    swipes = list(row.swipes or [])
    active = int(row.active_swipe or 0)
    if swipes:
        active = max(0, min(active, len(swipes) - 1))
    payload = {
        "id": row.id,
        "role": row.role,
        "sequence": row.sequence,
        "swipes": swipes,
        "activeSwipe": active,
        "text": swipe_content(swipes, active),
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
    }
    speaker = getattr(row, "speaker_card_id", None)
    if row.role == "assistant" and speaker:
        payload["speakerCardId"] = speaker
        payload["speakerName"] = (names or {}).get(speaker, "")
    return payload


def chat_out(row):
    return {
        "id": row.id,
        "cardId": row.card_id,
        "mode": getattr(row, "mode", "single") or "single",
        "members": list(getattr(row, "members", None) or []),
        "title": row.title,
        "presetId": row.preset_id,
        "sampling": dict(row.sampling or {}),
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
        .where(TavernChat.owner_id == owner, TavernChat.card_id.is_not(None))
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


def add_message(db, chat, owner, role, content, model="", speaker_card_id=None):
    row = TavernMessage(id=uid(), owner_id=owner, chat_id=chat.id, role=role,
                        speaker_card_id=speaker_card_id,
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


DEFAULT_PRESET_NAME = "默认"
DEFAULT_TEMPERATURE = 0.8
DEFAULT_TOP_P = 0.95


def preset_out(row):
    return {
        "id": row.id,
        "name": row.name,
        "isDefault": bool(row.is_default),
        "system": row.system or "",
        "main": row.main or "",
        "jailbreak": row.jailbreak or "",
        "postHistory": row.post_history or "",
        "temperature": row.temperature,
        "topP": row.top_p,
        "maxTokens": row.max_tokens,
        "stop": list(row.stop or []),
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
    }


def _builtin_preset(db, owner):
    return db.scalar(select(TavernPreset).where(TavernPreset.owner_id == owner,
                                                TavernPreset.builtin.is_(True)))


def default_preset(db, owner):
    """Return the active default preset, creating the built-in one exactly once.

    Creation is protected by a transaction-scoped advisory lock *and* by the
    partial unique index ``(owner_id) WHERE builtin`` so concurrent requests
    (or multiple uvicorn workers) can never create duplicate 「默认」 rows.
    """
    row = db.scalar(select(TavernPreset).where(TavernPreset.owner_id == owner,
                                               TavernPreset.is_default.is_(True)))
    if row is not None:
        return row
    row = _builtin_preset(db, owner)
    if row is not None:
        return row
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
               {"key": f"tavern-preset:{owner}"})
    row = _builtin_preset(db, owner)
    if row is not None:
        db.commit()  # release the advisory lock
        return row
    row = TavernPreset(id=uid(), owner_id=owner, name=DEFAULT_PRESET_NAME, builtin=True,
                       is_default=True, system="", main="", jailbreak="", post_history="",
                       temperature=DEFAULT_TEMPERATURE, top_p=DEFAULT_TOP_P,
                       max_tokens=None, stop=[], updated_at=now())
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Another session won the race: roll back and reuse its row.
        db.rollback()
        row = _builtin_preset(db, owner)
        if row is None:
            raise
    return row


def list_presets(db, owner):
    default_preset(db, owner)
    return list(db.scalars(select(TavernPreset).where(TavernPreset.owner_id == owner)
                           .order_by(TavernPreset.is_default.desc(),
                                     TavernPreset.created_at, TavernPreset.id)))


def get_preset(db, owner, preset_id):
    row = db.get(TavernPreset, preset_id)
    if row is None or row.owner_id != owner:
        raise Problem("PRESET_NOT_FOUND", "这个预设不存在", 404)
    return row


def preset_for_chat(db, owner, chat):
    if chat is not None and chat.preset_id:
        row = db.get(TavernPreset, chat.preset_id)
        if row is not None and row.owner_id == owner:
            return row
    return default_preset(db, owner)


def delete_preset(db, owner, preset_id):
    row = get_preset(db, owner, preset_id)
    if row.is_default or row.builtin:
        raise Problem("PRESET_IMMUTABLE", "内置默认预设不能删除", 409)
    db.execute(update(TavernChat).where(TavernChat.owner_id == owner,
                                        TavernChat.preset_id == row.id)
               .values(preset_id=None))
    db.delete(row)
    db.commit()

# ------------------------------------------------------------- group chats
MAX_GROUP_MEMBERS = 5
MIN_GROUP_MEMBERS = 2


def assistant_count(db, chat):
    return int(db.scalar(select(func.count()).select_from(TavernMessage).where(
        TavernMessage.chat_id == chat.id, TavernMessage.role == "assistant")) or 0)


def speaker_name_map(chat):
    return {str(item.get("cardId")): str(item.get("name") or "")
            for item in (getattr(chat, "members", None) or []) if isinstance(item, dict)}


def chat_cards(db, owner, chat):
    if (getattr(chat, "mode", "single") or "single") != "group":
        return [card_or_404(db, owner, chat.card_id)]
    cards = []
    for member in (getattr(chat, "members", None) or []):
        card_id = member.get("cardId") if isinstance(member, dict) else None
        if not card_id:
            continue
        card = db.get(CharacterCard, card_id)
        if card is None or card.owner_id != owner or card.deleted_at is not None:
            raise Problem("RESOURCE_NOT_FOUND", "群聊中有角色卡已被删除", 404)
        cards.append(card)
    if not cards:
        raise Problem("RESOURCE_NOT_FOUND", "这段群聊没有可用成员", 404)
    return cards


def chat_card(db, owner, chat):
    return chat_cards(db, owner, chat)[0]


def member_entry(card, order):
    return {"cardId": card.id, "name": card.name,
            "avatarPath": card.avatar_path or None, "order": order}


def group_speaker(db, chat):
    """Next speaker: members[assistant_count % member_count]."""
    members = [item for item in (getattr(chat, "members", None) or []) if isinstance(item, dict)]
    if not members:
        return None
    index = assistant_count(db, chat) % len(members)
    return members[index]


def chat_history(db, chat, before=None):
    """Prompt history rows with resolved assistant speaker names."""
    names = speaker_name_map(chat)
    rows = []
    for row in list_messages(db, chat):
        if before is not None and row.sequence >= before:
            continue
        item = {"role": row.role, "text": swipe_content(row.swipes, row.active_swipe)}
        speaker = getattr(row, "speaker_card_id", None)
        if row.role == "assistant" and speaker:
            item["speaker"] = names.get(speaker, "")
        rows.append(item)
    return rows


def group_world_info(db, cards):
    rows = []
    for card in cards:
        rows.extend(world_info_rows(db, card))
    return rows


def chats_out(db, rows):
    items = []
    for row in rows:
        item = chat_out(row)
        messages = list_messages(db, row)
        last = messages[-1] if messages else None
        names = speaker_name_map(row)
        item["lastMessage"] = (message_out(last, names) if last is not None else None)
        item["lastMessageAt"] = iso(row.last_message_at)
        item["messageCount"] = len(messages)
        items.append(item)
    return items


def list_chats(db, owner, limit=20, offset=0):
    statement = (select(TavernChat).where(TavernChat.owner_id == owner)
                 .order_by(TavernChat.last_message_at.desc().nulls_last(),
                           TavernChat.updated_at.desc(), TavernChat.id)
                 .offset(offset).limit(limit))
    return list(db.scalars(statement))
