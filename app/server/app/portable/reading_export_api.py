"""Owner-authorized finite public TXT/EPUB jobs and pure receipt queries."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import select,text

from ..db import get_db
from ..errors import Problem
from ..security import current_user
from .models import PortableOperation
from .reading_export import PROTOCOL,MAX_BODY,ReadingExportInput, download_reading, export_public, export_reading
from .service import owned_operation

router = APIRouter()


@router.get("/exports/capabilities")
def capabilities(user=Depends(current_user),db=Depends(get_db)):
    return {"protocol":PROTOCOL,"ready":db.scalar(text("SELECT to_regclass('portable_operations')")) is not None,
            "formats":["txt","epub"],"defaultRange":"observed","selectedUnseenRequiresConfirmation":True,
            "maxBytes":MAX_BODY}


@router.post("/exports", status_code=201)
def create(data: ReadingExportInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return export_reading(db, user.id, request.headers.get("idempotency-key"), data)


@router.get("/exports/requests")
def receipt(requestId: str, user=Depends(current_user), db=Depends(get_db)):
    if not requestId or len(requestId) > 160:
        raise Problem("INVALID_REQUEST", "导出查询字段无效")
    row = db.scalar(select(PortableOperation).where(PortableOperation.owner_id == user.id,
        PortableOperation.kind == "reading_export", PortableOperation.request_key == requestId))
    return {"found": row is not None, "job": export_public(row) if row else None}


@router.get("/exports/{identifier}")
def status(identifier: str, user=Depends(current_user), db=Depends(get_db)):
    return export_public(owned_operation(db, user.id, identifier))


@router.get("/exports/{identifier}/download")
def download(identifier: str, user=Depends(current_user), db=Depends(get_db)):
    path, result = download_reading(db, user.id, identifier)
    return FileResponse(path, media_type="text/plain; charset=utf-8" if result["format"] == "txt" else "application/epub+zip",
                        filename=result["fileName"])
