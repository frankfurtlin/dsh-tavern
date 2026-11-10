"""Native tavern (SillyTavern-like) API: characters, chats, SSE replies."""
import json
from types import SimpleNamespace

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import AliasChoices, Field
from sqlalchemy import func, select, update

from ..cards import exporting, parse, personas, presets
from ..cards import service as card_service
from ..cards.models import CharacterCard
from ..db import get_db
from ..errors import Problem
from ..models import now
from ..schemas import Strict
from ..security import current_user
from ..profiles import default_profile
from . import prompt as prompt_builder
from . import service, stream
from .models import TavernChat, TavernMessage, TavernPreset, TavernWorldInfo

router = APIRouter(prefix="/tavern", tags=["tavern"])

PROMPT_DEFAULTS = {"mainPrompt": "", "temperature": 0.8, "topP": 0.95, "maxTokens": 1024,
                   "stop": [], "system": "", "jailbreakPrompt": "", "postHistory": "",
                   "worldInfoPosition": "before_char"}

IMPORT_MAX_BYTES = 32 * 1024 * 1024
IMPORT_MAX_LINES = 20000


def _stamp() -> str:
    return now().strftime("%Y%m%d-%H%M%S")


def _default_chat_title(db, owner_id: str, card, requested: str | None) -> str:
    if requested:
        return requested.strip()[:160]
    count = db.scalar(select(func.count()).select_from(TavernChat).where(
        TavernChat.owner_id == owner_id, TavernChat.card_id == card.id)) or 0
    return f"{card.name} · {count + 1}"[:160]


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
    cardId: str | None = Field(default=None, max_length=32)
    cardIds: list[str] | None = Field(default=None, max_length=5)
    greetingId: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, min_length=1, max_length=160)
    presetId: str | None = Field(default=None, max_length=32)
    sampling: dict | None = None


