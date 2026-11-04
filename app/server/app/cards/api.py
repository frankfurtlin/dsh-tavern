"""Character card HTTP API (import, list, edit, avatar, delete, card chats),
plus the card-space endpoints for lorebooks, presets and regex scripts."""
from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from ..db import get_db
from ..models import now
from ..errors import Problem
from ..schemas import Strict
from ..security import current_user
from ..world.api import available
from . import groups, memory, parse, service, space

router = APIRouter(prefix="/character-cards", dependencies=[Depends(available)])
presets = APIRouter(prefix="/presets", dependencies=[Depends(available)])
regex_scripts = APIRouter(prefix="/regex-scripts", dependencies=[Depends(available)])
group_chats = APIRouter(prefix="/card-group-chats", dependencies=[Depends(available)])


class CardUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=20000)
    personality: str | None = Field(default=None, max_length=20000)
    scenario: str | None = Field(default=None, max_length=20000)
    firstMessage: str | None = Field(default=None, max_length=20000)
    systemPrompt: str | None = Field(default=None, max_length=20000)
    postHistoryInstructions: str | None = Field(default=None, max_length=20000)
    tags: list[str] | None = Field(default=None, max_length=200)
    presetId: str | None = Field(default=None, max_length=32)
    companion: bool | None = None


class LoreInput(Strict):
    name: str | None = Field(default=None, max_length=160)
    keys: list[str] = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=8000)
    enabled: bool = True
    order: int = 100


class LoreUpdate(Strict):
    name: str | None = Field(default=None, max_length=160)
    keys: list[str] | None = Field(default=None, min_length=1, max_length=20)
    content: str | None = Field(default=None, max_length=8000)
    enabled: bool | None = None
    order: int | None = None


class PresetInput(Strict):
    name: str = Field(min_length=1, max_length=80)
    systemSuffix: str = Field(default="", max_length=4000)
    enabled: bool = True


class PresetUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    systemSuffix: str | None = Field(default=None, max_length=4000)
    enabled: bool | None = None


class RegexInput(Strict):
    name: str = Field(min_length=1, max_length=80)
    pattern: str = Field(min_length=1, max_length=500)
    replacement: str = Field(default="", max_length=2000)
    enabled: bool = True
    order: int = 0


class RegexUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    replacement: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    order: int | None = None


class PreviewInput(Strict):
    message: str = Field(default="", max_length=20000)


@router.post("/imports", status_code=201)
def import_card(file: UploadFile = File(...), user=Depends(current_user), db=Depends(get_db)):
    data = file.file.read()
    if not data:
        raise Problem("CARD_INVALID", "角色卡文件为空", 422)
    parsed = parse.parse_card(data, file.filename or "")
    png = data if data.startswith(parse.PNG_MAGIC) else None
    card = service.import_card(db, user.id, parsed, png)
    return {"card": service.card_out(card)}


@router.get("")
def list_cards(q: str = Query("", max_length=100), user=Depends(current_user), db=Depends(get_db)):
    return {"items": [service.card_out(card) for card in service.list_cards(db, user.id, q)]}


