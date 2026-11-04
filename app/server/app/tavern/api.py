"""Native tavern (SillyTavern-like) API: characters, chats, SSE replies."""
import json
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import AliasChoices, Field
from sqlalchemy import select

from ..cards import exporting, parse, personas
from ..cards import service as card_service
from ..cards.models import CharacterCard
from ..db import get_db
from ..errors import Problem
from ..models import now
from ..schemas import Strict
from ..security import current_user
from ..story_flow import default_profile
from . import prompt as prompt_builder
from . import service, stream
from .models import TavernChat, TavernMessage, TavernWorldInfo

router = APIRouter(prefix="/tavern", tags=["tavern"])

PROMPT_DEFAULTS = {"mainPrompt": "", "temperature": 0.8, "topP": 0.95, "maxTokens": 1024, "stop": []}


class TavernCardPatch(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20000)
    personality: str | None = Field(default=None, max_length=20000)
    scenario: str | None = Field(default=None, max_length=20000)
    firstMessage: str | None = Field(default=None, max_length=20000)
    systemPrompt: str | None = Field(default=None, max_length=20000)
    postHistoryInstructions: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=20)
    alternateGreetings: list[str] | None = Field(default=None, max_length=10)


class ChatCreate(Strict):
    greetingId: int | None = Field(default=None, ge=0)


class ChatPatch(Strict):
    title: str = Field(min_length=1, max_length=160)


class MessageInput(Strict):
    # Accept both {"message": ...} (contract) and {"text": ...} (P0 client payload).
    message: str = Field(min_length=1, max_length=4000,
                         validation_alias=AliasChoices("message", "text"))
    personaId: str | None = Field(default=None, max_length=32)


class MessageEdit(Strict):
    text: str = Field(min_length=1, max_length=4000)


class RegenerateInput(Strict):
    personaId: str | None = Field(default=None, max_length=32)


class WorldInfoInput(Strict):
    keys: list[str] = Field(default_factory=list, max_length=20)
    content: str = Field(min_length=1, max_length=8000)
    position: str = Field(default="before_char", max_length=20)
    depth: int = Field(default=4, ge=0, le=100)
    constant: bool = False
    probability: int = Field(default=100, ge=1, le=100)
    order: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True


class WorldInfoUpdate(Strict):
    keys: list[str] | None = Field(default=None, max_length=20)
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    position: str | None = Field(default=None, max_length=20)
    depth: int | None = Field(default=None, ge=0, le=100)
    constant: bool | None = None
    probability: int | None = Field(default=None, ge=1, le=100)
    order: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class PreviewInput(Strict):
    message: str = Field(default="", max_length=4000)


class PromptInput(Strict):
    systemPrompt: str | None = Field(default=None, max_length=20000)
    mainPrompt: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    topP: float | None = Field(default=None, ge=0, le=1)
    maxTokens: int | None = Field(default=None, ge=16, le=8192)
    stop: list[str] | None = Field(default=None, max_length=8)


def _lore_rows(db, owner, card):
    return [SimpleNamespace(id=row.id, keys=list(row.keys or []), content=row.content,
                            enabled=row.enabled, order=row.order,
                            name=(list(row.keys or [""])[0] or "世界书"))
            for row in service.world_info_rows(db, card)]


def _history(db, chat, *, before=None):
    return [row for row in service.list_messages(db, chat) if before is None or row.sequence < before]


def _reply_context(db, owner, chat, card, history_rows, message, persona_id):
    settings = dict(card.prompt_settings or {})
    profile = default_profile(db, owner)
    persona = personas.get_persona(db, owner, persona_id) if persona_id else None
    persona_view = {"name": persona.name, "description": persona.description} if persona else None
    limit = stream.output_limit_for(profile, settings)
    history = [{"role": row.role, "text": service.swipe_content(row.swipes, row.active_swipe)}
               for row in history_rows]
    built = prompt_builder.build(card, chat, history=history, message=message,
                                 user_name=persona_view["name"] if persona_view else "User",
                                 persona=persona_view, settings=settings, profile=profile,
                                 output_limit=limit,
                                 world_info=service.world_info_rows(db, card))
    return built["systemPrompt"], built["data"], limit, stream.profile_view(profile), profile.model_id


def _prompt_view(card):
    settings = dict(PROMPT_DEFAULTS)
    settings.update(card.prompt_settings or {})
    return {"systemPrompt": card.system_prompt or "", **settings}


