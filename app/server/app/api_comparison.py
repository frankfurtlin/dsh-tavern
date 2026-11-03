from typing import Literal
from fastapi import APIRouter, Depends, Header, Query
from pydantic import ValidationError
from .db import get_db
from .security import current_user
from .errors import Problem
from .comparison.common import exposure_ready
from .comparison.schemas import CompareInput, RevealInput, ID, PROTOCOL
from .comparison import service

router = APIRouter()


@router.get("/comparison/capabilities")
def capabilities(user=Depends(current_user), db=Depends(get_db)):
    return {"protocol": PROTOCOL, "ready": True, "revealReady": exposure_ready(db),
        "alignments": {"book": ["latest"], "world": ["latest", "same-time"]}, "maxPageSize": 20}


@router.get("/stories/{story_id}/compare")
def compare(story_id: str, kind: Literal["book", "world"],
    leftLineId: str = Query(pattern=ID), rightLineId: str = Query(pattern=ID),
    bookId: str | None = Query(default=None, pattern=ID), worldId: str | None = Query(default=None, pattern=ID),
    leftNodeId: str | None = Query(default=None, pattern=ID), rightNodeId: str | None = Query(default=None, pattern=ID),
    alignment: Literal["latest", "same-time"] = "latest", atMinute: int | None = Query(default=None, ge=0),
    leftOffset: int = Query(default=0, ge=0, le=10000), rightOffset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=10, ge=1, le=20), user=Depends(current_user), db=Depends(get_db)):
    try:
        data = CompareInput(kind=kind, leftLineId=leftLineId, rightLineId=rightLineId, bookId=bookId,
            worldId=worldId, leftNodeId=leftNodeId, rightNodeId=rightNodeId, alignment=alignment,
            atMinute=atMinute, leftOffset=leftOffset, rightOffset=rightOffset, limit=limit)
    except ValidationError:
        raise Problem("INVALID_REQUEST", "请选择一致的故事路径和对齐方式", 422)
    return service.compare(db, user.id, story_id, data)


@router.post("/stories/{story_id}/compare/reveal")
def reveal(story_id: str, data: RevealInput, key: str | None = Header(default=None, alias="Idempotency-Key"),
    user=Depends(current_user), db=Depends(get_db)):
    return service.reveal(db, user.id, story_id, key, data)


@router.get("/stories/{story_id}/compare/reveal/requests")
def reveal_status(story_id: str, requestId: str = Query(min_length=1, max_length=160),
    user=Depends(current_user), db=Depends(get_db)):
    return service.reveal_status(db, user.id, story_id, requestId)
