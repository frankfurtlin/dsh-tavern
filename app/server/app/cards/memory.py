"""Long-term card memories: CRUD, model-assisted extraction and prompt injection."""
import json
import logging
from pathlib import Path

from sqlalchemy import case, func, select

from ..errors import Problem
from ..models import Task, now, uid
from ..play_controls.models import WorldChatMessage, WorldChatSession
from . import space
from .models import CardMemory, CharacterCard

logger = logging.getLogger("reader")

KINDS = ("fact", "preference", "relationship", "event")
MEMORY_LIMIT = 40
INJECT_LIMIT = 6
INJECT_CHARS = 3000
AUTO_EVERY = 8
HISTORY_MESSAGES = 60
HISTORY_CHARS = 12000
OUTPUT_LIMIT = 2048
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "card_memory_extract.txt"
INJECTION_TITLE = "【长期记忆】"


def _text(value, limit):
    return (value or "").strip()[:limit]


def memory_out(row):
    return {"id": row.id, "kind": row.kind, "title": row.title, "content": row.content,
            "salience": row.salience, "enabled": row.enabled, "source": row.source,
            "updatedAt": row.updated_at.isoformat()}


def list_memories(db, owner, card_id):
    return list(db.scalars(select(CardMemory).where(
        CardMemory.owner_id == owner, CardMemory.card_id == card_id,
    ).order_by(CardMemory.salience.desc(), CardMemory.updated_at.desc(), CardMemory.id.desc())))


def get_memory(db, owner, card_id, memory_id):
    row = db.scalar(select(CardMemory).where(
        CardMemory.id == memory_id, CardMemory.owner_id == owner, CardMemory.card_id == card_id))
    if row is None:
        raise Problem("RESOURCE_NOT_FOUND", "这条记忆不存在", 404)
    return row


def _validate(values):
    kind = values.get("kind") or "fact"
    if kind not in KINDS:
        raise Problem("MEMORY_INVALID", "记忆类型无效", 422)
    title = _text(values.get("title"), 120)
    content = _text(values.get("content"), 2000)
    if not title or not content:
        raise Problem("MEMORY_INVALID", "记忆标题和内容不能为空", 422)
    try:
        salience = int(values.get("salience", 50))
    except (TypeError, ValueError):
        salience = 50
    salience = max(1, min(100, salience))
    return kind, title, content, salience


def _trim(db, owner, card_id):
    """Keep at most MEMORY_LIMIT rows, evicting auto memories of low salience first."""
    count = db.scalar(select(func.count()).select_from(CardMemory).where(
        CardMemory.owner_id == owner, CardMemory.card_id == card_id))
    excess = int(count or 0) - MEMORY_LIMIT
    if excess <= 0:
        return 0
    rows = list(db.scalars(select(CardMemory).where(
        CardMemory.owner_id == owner, CardMemory.card_id == card_id,
    ).order_by(case((CardMemory.source == "auto", 0), else_=1),
               CardMemory.salience.asc(), CardMemory.updated_at.asc()).limit(excess)))
    for row in rows:
        db.delete(row)
    return len(rows)


def create_memory(db, owner, card_id, values):
    kind, title, content, salience = _validate(values)
    row = CardMemory(id=uid(), owner_id=owner, card_id=card_id, kind=kind, title=title,
                     content=content, salience=salience,
                     enabled=bool(values.get("enabled", True)), source="manual", updated_at=now())
    db.add(row)
    db.flush()
    _trim(db, owner, card_id)
    db.commit()
    return row


def update_memory(db, owner, card_id, memory_id, values):
    row = get_memory(db, owner, card_id, memory_id)
    if values.get("kind") is not None:
        if values["kind"] not in KINDS:
            raise Problem("MEMORY_INVALID", "记忆类型无效", 422)
        row.kind = values["kind"]
    if values.get("title") is not None:
        title = _text(values["title"], 120)
        if not title:
            raise Problem("MEMORY_INVALID", "记忆标题不能为空", 422)
        row.title = title
    if values.get("content") is not None:
        content = _text(values["content"], 2000)
        if not content:
            raise Problem("MEMORY_INVALID", "记忆内容不能为空", 422)
        row.content = content
    if values.get("salience") is not None:
        try:
            row.salience = max(1, min(100, int(values["salience"])))
        except (TypeError, ValueError):
            raise Problem("MEMORY_INVALID", "记忆权重必须是整数", 422)
    if values.get("enabled") is not None:
        row.enabled = bool(values["enabled"])
    row.updated_at = now()
    db.commit()
    return row