@router.get("/{card_id}")
def read_card(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    return service.card_out(service.get_card(db, user.id, card_id))


@router.patch("/{card_id}")
def patch_card(card_id: str, payload: CardUpdate, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    values = {field: getattr(payload, field) for field in (
        "name", "description", "personality", "scenario", "firstMessage",
        "systemPrompt", "postHistoryInstructions", "tags")}
    if "presetId" in payload.model_fields_set:
        values["presetId"] = payload.presetId
    if "companion" in payload.model_fields_set:
        values["companion"] = payload.companion
    card = service.update_card(db, user.id, card, values)
    return {"card": service.card_out(card)}


@router.get("/{card_id}/avatar")
def read_avatar(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    return FileResponse(service.avatar_file(card, user.id), media_type="image/png")


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    service.delete_card(db, card)
    return Response(status_code=204)


@router.get("/{card_id}/lore")
def list_lore(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    service.get_card(db, user.id, card_id)
    return {"items": [space.lore_out(row) for row in space.list_lore(db, user.id, card_id)]}


@router.post("/{card_id}/lore", status_code=201)
def create_lore(card_id: str, payload: LoreInput, user=Depends(current_user), db=Depends(get_db)):
    service.get_card(db, user.id, card_id)
    row = space.create_lore(db, user.id, card_id, payload.model_dump())
    return {"lore": space.lore_out(row)}


@router.patch("/{card_id}/lore/{lore_id}")
def patch_lore(card_id: str, lore_id: str, payload: LoreUpdate, user=Depends(current_user), db=Depends(get_db)):
    service.get_card(db, user.id, card_id)
    row = space.update_lore(db, user.id, card_id, lore_id, payload.model_dump(exclude_unset=False))
    return {"lore": space.lore_out(row)}


@router.delete("/{card_id}/lore/{lore_id}", status_code=204)
def delete_lore(card_id: str, lore_id: str, user=Depends(current_user), db=Depends(get_db)):
    service.get_card(db, user.id, card_id)
    space.delete_lore(db, user.id, card_id, lore_id)
    return Response(status_code=204)


@router.post("/{card_id}/context-preview")
def context_preview(card_id: str, payload: PreviewInput, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    return space.preview(db, user.id, card, payload.message)


@router.post("/{card_id}/chats", status_code=201)
def create_card_chat(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    try:
        session = service.open_chat(db, user.id, card)
        db.commit()
    except Problem:
        db.rollback()
        raise
    return {"sessionId": session.id, "cardId": card.id}


@presets.get("")
def list_presets(user=Depends(current_user), db=Depends(get_db)):
    return {"items": [space.preset_out(row) for row in space.list_presets(db, user.id)]}


@presets.post("", status_code=201)
def create_preset(payload: PresetInput, user=Depends(current_user), db=Depends(get_db)):
    return {"preset": space.preset_out(space.create_preset(db, user.id, payload.model_dump()))}


@presets.patch("/{preset_id}")
def patch_preset(preset_id: str, payload: PresetUpdate, user=Depends(current_user), db=Depends(get_db)):
    row = space.update_preset(db, user.id, preset_id, payload.model_dump(exclude_unset=False))
    return {"preset": space.preset_out(row)}


@presets.delete("/{preset_id}", status_code=204)
def delete_preset(preset_id: str, user=Depends(current_user), db=Depends(get_db)):
    space.delete_preset(db, user.id, preset_id)
    return Response(status_code=204)


@regex_scripts.get("")
def list_regex(user=Depends(current_user), db=Depends(get_db)):
    return {"items": [space.regex_out(row) for row in space.list_regex(db, user.id)]}


@regex_scripts.post("", status_code=201)
def create_regex(payload: RegexInput, user=Depends(current_user), db=Depends(get_db)):
    return {"script": space.regex_out(space.create_regex(db, user.id, payload.model_dump()))}


@regex_scripts.patch("/{script_id}")
def patch_regex(script_id: str, payload: RegexUpdate, user=Depends(current_user), db=Depends(get_db)):
    row = space.update_regex(db, user.id, script_id, payload.model_dump(exclude_unset=False))
    return {"script": space.regex_out(row)}


@regex_scripts.delete("/{script_id}", status_code=204)
def delete_regex(script_id: str, user=Depends(current_user), db=Depends(get_db)):
    space.delete_regex(db, user.id, script_id)
    return Response(status_code=204)


class GroupChatInput(Strict):
    cardIds: list[str] = Field(min_length=1, max_length=10)
    title: str | None = Field(default=None, max_length=80)


class GroupSendInput(Strict):
    message: str = Field(min_length=1, max_length=4000)
    turnId: str = Field(min_length=1, max_length=100)


@group_chats.get("")
def list_group_chats(user=Depends(current_user), db=Depends(get_db)):
    return {"items": groups.list_chats(db, user.id)}


@group_chats.post("", status_code=201)
def create_group_chat(payload: GroupChatInput, user=Depends(current_user), db=Depends(get_db)):
    return {"chat": groups.create_chat(db, user.id, payload.cardIds, payload.title)}


@group_chats.get("/{chat_id}")
def read_group_chat(chat_id: str, user=Depends(current_user), db=Depends(get_db)):
    return groups.chat_detail(db, user.id, chat_id)


@group_chats.delete("/{chat_id}", status_code=204)
def remove_group_chat(chat_id: str, user=Depends(current_user), db=Depends(get_db)):
    groups.delete_chat(db, user.id, chat_id)
    return Response(status_code=204)


@group_chats.post("/{chat_id}/messages", status_code=202)
def send_group_message(chat_id: str, payload: GroupSendInput, request: Request,
                       user=Depends(current_user), db=Depends(get_db)):
    return groups.send_message(db, user.id, request, chat_id, payload.message, payload.turnId)


class MemoryInput(Strict):
    kind: str = Field(default="fact", pattern="^(fact|preference|relationship|event)$")
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=2000)
    salience: int = Field(default=50, ge=1, le=100)
    enabled: bool = True


class MemoryUpdate(Strict):
    kind: str | None = Field(default=None, pattern="^(fact|preference|relationship|event)$")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    salience: int | None = Field(default=None, ge=1, le=100)
    enabled: bool | None = None


class CompanionInput(Strict):
    companion: bool


@router.get("/{card_id}/memories")
def list_card_memories(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    return {"items": [memory.memory_out(row) for row in memory.list_memories(db, user.id, card.id)]}


@router.post("/{card_id}/memories", status_code=201)
def create_card_memory(card_id: str, payload: MemoryInput, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    row = memory.create_memory(db, user.id, card.id, payload.model_dump())
    return {"memory": memory.memory_out(row)}


@router.patch("/{card_id}/memories/{memory_id}")
def patch_card_memory(card_id: str, memory_id: str, payload: MemoryUpdate,
                      user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    row = memory.update_memory(db, user.id, card.id, memory_id, payload.model_dump(exclude_unset=True))
    return {"memory": memory.memory_out(row)}


@router.delete("/{card_id}/memories/{memory_id}", status_code=204)
def delete_card_memory(card_id: str, memory_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    memory.delete_memory(db, user.id, card.id, memory_id)
    return Response(status_code=204)


@router.post("/{card_id}/memories/extract")
def extract_card_memories(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    from ..story_flow import default_profile
    card = service.get_card(db, user.id, card_id)
    profile = default_profile(db, user.id)
    rows, skipped = memory.extract(db, user.id, card, profile)
    return {"added": [memory.memory_out(row) for row in rows], "skipped": skipped}


@router.post("/{card_id}/companion")
def set_card_companion(card_id: str, payload: CompanionInput, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    card.companion = bool(payload.companion)
    card.updated_at = now()
    db.commit()
    return {"card": service.card_out(card)}