class ChatPatch(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    presetId: str | None = Field(default=None, max_length=32)
    sampling: dict | None = None
    cardIds: list[str] | None = None  # group chats only: replace members (2..5, deduped)


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
    probability: int = Field(default=100, ge=0, le=100)
    order: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True


class WorldInfoUpdate(Strict):
    keys: list[str] | None = Field(default=None, max_length=20)
    content: str | None = Field(default=None, min_length=1, max_length=8000)
    position: str | None = Field(default=None, max_length=20)
    depth: int | None = Field(default=None, ge=0, le=100)
    constant: bool | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    order: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None


class PreviewInput(Strict):
    message: str = Field(default="", max_length=4000)
    chatId: str | None = Field(default=None, max_length=32)


class PromptInput(Strict):
    systemPrompt: str | None = Field(default=None, max_length=20000)
    mainPrompt: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    topP: float | None = Field(default=None, ge=0, le=1)
    maxTokens: int | None = Field(default=None, ge=16, le=8192)
    stop: list[str] | None = Field(default=None, max_length=8)
    system: str | None = Field(default=None, max_length=20000,
                               validation_alias=AliasChoices("system", "system_prompt"))
    main: str | None = Field(default=None, max_length=20000,
                             validation_alias=AliasChoices("main", "main_prompt"))
    jailbreakPrompt: str | None = Field(default=None, max_length=20000)
    postHistory: str | None = Field(default=None, max_length=20000)
    worldInfoPosition: str | None = Field(default=None, max_length=20)


class PresetInput(Strict):
    name: str = Field(min_length=1, max_length=80)
    system: str = Field(default="", max_length=20000)
    main: str = Field(default="", max_length=20000)
    jailbreak: str = Field(default="", max_length=20000)
    postHistory: str = Field(default="", max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    topP: float | None = Field(default=None, ge=0, le=1)
    maxTokens: int | None = Field(default=None, ge=16, le=8192)
    stop: list[str] = Field(default_factory=list, max_length=4)


class PresetUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    system: str | None = Field(default=None, max_length=20000)
    main: str | None = Field(default=None, max_length=20000)
    jailbreak: str | None = Field(default=None, max_length=20000)
    postHistory: str | None = Field(default=None, max_length=20000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    topP: float | None = Field(default=None, ge=0, le=1)
    maxTokens: int | None = Field(default=None, ge=16, le=8192)
    stop: list[str] | None = Field(default=None, max_length=4)
    isDefault: bool | None = Field(default=None,
                                   validation_alias=AliasChoices("isDefault", "is_default"))


def _clean_stop(values):
    cleaned = [str(item)[:200] for item in (values or []) if str(item).strip()]
    if len(cleaned) != len(values or []) or any(len(str(item)) > 200 for item in (values or [])):
        raise Problem("PRESET_INVALID", "stop 最多 4 条、每条不超过 200 字", 422)
    return cleaned


SAMPLING_KEYS = ("temperature", "topP", "maxTokens", "stop")


def _clean_sampling(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise Problem("INVALID_REQUEST", "sampling 必须是对象", 422)
    result = {}

    def number(key, snake_key, minimum, maximum):
        raw = value.get(key, value.get(snake_key))
        if raw is None:
            return None
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            raise Problem("INVALID_REQUEST", "采样参数必须是数字", 422)
        if parsed < minimum or parsed > maximum:
            raise Problem("INVALID_REQUEST", "采样参数超出范围", 422)
        return parsed

    temperature = number("temperature", "temperature", 0, 2)
    top_p = number("topP", "top_p", 0, 1)
    max_tokens = number("maxTokens", "max_tokens", 16, 8192)
    stop = value.get("stop")
    if temperature is not None:
        result["temperature"] = temperature
    if top_p is not None:
        result["topP"] = top_p
    if max_tokens is not None:
        result["maxTokens"] = int(max_tokens)
    if stop is not None:
        if not isinstance(stop, list):
            raise Problem("INVALID_REQUEST", "stop 必须是数组", 422)
        result["stop"] = _clean_stop(stop)
    return result


def _lore_rows(db, owner, card):
    return [SimpleNamespace(id=row.id, keys=list(row.keys or []), content=row.content,
                            enabled=row.enabled, order=row.order,
                            name=(list(row.keys or [""])[0] or "世界书"))
            for row in service.world_info_rows(db, card)]


def _history(db, chat, *, before=None):
    return [row for row in service.list_messages(db, chat) if before is None or row.sequence < before]


def _build_prompt(db, owner, card, chat, history_rows, message, persona_id,
                  preset=None, require_profile=True, speaker_member=None):
    group = chat is not None and (chat.mode or "single") == "group"
    cards = service.chat_cards(db, owner, chat) if group else None
    if group:
        if not cards:
            raise Problem("CHAT_NO_MEMBERS", "群聊成员已全部删除，无法继续发言", 409)
        wanted = (speaker_member or {}).get("cardId")
        speaker_card = next((item for item in cards if item.id == wanted), cards[0])
    else:
        speaker_card = card
    settings = dict(speaker_card.prompt_settings or {})
    try:
        profile = default_profile(db, owner)
    except Problem:
        if require_profile:
            raise
        profile = None
    preset = preset if preset is not None else service.preset_for_chat(db, owner, chat)
    persona = personas.get_persona(db, owner, persona_id) if persona_id else None
    persona_view = {"name": persona.name, "description": persona.description} if persona else None
    chat_sampling = dict(chat.sampling or {}) if chat is not None else {}
    if profile is None:
        limit = int(chat_sampling.get("maxTokens") or getattr(preset, "max_tokens", None)
                    or settings.get("maxTokens") or 1024)
    else:
        limit = stream.output_limit_for(profile, settings, preset, chat_sampling)
    sampling = stream.sampling_for(settings, preset, chat_sampling)
    names = service.speaker_name_map(chat) if group else {}
    history = [_history_item(row, names) for row in history_rows]
    char_name = speaker_card.name
    if not group:
        history = [{"role": item["role"], "text": item["text"]} for item in history]
    world_info = (service.group_world_info(db, cards) if group
                  else service.world_info_rows(db, speaker_card))
    builder = prompt_builder.build_group if group else prompt_builder.build
    build_kwargs = dict(history=history, message=message,
                        user_name=persona_view["name"] if persona_view else "User",
                        persona=persona_view, settings=settings, profile=profile,
                        output_limit=limit, world_info=world_info, preset=preset)
    if group:
        build_kwargs["speaker"] = speaker_card
        built = builder(cards, chat, **build_kwargs)
    else:
        built = builder(speaker_card, chat if chat is not None else SimpleNamespace(id=""),
                        **build_kwargs)
    return {"built": built, "limit": limit, "profile": profile, "sampling": sampling,
            "preset": preset, "persona": persona_view, "speaker": speaker_member,
            "speaker_card": speaker_card, "char_name": char_name}


def _reply_context(db, owner, chat, card, history_rows, message, persona_id, speaker=None):
    context = _build_prompt(db, owner, card, chat, history_rows, message, persona_id,
                            speaker_member=speaker)
    built = context["built"]
    return (built["systemPrompt"], built["data"], context["limit"],
            stream.profile_view(context["profile"]), context["profile"].model_id,
            context["sampling"], context["preset"], context["speaker"])


def _prompt_view(card):
    settings = dict(PROMPT_DEFAULTS)
    settings.update(card.prompt_settings or {})
    return {"systemPrompt": card.system_prompt or "", **settings}


GROUP_MIN_MEMBERS = 2
GROUP_MAX_MEMBERS = 5


def _group_members(db, owner, card_ids):
    """Validate + dedupe group members (order preserved) -> (member dicts, cards)."""
    seen, ids = set(), []
    for raw in card_ids or []:
        card_id = str(raw or "").strip()
        if card_id and card_id not in seen:
            seen.add(card_id)
            ids.append(card_id)
    if len(ids) < GROUP_MIN_MEMBERS or len(ids) > GROUP_MAX_MEMBERS:
        raise Problem("GROUP_MEMBERS_INVALID", "群聊需要 2 至 5 位角色", 422)
    cards = [service.card_or_404(db, owner, card_id) for card_id in ids]
    return [service.member_entry(card, index) for index, card in enumerate(cards)], cards


def _chat_speaker(db, owner, chat, card_id=None):
    if (chat.mode or "single") != "group":
        return None
    members = service.alive_members(db, owner, chat)
    if card_id:
        for member in members:
            if member.get("cardId") == card_id:
                return member
    return service.group_speaker(db, owner, chat)


def _history_item(row, names):
    item = {"role": row.role, "text": service.swipe_content(row.swipes, row.active_swipe)}
    speaker = getattr(row, "speaker_card_id", None)
    if row.role == "assistant" and speaker:
        item["speaker"] = names.get(speaker, "")
    return item


# ---------------------------------------------------------------- characters
@router.get("/characters")
def list_characters(q: str = Query(default="", max_length=100),
                    sort: str = Query(default="recent"),
                    user=Depends(current_user), db=Depends(get_db)):
    # Bundled preset cards are seeded lazily so the card library is never empty
    # for a fresh account; failures are logged inside and never block the list.
    presets.ensure_preset_cards(db, user.id)
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
    preset_id = service.get_preset(db, user.id, payload.presetId).id if payload.presetId else None
    greetings = [str(item).strip() for item in (card.alternate_greetings or []) if str(item).strip()]
    if payload.greetingId is None:
        greeting = (card.first_message or "").strip()
    else:
        if payload.greetingId >= len(greetings):
            raise Problem("GREETING_INVALID", "这个开场白不存在", 422)
        greeting = greetings[payload.greetingId]
    chat = TavernChat(id=service.uid(), owner_id=user.id, card_id=card.id,
                      preset_id=preset_id, sampling=_clean_sampling(payload.sampling),
                      title=_default_chat_title(db, user.id, card, payload.title), model_name="",
                      updated_at=now())
    db.add(chat)
    db.flush()
    if greeting:
        service.add_message(db, chat, user.id, "assistant", greeting, "")
    db.commit()
    return {"chat": service.chat_out(chat)}


@router.post("/chats", status_code=201)
def create_group_chat(payload: ChatCreate, user=Depends(current_user), db=Depends(get_db)):
    """Create a group chat (2-5 cards) or a single chat addressed by ``cardId``."""
    if payload.cardIds:
        members, cards = _group_members(db, user.id, payload.cardIds)
        preset_id = service.get_preset(db, user.id, payload.presetId).id if payload.presetId else None
        title = (payload.title or "、".join(card.name for card in cards))[:160]
        chat = TavernChat(id=service.uid(), owner_id=user.id, card_id=None,
                          mode="group", members=members, preset_id=preset_id,
                          sampling=_clean_sampling(payload.sampling), title=title,
                          model_name="", updated_at=now())
        db.add(chat)
        db.commit()
        return {"chat": service.chat_out(chat)}
    if payload.cardId:
        return create_chat(payload.cardId, payload, user=user, db=db)
    raise Problem("INVALID_REQUEST", "创建对话需要 cardId 或 cardIds", 422)


@router.get("/chats")
def list_chat_history(limit: int = Query(default=20, ge=1, le=100),
                      offset: int = Query(default=0, ge=0),
                      user=Depends(current_user), db=Depends(get_db)):
    rows = service.list_chats(db, user.id, limit=limit, offset=offset)
    return {"items": service.chats_out(db, rows), "limit": limit, "offset": offset}


def _chat_view(db, owner, chat):
    """Chat payload shared by GET detail and PATCH (members incl. deleted flags)."""
    group = (chat.mode or "single") == "group"
    cards = service.chat_cards(db, owner, chat)
    return {**service.chat_out(chat),
            "members": service.member_views(db, owner, chat) if group else [],
            "aliveMemberCount": len(cards),
            "card": service.card_summary(cards[0]) if cards else None,
            "cards": [service.card_summary(card) for card in cards]}


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, before: int | None = Query(default=None, ge=1),
             limit: int = Query(default=200, ge=1, le=500),
             user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id)
    names = service.speaker_name_map(chat)
    all_rows = service.list_messages(db, chat)
    latest = all_rows[-1].sequence if all_rows else 0
    candidates = [row for row in all_rows if before is None or row.sequence < before]
    has_more = len(candidates) > limit
    page = candidates[-limit:] if has_more else candidates
    return {"chat": _chat_view(db, user.id, chat),
            "messages": [service.message_out(row, names) for row in page],
            "hasMore": has_more, "latestSequence": latest}


@router.get("/chats/{chat_id}/export")
def export_chat(chat_id: str, format: str = Query(default="jsonl"),
                user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id)
    group = (chat.mode or "single") == "group"
    cards = service.chat_cards(db, user.id, chat)
    members = service.member_views(db, user.id, chat) if group else []
    names = service.speaker_name_map(chat)
    rows = service.list_messages(db, chat)
    fallback_name = (cards[0].name if cards else "") or service.DELETED_MEMBER_NAME
    export_title = chat.title or fallback_name
    card_payloads = [service.card_summary(card) for card in cards]
    chat_payload = {**service.chat_out(chat), "members": members,
                    "aliveMemberCount": len(cards),
                    "card": card_payloads[0] if card_payloads else None,
                    "cards": card_payloads}
    if format == "json":
        document = {"chat": chat_payload,
                    "card": card_payloads[0] if card_payloads else None,
                    "cards": card_payloads,
                    "messages": [service.message_out(row, names) for row in rows]}
        return Response(json.dumps(document, ensure_ascii=False), media_type="application/json",
                        headers=exporting.filename_header(f"{export_title}-{_stamp()}", ".json"))
    if format == "jsonl":
        lines = []
        for row in rows:
            swipes = list(row.swipes or [])
            speaker = names.get(row.speaker_card_id) if row.speaker_card_id else ""
            payload = {
                "user_name": "User",
                "character_name": speaker or (cards[0].name if cards else service.DELETED_MEMBER_NAME),
                "create_date": service.iso(row.updated_at or row.created_at),
                "mes": service.swipe_content(row.swipes, row.active_swipe),
                "is_user": row.role == "user",
                "is_name": True,
            }
            if speaker and row.role == "assistant":
                payload["speaker_name"] = speaker
            if len(swipes) > 1:
                payload["swipes"] = [str((item or {}).get("content", "")) for item in swipes]
                payload["swipe_id"] = int(row.active_swipe or 0)
            lines.append(json.dumps(payload, ensure_ascii=False))
        body = "\n".join(lines) + ("\n" if lines else "")
        return Response(body, media_type="text/plain; charset=utf-8",
                        headers=exporting.filename_header(f"{export_title}-{_stamp()}", ".jsonl"))
    raise Problem("INVALID_REQUEST", "导出格式仅支持 jsonl 或 json", 422)


@router.post("/characters/{card_id}/chats/imports", status_code=201)
async def import_chat(card_id: str, file: UploadFile = File(...),
                      user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    raw = await file.read()
    if len(raw) > IMPORT_MAX_BYTES:
        raise Problem("CHAT_IMPORT_TOO_LARGE", "聊天文件超过 32 MiB", 422)
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise Problem("CHAT_IMPORT_EMPTY", "文件里没有可以导入的聊天记录", 422)
    if len(lines) > IMPORT_MAX_LINES:
        raise Problem("CHAT_IMPORT_TOO_LARGE", "聊天记录超过 20000 行", 422)
    title = (file.filename or "").rsplit(".", 1)[0].strip()
    chat = TavernChat(id=service.uid(), owner_id=user.id, card_id=card.id,
                      title=(title or _default_chat_title(db, user.id, card, None))[:160],
                      model_name="", updated_at=now())
    db.add(chat)
    db.flush()
    imported, skipped, sequence = 0, 0, 0
    for line in lines:
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
            mes = payload.get("mes")
            if not isinstance(mes, str) or not mes.strip():
                raise ValueError("missing mes")
            role = "user" if payload.get("is_user") else "assistant"
            swipes = payload.get("swipes")
            if isinstance(swipes, list) and swipes:
                entries = []
                for item in swipes:
                    content = str(item).strip() if not isinstance(item, dict) else str(item.get("content") or "").strip()
                    if content:
                        entries.append({"content": content, "model": "", "createdAt": None})
                if not entries:
                    entries = [{"content": mes.strip(), "model": "", "createdAt": None}]
            else:
                entries = [{"content": mes.strip(), "model": "", "createdAt": None}]
            active = payload.get("swipe_id")
            active = int(active) if isinstance(active, int) else 0
            active = max(0, min(active, len(entries) - 1))
            sequence += 1
            db.add(TavernMessage(id=service.uid(), owner_id=user.id, chat_id=chat.id,
                                 role=role, sequence=sequence, swipes=entries,
                                 active_swipe=active, updated_at=now()))
            imported += 1
        except (ValueError, TypeError, json.JSONDecodeError):
            skipped += 1
    if imported == 0:
        db.rollback()
        raise Problem("CHAT_IMPORT_EMPTY", "所有行都无法解析，未导入任何消息", 422)
    service.touch_chat(db, chat, "")
    db.commit()
    return {"chat": service.chat_out(chat), "imported": imported, "skipped": skipped}


@router.patch("/chats/{chat_id}")
def patch_chat(chat_id: str, payload: ChatPatch, user=Depends(current_user),
               db=Depends(get_db)):
    fields = payload.model_fields_set
    if not fields:
        raise Problem("INVALID_REQUEST", "没有需要更新的字段", 422)
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    if "cardIds" in fields:
        if (chat.mode or "single") != "group":
            raise Problem("CHAT_MODE_INVALID", "单聊不支持修改成员", 422)
        ids = list(dict.fromkeys(str(card_id) for card_id in (payload.cardIds or []) if card_id))
        if not GROUP_MIN_MEMBERS <= len(ids) <= GROUP_MAX_MEMBERS:
            raise Problem("GROUP_MEMBERS_INVALID", "群聊需要 2 至 5 位角色", 422)
        cards = [service.card_or_404(db, user.id, card_id) for card_id in ids]
        chat.members = [service.member_entry(card, index) for index, card in enumerate(cards)]
    if "title" in fields and payload.title:
        chat.title = payload.title.strip()
    if "presetId" in fields:
        if payload.presetId:
            service.get_preset(db, user.id, payload.presetId)
        chat.preset_id = payload.presetId or None
    if "sampling" in fields:
        chat.sampling = _clean_sampling(payload.sampling)
    chat.updated_at = now()
    db.commit()
    return {"chat": _chat_view(db, user.id, chat)}


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
    group = (chat.mode or "single") == "group"
    if group:
        speaker = service.group_speaker(db, user.id, chat)
        if speaker is None:
            raise Problem("CHAT_NO_MEMBERS", "群聊成员已全部删除，无法继续发言", 409)
        card = service.chat_card(db, user.id, chat)
        if card is None:
            raise Problem("CHAT_NO_MEMBERS", "群聊成员已全部删除，无法继续发言", 409)
    else:
        speaker = None
        card = service.card_or_404(db, user.id, chat.card_id)
    text = payload.message.strip()
    if not text:
        raise Problem("MESSAGE_REQUIRED", "消息不能为空", 422)
    names = service.speaker_name_map(chat) if group else {}
    system_prompt, data, limit, profile, model_name, sampling, preset, speaker = _reply_context(
        db, user.id, chat, card, _history(db, chat), text, payload.personaId, speaker)
    user_row = service.add_message(db, chat, user.id, "user", text, "")
    db.commit()
    initial = {"type": "message", "userMessage": service.message_out(user_row),
               "assistantSequence": user_row.sequence + 1}
    if speaker:
        initial["speaker"] = {"cardId": speaker.get("cardId"),
                              "name": speaker.get("name", "")}
    return StreamingResponse(
        stream.reply_events(owner=user.id, chat_id=chat.id, mode="message",
                            system_prompt=system_prompt, data=data, output_limit=limit,
                            profile=profile, model_name=model_name, initial=initial,
                            sampling=sampling,
                            speaker_card_id=(speaker or {}).get("cardId"),
                            speaker_names=names),
        media_type=stream.SSE_MEDIA_TYPE, headers=stream.SSE_HEADERS)


@router.post("/chats/{chat_id}/regenerate")
def regenerate(chat_id: str, payload: RegenerateInput | None = None,
               user=Depends(current_user), db=Depends(get_db)):
    chat = service.chat_or_404(db, user.id, chat_id, lock=True)
    group = (chat.mode or "single") == "group"
    target = service.last_assistant(db, chat)
    if target is None:
        raise Problem("NOTHING_TO_REGENERATE", "没有可以重新生成的回复", 409)
    speaker, names = None, {}
    if group:
        speaker = _chat_speaker(db, user.id, chat, target.speaker_card_id)
        if speaker is None:
            raise Problem("CHAT_NO_MEMBERS", "群聊成员已全部删除，无法继续发言", 409)
        names = service.speaker_name_map(chat)
        card = service.chat_card(db, user.id, chat)
        if card is None:
            raise Problem("CHAT_NO_MEMBERS", "群聊成员已全部删除，无法继续发言", 409)
    else:
        card = service.card_or_404(db, user.id, chat.card_id)
    history = [row for row in service.list_messages(db, chat) if row.sequence < target.sequence]
    persona_id = payload.personaId if payload else None
    message = next((service.swipe_content(row.swipes, row.active_swipe)
                    for row in reversed(history) if row.role == "user"), "")
    system_prompt, data, limit, profile, model_name, sampling, preset, speaker = _reply_context(
        db, user.id, chat, card, history, message, persona_id, speaker)
    initial = {"type": "message", "messageId": target.id,
               "activeSwipe": target.active_swipe}
    if speaker:
        initial["speaker"] = {"cardId": speaker.get("cardId"),
                              "name": speaker.get("name", "")}
    return StreamingResponse(
        stream.reply_events(owner=user.id, chat_id=chat.id, mode="regenerate",
                            system_prompt=system_prompt, data=data, output_limit=limit,
                            profile=profile, model_name=model_name, target_id=target.id,
                            sampling=sampling, initial=initial, speaker_names=names,
                            speaker_card_id=(speaker or {}).get("cardId")),
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
    names = service.speaker_name_map(chat)
    return {"message": service.message_out(row, names)}


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


def _last_user_text(rows):
    for row in reversed(rows):
        if row.role == "user":
            text = service.swipe_content(row.swipes, row.active_swipe).strip()
            if text:
                return text
    return ""


@router.post("/characters/{card_id}/world-info/preview")
def preview_world_info(card_id: str, payload: PreviewInput, user=Depends(current_user),
                       db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    chat = service.chat_or_404(db, user.id, payload.chatId) if payload.chatId else None
    if chat is not None and chat.card_id != card.id:
        raise Problem("RESOURCE_NOT_FOUND", "会话不存在", 404)
    history_rows = service.list_messages(db, chat) if chat is not None else []
    message = payload.message.strip() or _last_user_text(history_rows)
    context = _build_prompt(db, user.id, card, chat, history_rows, message, None,
                            require_profile=False)
    built = context["built"]
    view = _prompt_view(card)
    return {"matched": built["matched"], "injection": built["worldInjection"],
            "reasons": [{"id": item["id"], "reason": item["reason"]}
                        for item in built["matched"]],
            "worldInfoPosition": view.get("worldInfoPosition", "before_char"),
            "sections": built["sections"], "systemPrompt": built["systemPrompt"]}


# ------------------------------------------------------------------- prompt
@router.get("/characters/{card_id}/prompt")
def get_prompt(card_id: str, chatId: str | None = Query(default=None, max_length=32),
               user=Depends(current_user), db=Depends(get_db)):
    card = service.card_or_404(db, user.id, card_id)
    chat = service.chat_or_404(db, user.id, chatId) if chatId else None
    if chat is not None and chat.card_id != card.id:
        raise Problem("RESOURCE_NOT_FOUND", "会话不存在", 404)
    history_rows = service.list_messages(db, chat) if chat is not None else []
    context = _build_prompt(db, user.id, card, chat, history_rows,
                            _last_user_text(history_rows), None, require_profile=False)
    built = context["built"]
    preset = context["preset"]
    view = _prompt_view(card)
    return {"prompt": view, "presetId": preset.id, "presetName": preset.name,
            "system": view.get("system", ""), "main": view.get("mainPrompt", ""),
            "jailbreakPrompt": view.get("jailbreakPrompt", ""),
            "postHistory": view.get("postHistory", ""),
            "worldInfoPosition": view.get("worldInfoPosition", "before_char"),
            "systemPrompt": built["systemPrompt"], "sections": built["sections"],
            "segments": built["sections"],
            "matched": built["matched"], "worldInjection": built["worldInjection"]}


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
    mapping = {"system": "system", "main": "mainPrompt", "jailbreakPrompt": "jailbreakPrompt",
               "postHistory": "postHistory", "worldInfoPosition": "worldInfoPosition"}
    for api_name, setting_key in mapping.items():
        if api_name in fields:
            settings[setting_key] = (getattr(payload, api_name) or "").strip()
    card.prompt_settings = settings
    card.updated_at = now()
    db.commit()
    return {"prompt": _prompt_view(card)}


# ------------------------------------------------------------------ presets
@router.get("/presets")
def list_presets(user=Depends(current_user), db=Depends(get_db)):
    rows = service.list_presets(db, user.id)
    return {"items": [service.preset_out(row) for row in rows]}


@router.post("/presets", status_code=201)
def create_preset(payload: PresetInput, user=Depends(current_user), db=Depends(get_db)):
    row = TavernPreset(id=service.uid(), owner_id=user.id, name=payload.name.strip(),
                       is_default=False, system=payload.system.strip(),
                       main=payload.main.strip(), jailbreak=payload.jailbreak.strip(),
                       post_history=payload.postHistory.strip(),
                       temperature=payload.temperature, top_p=payload.topP,
                       max_tokens=payload.maxTokens, stop=_clean_stop(payload.stop),
                       updated_at=now())
    db.add(row)
    db.commit()
    return {"preset": service.preset_out(row)}


@router.patch("/presets/{preset_id}")
def patch_preset(preset_id: str, payload: PresetUpdate, user=Depends(current_user),
                 db=Depends(get_db)):
    row = service.get_preset(db, user.id, preset_id)
    fields = payload.model_fields_set
    if not fields:
        raise Problem("INVALID_REQUEST", "没有需要更新的字段", 422)
    mapping = {"name": "name", "system": "system", "main": "main", "jailbreak": "jailbreak",
               "postHistory": "post_history", "temperature": "temperature",
               "topP": "top_p", "maxTokens": "max_tokens", "stop": "stop"}
    for api_name, column in mapping.items():
        if api_name not in fields:
            continue
        value = getattr(payload, api_name)
        if api_name in ("name", "system", "main", "jailbreak", "postHistory") and value is not None:
            value = value.strip()
        if api_name == "stop":
            value = _clean_stop(value)
        setattr(row, column, value)
    if "isDefault" in fields and payload.isDefault is not None:
        if payload.isDefault:
            db.execute(update(TavernPreset).where(
                TavernPreset.owner_id == user.id, TavernPreset.id != row.id).values(is_default=False))
            row.is_default = True
        else:
            row.is_default = False
    row.updated_at = now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Problem("PRESET_CONFLICT", "默认预设切换冲突，请重试", 409, retryable=True)
    return {"preset": service.preset_out(row)}


@router.delete("/presets/{preset_id}", status_code=204)
def drop_preset(preset_id: str, user=Depends(current_user), db=Depends(get_db)):
    service.delete_preset(db, user.id, preset_id)
    return Response(status_code=204)