def delete_memory(db, owner, card_id, memory_id):
    row = get_memory(db, owner, card_id, memory_id)
    db.delete(row)
    db.commit()


# --- extraction ------------------------------------------------------------

def _prompt():
    text = PROMPT_FILE.read_text(encoding="utf-8")
    return text.strip()


def _conversation(db, owner, card):
    if not card.world_id:
        return []
    session = db.scalar(select(WorldChatSession).where(
        WorldChatSession.owner_id == owner, WorldChatSession.world_id == card.world_id,
        WorldChatSession.status == "active",
    ).order_by(WorldChatSession.updated_at.desc(), WorldChatSession.id.desc()).limit(1))
    if session is None:
        return []
    rows = list(db.scalars(select(WorldChatMessage).where(
        WorldChatMessage.owner_id == owner, WorldChatMessage.session_id == session.id,
        WorldChatMessage.role.in_(("user", "assistant")), WorldChatMessage.status == "saved",
    ).order_by(WorldChatMessage.sequence.desc()).limit(HISTORY_MESSAGES)))
    items, total = [], 0
    for row in rows:
        text = _text(row.text, 2000)
        if not text:
            continue
        total += len(text)
        if total > HISTORY_CHARS:
            break
        items.append({"role": row.role, "text": text})
    return list(reversed(items))


def _titles(db, owner, card_id):
    rows = db.scalars(select(CardMemory.title).where(
        CardMemory.owner_id == owner, CardMemory.card_id == card_id))
    return {title.strip().lower() for title in rows if (title or "").strip()}


