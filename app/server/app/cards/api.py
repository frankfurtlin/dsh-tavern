"""Character card HTTP API (import, list, avatar, delete, card chats)."""
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse

from ..db import get_db
from ..errors import Problem
from ..security import current_user
from ..world.api import available
from . import parse, service

router = APIRouter(prefix="/character-cards", dependencies=[Depends(available)])


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


@router.get("/{card_id}/avatar")
def read_avatar(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    return FileResponse(service.avatar_file(card, user.id), media_type="image/png")


@router.delete("/{card_id}", status_code=204)
def delete_card(card_id: str, user=Depends(current_user), db=Depends(get_db)):
    card = service.get_card(db, user.id, card_id)
    service.delete_card(db, card)
    return Response(status_code=204)


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