# ---------------------------------------------------------------- characters
@router.get("/characters")
def list_characters(q: str = Query(default="", max_length=100),
                    sort: str = Query(default="recent"),
                    user=Depends(current_user), db=Depends(get_db)):
    rows = list(db.scalars(select(CharacterCard).where(
        CharacterCard.owner_id == user.id, CharacterCard.deleted_at.is_(None))))
    if q.strip():
        needle = q.strip().lower()
        rows = [row for row in rows if needle in (row.name or "").lower()]
    stats = service.chat_stats(db, user.id)
    items = []
    for row in rows:
        item = service.card_summary(row)
        stat = stats.get(row.id) or {}
        item["chatCount"] = stat.get("count", 0)
        item["lastChatAt"] = service.iso(stat.get("last"))
        items.append(item)
    if sort == "name":
        items.sort(key=lambda item: (item["name"] or "").lower())
    elif sort == "created":
        items.sort(key=lambda item: item["createdAt"] or "", reverse=True)
    else:
        items.sort(key=lambda item: (item["lastChatAt"] or item["createdAt"] or ""), reverse=True)
    return {"items": items}


@router.post("/characters/imports", status_code=201)
async def import_character(file: UploadFile = File(...), user=Depends(current_user),
                           db=Depends(get_db)):
    raw = await file.read()
    card = service.import_card(db, user.id, raw, file.filename or "")
    return {"card": service.card_summary(card)}