def _parse(text):
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").strip()
        if "\n" in body:
            body = body.split("\n", 1)[1]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        raise ValueError("response is not JSON")
    payload = json.loads(body[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("response is not a JSON object")
    memories = payload.get("memories")
    if not isinstance(memories, list):
        raise ValueError("response has no memories list")
    return memories


def _generate(profile, data):
    from ..worker import adapter_for
    from ..completion_policy import completion_limit
    adapter = adapter_for()
    limit = completion_limit(profile, "generation")
    return adapter.generate(profile, _prompt(), data, limit)


def _extract_once(db, owner, card, profile):
    data = {"card": {"name": card.name, "description": card.description[:1200],
                     "personality": card.personality[:1200], "scenario": card.scenario[:1200]},
            "existingTitles": sorted(_titles(db, owner, card.id))[:200],
            "messages": _conversation(db, owner, card)}
    candidates = _parse(_generate(profile, data).get("text"))
    existing = _titles(db, owner, card.id)
    rows, skipped = [], 0
    for item in candidates:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            kind, title, content, salience = _validate(item)
        except Problem:
            skipped += 1
            continue
        if title.lower() in existing:
            skipped += 1
            continue
        row = CardMemory(id=uid(), owner_id=owner, card_id=card.id, kind=kind, title=title,
                         content=content, salience=salience, enabled=True, source="auto",
                         updated_at=now())
        db.add(row)
        db.flush()
        rows.append(row)
        existing.add(title.lower())
    _trim(db, owner, card.id)
    return rows, skipped


def extract(db, owner, card, profile):
    """Run extraction, retrying once on model or parse failure. Probing errors
    are reported as MEMORY_EXTRACT_FAILED without leaving partial rows."""
    last = None
    for attempt in range(2):
        try:
            rows, skipped = _extract_once(db, owner, card, profile)
            db.commit()
            return rows, skipped
        except Exception as error:  # noqa: BLE001 - model/parse failures share one error
            db.rollback()
            last = error
            logger.warning("card memory extract attempt %s failed card=%s error=%s",
                           attempt + 1, card.id, error)
    raise Problem("MEMORY_EXTRACT_FAILED", "记忆整理失败，请稍后重试", 502, retryable=True)


# --- injection -------------------------------------------------------------

def injection(db, owner, card):
    rows = [row for row in list_memories(db, owner, card.id) if row.enabled][:INJECT_LIMIT]
    lines, total = [], 0
    for row in rows:
        line = f"- {row.title}：{row.content}"
        if total + len(line) > INJECT_CHARS:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return ""
    return (INJECTION_TITLE + "\n"
            "以下记忆来自读者自述或双方约定，只作扮演补充，不作为世界正史；与 source 冲突时以 source 为准。\n"
            + "\n".join(lines))


def preview_payload(db, owner, card):
    rows = [row for row in list_memories(db, owner, card.id) if row.enabled][:INJECT_LIMIT]
    return {"memories": [{"id": row.id, "title": row.title, "salience": row.salience} for row in rows],
            "memorySection": injection(db, owner, card)}


# --- automatic extraction --------------------------------------------------

def maybe_enqueue(db, task):
    """After a saved assistant reply, queue extraction every AUTO_EVERY replies."""
    payload = task.payload or {}
    chat_id = payload.get("chatId")
    world_id = payload.get("worldId")
    if not chat_id or not world_id:
        return
    card = space.card_for_world(db, task.owner_id, world_id)
    if card is None:
        return
    if card.memory_task_id:
        pending = db.get(Task, card.memory_task_id)
        if pending is not None and pending.state not in ("succeeded", "failed", "cancelled"):
            return
        card.memory_task_id = None
    saved = db.scalar(select(func.count()).select_from(WorldChatMessage).where(
        WorldChatMessage.owner_id == task.owner_id, WorldChatMessage.session_id == chat_id,
        WorldChatMessage.role == "assistant", WorldChatMessage.status == "saved"))
    if not saved or saved % AUTO_EVERY:
        return
    key = f"memory:{card.id}:{saved}"
    existing = db.scalar(select(Task).where(
        Task.owner_id == task.owner_id, Task.task_type == "card_memory_extract",
        Task.payload["memoryKey"].astext == key))
    if existing is not None:
        return
    from ..execution_prompts import freeze_prompts
    memory_task = Task(id=uid(), owner_id=task.owner_id, task_type="card_memory_extract",
                       payload={"cardId": card.id, "worldId": world_id, "memoryKey": key},
                       checkpoint={"executionPrompts": freeze_prompts()})
    db.add(memory_task)
    db.flush()
    card.memory_task_id = memory_task.id


def process_task(task_id):
    """Worker handler for a queued card_memory_extract task."""
    from ..db import SessionLocal
    from ..story_flow import default_profile
    from ..worker import assert_task_active, transition

    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None or task.state in ("succeeded", "failed", "cancelled"):
            return
        owner = task.owner_id
        card = db.get(CharacterCard, (task.payload or {}).get("cardId"))
        if card is None or card.deleted_at is not None:
            task.state = "failed"
            task.result_outcome = "failed"
            db.commit()
            return
        transition(task_id, "running")
        db.expire_all()
        task = db.get(Task, task_id)
        card = db.get(CharacterCard, card.id)
        assert_task_active(db, task)
        try:
            profile = default_profile(db, owner)
            rows, skipped = extract(db, owner, card, profile)
            task = db.get(Task, task_id)
            task.state, task.persistence_state = "succeeded", "saved"
            task.result_outcome = "complete"
            task.result = {"cardId": card.id, "added": len(rows), "skipped": skipped}
            card.memory_task_id = None
            db.commit()
        except Exception as error:  # noqa: BLE001 - silent background failure
            db.rollback()
            logger.warning("card memory task failed task=%s card=%s error=%s",
                           task_id, card.id, error)
            task = db.get(Task, task_id)
            card = db.get(CharacterCard, card.id)
            if task is not None:
                task.state, task.result_outcome = "failed", "failed"
                task.result = {"cardId": card.id if card else ""}
            if card is not None:
                card.memory_task_id = None
            db.commit()
