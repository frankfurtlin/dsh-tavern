"""Streaming private backup routes, independent of novel's 50 MiB upload."""
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import select, text

from ..db import get_db
from ..errors import Problem
from ..models import now
from ..schemas import Strict
from ..security import current_user
from .models import PortableOperation
from .service import PROTOCOL, cleanup_expired, export_backup, owned_operation, preview_backup, problem, public, restore_preview, stage_path
from .errors import ArchiveError

router=APIRouter()

# Public reading exports share bounded owner stages and cleanup, but never the
# private backup manifest or hidden world records.
from .reading_export_api import router as reading_export_router
router.include_router(reading_export_router)


class RestoreConfirmation(Strict):
    confirmed:bool=Field(strict=True)
    expectedVersion:int=Field(strict=True,ge=1)
    targetOwnerId:str=Field(pattern=r"^[a-f0-9]{32}$")


@router.get("/backups/capabilities")
def capabilities(user=Depends(current_user),db=Depends(get_db)):
    ready=db.scalar(text("SELECT to_regclass('portable_operations')")) is not None
    return {"protocol":PROTOCOL,"ready":ready,"formatVersion":1,"schemaVersion":1,
            "maxUncompressedBytes":2*1024**3,"maxEntries":20000,"maxImageBytes":10*1024**2,
            "encrypted":True,"privateContainsHiddenWorldData":True,"defaultRestore":"new-copy"}


@router.post("/stories/{story_id}/backup",status_code=201)
def export(story_id:str,request:Request,password:str=Form(...),includeSource:bool=Form(False),user=Depends(current_user),db=Depends(get_db)):
    try:return export_backup(db,user.id,story_id,request.headers.get("idempotency-key"),password,includeSource)
    except ArchiveError as error:raise problem(error)


@router.post("/backups/prepare",status_code=201)
def prepare(request:Request,file:UploadFile=File(...),password:str=Form(...),user=Depends(current_user),db=Depends(get_db)):
    try:return preview_backup(db,user.id,request.headers.get("idempotency-key"),file.file,password)
    except ArchiveError as error:raise problem(error)


@router.get("/backups/requests")
def request_receipt(kind:str,requestId:str,user=Depends(current_user),db=Depends(get_db)):
    if kind not in {"restore","export"} or not requestId or len(requestId)>160:raise Problem("INVALID_REQUEST","备份查询字段无效")
    operation=db.scalar(select(PortableOperation).where(PortableOperation.owner_id==user.id,PortableOperation.kind==kind,PortableOperation.request_key==requestId))
    return {"found":operation is not None,"operation":public(operation) if operation else None}


@router.get("/backups/{operation_id}")
def status(operation_id:str,user=Depends(current_user),db=Depends(get_db)):
    return public(owned_operation(db,user.id,operation_id))


@router.post("/backups/{operation_id}/restore")
def restore(operation_id:str,data:RestoreConfirmation,request:Request,user=Depends(current_user)):
    return restore_preview(user.id,operation_id,request.headers.get("idempotency-key"),data.expectedVersion,data.confirmed,data.targetOwnerId)


@router.get("/backups/{operation_id}/download")
def download(operation_id:str,user=Depends(current_user),db=Depends(get_db)):
    operation=owned_operation(db,user.id,operation_id)
    if operation.kind!="export" or operation.status!="ready" or operation.expires_at<=now():
        raise Problem("BACKUP_DOWNLOAD_EXPIRED","备份下载已失效，请重新导出",409)
    try:path=stage_path(user.id,operation.stage_name)/"private.srbackup"
    except ArchiveError as error:raise problem(error)
    if not path.is_file() or path.is_symlink():raise Problem("RESOURCE_NOT_FOUND","备份文件不可用",404)
    return FileResponse(path,media_type="application/octet-stream",filename="story-"+operation.id+".srbackup")