@router.get("/characters/{card_id}")
def get_character(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    return {"card": service.card_summary(card), "prompt": _prompt_view(card),
            "worldInfoCount": len(service.world_info_rows(db, card))}


@router.patch("/characters/{card_id}")
def patch_character(card_id: str, payload: TavernCardPatch, user=Depends(current_user),
                    db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    fields = payload.model_fields_set
    mapping = {"name": "name", "description": "description", "personality": "personality",
               "scenario": "scenario", "firstMessage": "first_message",
               "systemPrompt": "system_prompt",
               "postHistoryInstructions": "post_history_instructions"}
    for api_name, column in mapping.items():
        if api_name in fields:
            value = getattr(payload, api_name)
            setattr(card, column, (value or "").strip())
    if "tags" in fields:
        card.tags = [str(tag).strip() for tag in (payload.tags or []) if str(tag).strip()][:20]
    if "alternateGreetings" in fields:
        card.alternate_greetings = [str(item).strip() for item in (payload.alternateGreetings or [])
                                    if str(item).strip()][:10]
    card.updated_at = now()
    db.commit()
    return {"card": service.card_summary(card)}


@router.delete("/characters/{card_id}", status_code=204)
def delete_character(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    card.deleted_at = now()
    db.commit()
    return Response(status_code=204)


@router.get("/characters/{card_id}/avatar")
def character_avatar(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    path = card_service.avatar_file(card, user.id)
    return FileResponse(path, media_type="image/png")


@router.get("/characters/{card_id}/export")
def export_character(card_id: str, format: str = Query(default="json"),
                     user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    lore = _lore_rows(db, user.id, card)
    if format == "png":
        data = exporting.card_png(card, lore, user.id)
        return Response(data, media_type="image/png",
                        headers=exporting.filename_header(card.name, ".png"))
    if format == "json":
        document = exporting.card_document(card, lore)
        return Response(json.dumps(document, ensure_ascii=False), media_type="application/json",
                        headers=exporting.filename_header(card.name, ".json"))
    raise Problem("INVALID_REQUEST", "导出格式仅支持 png 或 json", 422)


# -------------------------------------------------------------------- chats
@router.get("/characters/{card_id}/chats")
def list_chats(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    rows = list(db.scalars(select(TavernChat).where(
        TavernChat.owner_id == user.id, TavernChat.card_id == card.id,
    ).order_by(TavernChat.updated_at.desc())))
    items = []
    for row in rows:
        item = service.chat_out(row)
        messages = service.list_messages(db, row)
        last = messages[-1] if messages else None
        item["lastText"] = service.swipe_content(last.swipes, last.active_swipe) if last else ""
        item["messageCount"] = len(messages)
        items.append(item)
    return {"items": items, "card": service.card_summary(card)}


@router.post("/characters/{card_id}/chats", status_code=201)
def create_chat(card_id: str, payload: ChatCreate | None = None,
                user=Depends(current_user), db=Depends(get_db)):
    payload = payload or ChatCreate()
    card = service.card_or_404(db, user.id, card_id)
    greetings = [str(item).strip() for item in (card.alternate_greetings or []) if str(item).strip()]
    if payload.greetingId is None:
        greeting = (card.first_message or "").strip()
    else:
        if payload.greetingId >= len(greetings):
            raise Problem("GREETING_INVALID", "这个开场白不存在", 422)
        greeting = greetings[payload.greetingId]
    chat = TavernChat(id=service.uid(), owner_id=user.id, card_id=card.id,
                      title=f"{card.name} 的对话"[:160], model_name="",
                      updated_at=now())
    db.add(chat)
    db.flush()
    if greeting:
        service.add_message(db, chat, user.id, "assistant", greeting, "")
    db.commit()
    return {"chat": service.chat_out(chat)}


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id)
    card = service.card_or_404(db, user.id, chat.card_id)
    return {"chat": {**service.chat_out(chat), "card": service.card_summary(card)},
            "messages": [service.message_out(row) for row in service.list_messages(db, chat)]}


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, payload: ChatPatch, user=Depends(current_user),
               db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    chat.title = payload.title.strip()
    chat.updated_at = now()
    db.commit()
    return {"chat": service.chat_out(chat)}


@router.delete("/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    for row in service.list_messages(db, chat):
        db.delete(row)
    db.delete(chat)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------ chat messages
@router.post("/chats/{chat_id}/messages")
def send_message(chat_id: str, payload: MessageInput, user=Depends(current_user),
                 db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    card = service.card_or_404(db, user.id, chat.card_id)
    text = payload.message.strip()
    if not text:
        raise Problem("MESSAGE_REQUIRED", "消息不能为空", 422)
    system_prompt, data, limit, profile, model_name = _reply_context(
        db, user.id, chat, card, _history(db, chat), text, payload.personaId)
    user_row = service.add_message(db, chat, user.id, "user", text, "")
    db.commit()
    initial = {"type": "message", "userMessage": service.message_out(user_row),
               "assistantSequence": user_row.sequence + 1}
    return StreamingResponse(
        stream.reply_events(owner=user.id, chat_id=chat.id, mode="message",
                            system_prompt=system_prompt, data=data, output_limit=limit,
                            profile=profile, model_name=model_name, initial=initial),
        media_type=stream.SSE_MEDIA_TYPE, headers=stream.SSE_HEADERS)


@router.post("/chats/{chat_id}/regenerate")
def regenerate(chat_id: str, payload: RegenerateInput | None = None,
               user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    card = service.card_or_404(db, user.id, chat.card_id)
    target = service.last_assistant(db, chat)
    if target is None:
        raise Problem("NOTHING_TO_REGENERATE", "没有可以重新生成的回复", 409)
    history = [row for row in service.list_messages(db, chat) if row.sequence < target.sequence]
    persona_id = payload.personaId if payload else None
    message = next((service.swipe_content(row.swipes, row.active_swipe)
                    for row in reversed(history) if row.role == "user"), "")
    system_prompt, data, limit, profile, model_name = _reply_context(
        db, user.id, chat, card, history, message, persona_id)
    return StreamingResponse(
        stream.reply_events(owner=user.id, chat_id=chat.id, mode="regenerate",
                            system_prompt=system_prompt, data=data, output_limit=limit,
                            profile=profile, model_name=model_name, target_id=target.id,
                            initial={"type": "message", "messageId": target.id,
                                     "activeSwipe": target.active_swipe}),
        media_type=stream.SSE_MEDIA_TYPE, headers=stream.SSE_HEADERS)


@router.post("/chats/{chat_id}/messages/{message_id}/swipes/{index}/select")
def select_swipe(chat_id: str, message_id: str, index: int,
                 user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id)
    row = service.message_or_404(db, chat, message_id)
    swipes = list(row.swipes or [])
    if index < 0 or index >= len(swipes):
        raise Problem("SWIPE_INVALID", "这个版本不存在", 422)
    row.active_swipe = index
    row.updated_at = now()
    db.commit()
    return {"message": service.message_out(row)}


@router.patch("/chats/{chat_id}/messages/{message_id}")
def edit_message(chat_id: str, message_id: str, payload: MessageEdit,
                 user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    row = service.message_or_404(db, chat, message_id)
    if row.role != "user":
        raise Problem("MESSAGE_NOT_EDITABLE", "只能修改自己发送的消息", 422)
    text = payload.text.strip()
    if not text:
        raise Problem("MESSAGE_REQUIRED", "消息不能为空", 422)
    deleted = len([item for item in service.list_messages(db, chat) if item.sequence > row.sequence])
    service.truncate_after(db, chat, row.sequence)
    swipes = list(row.swipes or [{}])
    swipes[0] = {**swipes[0], "content": text}
    row.swipes = swipes
    row.active_swipe = 0
    row.updated_at = now()
    chat.updated_at = now()
    db.commit()
    return {"message": service.message_out(row), "deleted": deleted}


@router.delete("/chats/{chat_id}/messages/{message_id}", status_code=204)
def delete_message(chat_id: str, message_id: str, user=Depends(current_user),
                   db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    row = service.message_or_404(db, chat, message_id)
    service.truncate_after(db, chat, row.sequence)
    db.delete(row)
    chat.updated_at = now()
    db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------- world info
@router.get("/characters/{card_id}/world-info")
def list_world_info(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    rows = service.world_info_rows(db, card)
    return {"items": [service.world_info_out(row) for row in rows]}


@router.post("/characters/{card_id}/world-info", status_code=201)
def create_world_info(card_id: str, payload: WorldInfoInput, user=Depends(current_user),
                      db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    if payload.position not in service.POSITIONS:
        raise Problem("INVALID_REQUEST", "注入位置无效", 422)
    row = TavernWorldInfo(id=service.uid(), owner_id=user.id, card_id=card.id,
                          keys=[str(key).strip() for key in payload.keys if str(key).strip()],
                          content=payload.content.strip(), position=payload.position,
                          depth=payload.depth, constant=payload.constant,
                          probability=payload.probability, order=payload.order,
                          enabled=payload.enabled, source="manual", updated_at=now())
    db.add(row)
    db.commit()
    return {"item": service.world_info_out(row)}


def _world_info_or_404(db, owner, card, entry_id):
    row = db.get(TavernWorldInfo, entry_id)
    if row is None or row.owner_id != owner or row.card_id != card.id:
        raise Problem("RESOURCE_NOT_FOUND", "这条世界书不存在", 404)
    return row


@router.patch("/characters/{card_id}/world-info/{entry_id}")
def patch_world_info(card_id: str, entry_id: str, payload: WorldInfoUpdate,
                     user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    row = _world_info_or_404(db, user.id, card, entry_id)
    fields = payload.model_fields_set
    if "position" in fields and payload.position not in service.POSITIONS:
        raise Problem("INVALID_REQUEST", "注入位置无效", 422)
    mapping = {"keys": "keys", "content": "content", "position": "position",
               "depth": "depth", "constant": "constant", "probability": "probability",
               "order": "order", "enabled": "enabled"}
    for api_name, column in mapping.items():
        if api_name in fields:
            value = getattr(payload, api_name)
            if api_name == "keys":
                value = [str(key).strip() for key in (value or []) if str(key).strip()]
            if api_name == "content":
                value = (value or "").strip()
            setattr(row, column, value)
    row.updated_at = now()
    db.commit()
    return {"item": service.world_info_out(row)}


@router.delete("/characters/{card_id}/world-info/{entry_id}", status_code=204)
def drop_world_info(card_id: str, entry_id: str, user=Depends(current_user),
                    db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    row = _world_info_or_404(db, user.id, card, entry_id)
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/characters/{card_id}/world-info/preview")
def preview_world_info(card_id: str, payload: PreviewInput, user=Depends(current_user),
                       db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    rows = service.world_info_rows(db, card)
    history = [{"role": "user", "text": payload.message}] if payload.message else []
    matched = prompt_builder.match_world_info(rows, prompt_builder.scan_text(payload.message, history))
    return {"matched": [service.world_info_out(row) for row in matched],
            "injection": prompt_builder.world_section(matched)}


# ------------------------------------------------------------------- prompt
@router.get("/characters/{card_id}/prompt")
def get_prompt(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    return {"prompt": _prompt_view(card)}


@router.patch("/characters/{card_id}/prompt")
def patch_prompt(card_id: str, payload: PromptInput, user=Depends(current_user),
                 db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    fields = payload.model_fields_set
    if "systemPrompt" in fields:
        card.system_prompt = (payload.systemPrompt or "").strip()
    settings = dict(PROMPT_DEFAULTS)
    settings.update(card.prompt_settings or {})
    for key in ("mainPrompt", "temperature", "topP", "maxTokens", "stop"):
        if key in fields:
            settings[key] = getattr(payload, key)
    card.prompt_settings = settings
    card.updated_at = now()
    db.commit()
    return {"prompt": _prompt_view(card)}
