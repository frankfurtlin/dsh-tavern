"""Reader personas ("我是谁") for character card chats: CRUD and prompt injection."""
from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select, update

from ..db import get_db
from ..errors import Problem
from ..models import now, uid
from ..schemas import Strict
from ..security import current_user
from ..world.api import available
from ..play_controls.models import WorldChatSession
from .models import TavernPersona

router = APIRouter(prefix="/tavern-personas", dependencies=[Depends(available)])

NAME_LIMIT = 80
DESCRIPTION_LIMIT = 800


def persona_out(row):
    return {"id": row.id, "name": row.name, "description": row.description,
            "createdAt": row.created_at.isoformat(), "updatedAt": row.updated_at.isoformat()}


def list_personas(db, owner):
    return list(db.scalars(select(TavernPersona).where(
        TavernPersona.owner_id == owner, TavernPersona.deleted_at.is_(None),
    ).order_by(TavernPersona.updated_at.desc(), TavernPersona.created_at.desc())))


def get_persona(db, owner, persona_id):
    row = db.scalar(select(TavernPersona).where(
        TavernPersona.id == persona_id, TavernPersona.owner_id == owner,
        TavernPersona.deleted_at.is_(None)))
    if row is None:
        raise Problem("PERSONA_NOT_FOUND", "这份人设不存在或已被删除", 404)
    return row


def _values(values):
    name = (values.get("name") or "").strip()
    description = (values.get("description") or "").strip()
    if not name:
        raise Problem("PERSONA_INVALID", "请填写人设名称", 422)
    return name[:NAME_LIMIT], description[:DESCRIPTION_LIMIT]


def create_persona(db, owner, values):
    name, description = _values(values)
    row = TavernPersona(id=uid(), owner_id=owner, name=name, description=description,
                        updated_at=now())
    db.add(row)
    db.commit()
    return row


def update_persona(db, owner, persona_id, values):
    row = get_persona(db, owner, persona_id)
    if values.get("name") is not None or values.get("description") is not None:
        name, description = _values({"name": values.get("name", row.name),
                                     "description": values.get("description", row.description)})
        row.name, row.description = name, description
        row.updated_at = now()
        db.commit()
    return row


def delete_persona(db, owner, persona_id):
    row = get_persona(db, owner, persona_id)
    row.deleted_at = now()
    db.execute(update(WorldChatSession).where(
        WorldChatSession.owner_id == owner, WorldChatSession.persona_id == persona_id,
    ).values(persona_id=None))
    db.commit()
    return row


def persona_for_chat(db, owner, chat_id):
    if not chat_id:
        return None
    session = db.get(WorldChatSession, chat_id)
    if session is None or session.owner_id != owner or not session.persona_id:
        return None
    return db.scalar(select(TavernPersona).where(
        TavernPersona.id == session.persona_id, TavernPersona.owner_id == owner,
        TavernPersona.deleted_at.is_(None)))


class PersonaInput(Strict):
    name: str = Field(min_length=1, max_length=NAME_LIMIT)
    description: str = Field(default="", max_length=DESCRIPTION_LIMIT)


class PersonaUpdate(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_LIMIT)
    description: str | None = Field(default=None, max_length=DESCRIPTION_LIMIT)


@router.get("")
def list_route(user=Depends(current_user), db=Depends(get_db)):
    return {"items": [persona_out(row) for row in list_personas(db, user.id)]}


@router.post("", status_code=201)
def create_route(payload: PersonaInput, user=Depends(current_user), db=Depends(get_db)):
    return {"persona": persona_out(create_persona(db, user.id, payload.model_dump()))}


@router.patch("/{persona_id}")
def update_route(persona_id: str, payload: PersonaUpdate, user=Depends(current_user), db=Depends(get_db)):
    return {"persona": persona_out(update_persona(db, user.id, persona_id, payload.model_dump(exclude_unset=True)))}


@router.delete("/{persona_id}", status_code=204)
def delete_route(persona_id: str, user=Depends(current_user), db=Depends(get_db)):
    from fastapi import Response
    delete_persona(db, user.id, persona_id)
    return Response(status_code=204)
