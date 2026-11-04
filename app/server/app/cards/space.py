"""Card space: lorebooks, presets, regex scripts, prompt injection and preview."""
import logging
import re

from sqlalchemy import select, update

from ..errors import Problem
from ..models import now, uid
from .models import CharacterCard, CardLore, ChatPreset, RegexScript

logger = logging.getLogger("reader")

CARD_SECTION_LIMIT = 6000
LORE_SECTION_LIMIT = 6000
PRESET_SECTION_LIMIT = 4000
MAX_MATCHED_LORE = 6
RECENT_USER_MESSAGES = 4
MAX_KEYS = 20
INJECTION_HEADER = "以下内容仅作扮演补充，与 source 冲突时以 source 为准。"

CARD_FIELDS = ("description", "personality", "scenario", "system_prompt", "post_history_instructions")


def _text(value, limit):
    return (value or "").strip()[:limit]


def _keys(value):
    if not isinstance(value, list):
        raise Problem("CARD_INVALID", "世界书的 keys 必须是字符串列表", 422)
    items = []
    for key in value:
        text = _text(key if isinstance(key, str) else str(key), 200)
        if text and text not in items:
            items.append(text)
        if len(items) >= MAX_KEYS:
            break
    if not items:
        raise Problem("CARD_INVALID", "世界书条目至少需要一个关键词", 422)
    return items


# --- lorebooks -------------------------------------------------------------

def list_lore(db, owner, card_id):
    return list(db.scalars(select(CardLore).where(
        CardLore.owner_id == owner, CardLore.card_id == card_id,
    ).order_by(CardLore.order.asc(), CardLore.created_at.asc(), CardLore.id.asc())))


def lore_out(row):
    return {"id": row.id, "name": row.name, "keys": list(row.keys or []),
            "content": row.content, "enabled": row.enabled, "order": row.order,
            "source": row.source}


def get_lore(db, owner, card_id, lore_id):
    row = db.scalar(select(CardLore).where(CardLore.id == lore_id, CardLore.owner_id == owner,
                                          CardLore.card_id == card_id))
    if row is None:
        raise Problem("RESOURCE_NOT_FOUND", "这条世界书条目不存在", 404)
    return row


def create_lore(db, owner, card_id, values):
    keys = _keys(values.get("keys"))
    row = CardLore(id=uid(), owner_id=owner, card_id=card_id,
                   name=_text(values.get("name") or keys[0], 160) or keys[0][:160],
                   keys=keys, content=_text(values.get("content"), 8000),
                   enabled=bool(values.get("enabled", True)),
                   order=int(values.get("order", 100)), source="manual", updated_at=now())
    db.add(row)
    db.commit()
    return row


def update_lore(db, owner, card_id, lore_id, values):
    row = get_lore(db, owner, card_id, lore_id)
    if "keys" in values and values["keys"] is not None:
        row.keys = _keys(values["keys"])
    if "name" in values and values["name"] is not None:
        row.name = _text(values["name"], 160) or (list(row.keys or []) or ["世界书条目"])[0][:160]
    if "content" in values and values["content"] is not None:
        row.content = _text(values["content"], 8000)
    if "enabled" in values and values["enabled"] is not None:
        row.enabled = bool(values["enabled"])
    if "order" in values and values["order"] is not None:
        row.order = int(values["order"])
    row.updated_at = now()
    db.commit()
    return row


def delete_lore(db, owner, card_id, lore_id):
    row = get_lore(db, owner, card_id, lore_id)
    db.delete(row)
    db.commit()


def import_lore(db, owner, card_id, entries):
    for entry in entries or []:
        keys = [key for key in (entry.get("keys") or []) if isinstance(key, str) and key.strip()][:MAX_KEYS]
        if not keys:
            continue
        db.add(CardLore(id=uid(), owner_id=owner, card_id=card_id,
                        name=_text(entry.get("name") or keys[0], 160) or keys[0][:160],
                        keys=keys, content=_text(entry.get("content"), 8000),
                        enabled=bool(entry.get("enabled", True)), order=int(entry.get("order", 100)),
                        source="import", updated_at=now()))


# --- presets ---------------------------------------------------------------

def preset_out(row):
    return {"id": row.id, "name": row.name, "systemSuffix": row.system_suffix,
            "enabled": row.enabled, "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat()}


def list_presets(db, owner):
    return list(db.scalars(select(ChatPreset).where(ChatPreset.owner_id == owner).order_by(
        ChatPreset.created_at.asc(), ChatPreset.id.asc())))


def get_preset(db, owner, preset_id):
    row = db.scalar(select(ChatPreset).where(ChatPreset.id == preset_id, ChatPreset.owner_id == owner))
    if row is None:
        raise Problem("RESOURCE_NOT_FOUND", "这个预设不存在", 404)
    return row


def create_preset(db, owner, values):
    row = ChatPreset(id=uid(), owner_id=owner, name=_text(values["name"], 80),
                     system_suffix=_text(values.get("systemSuffix"), 4000),
                     enabled=bool(values.get("enabled", True)), updated_at=now())
    db.add(row)
    db.commit()
    return row


def update_preset(db, owner, preset_id, values):
    row = get_preset(db, owner, preset_id)
    if "name" in values and values["name"] is not None:
        row.name = _text(values["name"], 80)
    if "systemSuffix" in values and values["systemSuffix"] is not None:
        row.system_suffix = _text(values["systemSuffix"], 4000)
    if "enabled" in values and values["enabled"] is not None:
        row.enabled = bool(values["enabled"])
    row.updated_at = now()
    db.commit()
    return row


def delete_preset(db, owner, preset_id):
    row = get_preset(db, owner, preset_id)
    db.execute(update(CharacterCard).where(CharacterCard.owner_id == owner,
                                           CharacterCard.preset_id == preset_id).values(preset_id=None))
    db.delete(row)
    db.commit()


def bind_preset(db, owner, card, preset_id):
    if preset_id is None:
        card.preset_id = None
        return
    preset = get_preset(db, owner, preset_id)
    card.preset_id = preset.id


# --- regex scripts ---------------------------------------------------------

def regex_out(row):
    return {"id": row.id, "name": row.name, "pattern": row.pattern,
            "replacement": row.replacement, "enabled": row.enabled, "order": row.order}


def validate_regex(pattern, replacement):
    try:
        re.compile(pattern).sub(replacement or "", "")
    except re.error as error:
        raise Problem("REGEX_INVALID", f"正则表达式无效：{error}", 422)


def get_regex(db, owner, script_id):
    row = db.scalar(select(RegexScript).where(RegexScript.id == script_id, RegexScript.owner_id == owner))
    if row is None:
        raise Problem("RESOURCE_NOT_FOUND", "这条替换规则不存在", 404)
    return row


def list_regex(db, owner):
    return list(db.scalars(select(RegexScript).where(RegexScript.owner_id == owner).order_by(
        RegexScript.order.asc(), RegexScript.created_at.asc(), RegexScript.id.asc())))


def create_regex(db, owner, values):
    validate_regex(values["pattern"], values.get("replacement", ""))
    row = RegexScript(id=uid(), owner_id=owner, name=_text(values["name"], 80),
                      pattern=values["pattern"], replacement=_text(values.get("replacement"), 2000),
                      enabled=bool(values.get("enabled", True)), order=int(values.get("order", 0)),
                      updated_at=now())
    db.add(row)
    db.commit()
    return row


def update_regex(db, owner, script_id, values):
    row = get_regex(db, owner, script_id)
    pattern = values["pattern"] if values.get("pattern") is not None else row.pattern
    replacement = values["replacement"] if values.get("replacement") is not None else row.replacement
    validate_regex(pattern, replacement)
    if values.get("pattern") is not None:
        row.pattern = values["pattern"]
    if values.get("replacement") is not None:
        row.replacement = _text(values["replacement"], 2000)
    if values.get("name") is not None:
        row.name = _text(values["name"], 80)
    if values.get("enabled") is not None:
        row.enabled = bool(values["enabled"])
    if values.get("order") is not None:
        row.order = int(values["order"])
    row.updated_at = now()
    db.commit()
    return row


def delete_regex(db, owner, script_id):
    row = get_regex(db, owner, script_id)
    db.delete(row)
    db.commit()


def apply_regex(db, owner, text):
    """Apply enabled output substitutions in order, before the reply is saved."""
    output = text
    for row in list_regex(db, owner):
        if not row.enabled:
            continue
        try:
            output = re.sub(row.pattern, row.replacement or "", output)
        except re.error as error:
            logger.warning("regex script skipped id=%s error=%s", row.id, error)
    return output


# --- prompt injection ------------------------------------------------------

def _card_section(card):
    lines = [f"名称：{_text(card.name, 160)}"]
    labels = {"description": "描述", "personality": "性格", "scenario": "场景",
              "system_prompt": "系统提示", "post_history_instructions": "后置指令"}
    for field, label in labels.items():
        value = _text(getattr(card, field), 20000)
        if value:
            lines.append(f"{label}：{value}")
    body = "\n".join(lines)[:CARD_SECTION_LIMIT]
    return "【角色卡设定】\n" + body


def _match_text(db, owner, card_id, message, recent=()):
    haystack = "\n".join([message or "", *recent]).lower()
    if not haystack.strip():
        return []
    rows = list(db.scalars(select(CardLore).where(
        CardLore.owner_id == owner, CardLore.card_id == card_id, CardLore.enabled.is_(True),
    ).order_by(CardLore.order.asc(), CardLore.created_at.asc(), CardLore.id.asc())))
    matched = []
    for row in rows:
        keys = [key for key in (row.keys or []) if isinstance(key, str) and key.strip()]
        if any(key.lower() in haystack for key in keys):
            matched.append(row)
            if len(matched) >= MAX_MATCHED_LORE:
                break
    return matched


def _lore_section(matched):
    parts = []
    used = 0
    for row in matched:
        block = f"· {row.name}\n{row.content}"
        if used + len(block) > LORE_SECTION_LIMIT:
            break
        parts.append(block)
        used += len(block)
    return "【世界书】\n" + "\n".join(parts) if parts else ""


def _preset_section(db, owner, card):
    if not card.preset_id:
        return "", None
    preset = db.scalar(select(ChatPreset).where(ChatPreset.id == card.preset_id,
                                               ChatPreset.owner_id == owner))
    if preset is None or not preset.enabled:
        return "", None
    suffix = _text(preset.system_suffix, PRESET_SECTION_LIMIT)
    if not suffix:
        return "", preset
    return "【预设】\n" + suffix, preset


def injection(db, owner, card, message, recent=()):
    """Render the appended system-prompt text for a card chat turn."""
    matched = _match_text(db, owner, card.id, message, recent)
    sections = [_card_section(card), _lore_section(matched)]
    preset_text, _preset = _preset_section(db, owner, card)
    if preset_text:
        sections.append(preset_text)
    body = "\n\n".join(section for section in sections if section)
    return INJECTION_HEADER + "\n\n" + body if body else ""


def recent_user_messages(payload):
    texts = []
    for item in reversed(payload.get("messages") or []):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            texts.append(text)
        if len(texts) >= RECENT_USER_MESSAGES:
            break
    return texts


def card_for_world(db, owner, world_id):
    if not world_id:
        return None
    return db.scalar(select(CharacterCard).where(
        CharacterCard.owner_id == owner, CharacterCard.world_id == world_id,
        CharacterCard.deleted_at.is_(None),
    ).order_by(CharacterCard.created_at.desc(), CharacterCard.id.desc()).limit(1))


def chat_injection(db, task):
    """System-prompt append for a world_character_chat generation call."""
    payload = task.payload or {}
    card = card_for_world(db, task.owner_id, payload.get("worldId"))
    if card is None:
        return ""
    return injection(db, task.owner_id, card, payload.get("message") or "",
                     recent_user_messages(payload))


def preview(db, owner, card, message):
    matched = _match_text(db, owner, card.id, message)
    preset_text, preset = _preset_section(db, owner, card)
    return {"loreMatched": [{"id": row.id, "name": row.name, "keys": list(row.keys or [])} for row in matched],
            "preset": {"id": preset.id, "name": preset.name} if preset else None,
            "systemSuffix": _text(preset.system_suffix, PRESET_SECTION_LIMIT) if preset else ""}
