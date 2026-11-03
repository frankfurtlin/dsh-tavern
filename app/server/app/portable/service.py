"""Durable authorized preview/export receipts with private, bounded stages."""
from datetime import timedelta
import hashlib
from pathlib import Path
import re

from sqlalchemy import or_, select, text, tuple_

from ..api_stories import story_root
from ..config import settings
from ..db import SessionLocal
from ..domain import canonical, digest
from ..errors import Problem
from ..models import User, now, uid
from .errors import ArchiveError
from .format import MAX_CIPHERTEXT_BYTES
from .manifest import ArchiveLimits, encode_json, parse_json, validate_manifest
from .models import PortableOperation
from .package import PreparedArchive, _remove_owned_stage, prepare_backup, write_backup
from .restore import restore_domain
from .snapshot import build_domain_snapshot

PROTOCOL = "reader-portable-v1"
EXPIRY_MINUTES = 30
MESSAGES = {
    "BACKUP_PASSWORD_OR_INTEGRITY_INVALID": "密码不正确或备份已损坏，原文件已保留",
    "BACKUP_VERSION_UNSUPPORTED": "这个备份版本暂不支持，原文件已保留",
    "BACKUP_SIZE_LIMIT": "备份超过 2 GiB 解压配额",
    "BACKUP_REFERENCE_MISSING": "备份缺少必需的故事引用，未恢复任何内容",
    "BACKUP_FILE_HASH_MISMATCH": "备份文件校验失败，未恢复任何内容",
    "BACKUP_OWNER_NOT_ACTIVE": "当前账号不可恢复，请重新登录",
}


def problem(error):
    if isinstance(error, ArchiveError):
        return Problem(error.code, MESSAGES.get(error.code, "备份未通过完整校验，未恢复任何内容"), 422, error.details)
    return error


def directory(owner):
    if not re.fullmatch(r"[a-f0-9]{32}", owner): raise ArchiveError("BACKUP_OWNER_INVALID")
    tombstone = settings.storage_path / ".deleted" / owner
    if tombstone.exists():raise ArchiveError("BACKUP_OWNER_NOT_ACTIVE")
    parent = settings.storage_path / ".portable" / owner
    if parent.is_symlink() or not parent.resolve().is_relative_to(settings.storage_path.resolve()): raise ArchiveError("BACKUP_PATH_INVALID")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if tombstone.exists():raise ArchiveError("BACKUP_OWNER_NOT_ACTIVE")
    return parent


def stage_path(owner, name, *, exists=True):
    if not isinstance(name, str) or not re.fullmatch(r"reader-bk-(import|download)-[A-Za-z0-9_-]{8,80}", name):
        raise ArchiveError("BACKUP_STAGING_INVALID")
    parent = directory(owner); target = parent / name
    if target.is_symlink() or target.resolve().parent != parent.resolve() or (exists and not target.is_dir()):
        raise ArchiveError("BACKUP_STAGING_INVALID")
    return target


def public(operation):
    result = {"id":operation.id,"kind":operation.kind,"status":operation.status,"version":operation.version,
              "counts":operation.counts,"expiresAt":operation.expires_at.isoformat(),"protocol":PROTOCOL}
    if operation.error_code: result["errorCode"] = operation.error_code
    for name in ("objectType","targetOwnerId"):
        if name in operation.result:result[name]=operation.result[name]
    if operation.result.get("receipt") is not None: result["receipt"] = operation.result["receipt"]
    return result


def active(db, owner):
    user = db.scalar(select(User).where(User.id==owner,User.status=="active").with_for_update(read=True))
    if user is None: raise ArchiveError("BACKUP_OWNER_NOT_ACTIVE")
    return user


def owned_operation(db, owner, identifier, *, lock=False):
    query=select(PortableOperation).where(PortableOperation.id==identifier,PortableOperation.owner_id==owner)
    if lock: query=query.with_for_update()
    operation=db.scalar(query.execution_options(populate_existing=True))
    if operation is None: raise Problem("RESOURCE_NOT_FOUND","备份记录不存在或不可访问",404)
    return operation


def create_operation(db, owner, kind, key, payload):
    if not isinstance(key,str) or not key or len(key)>160: raise Problem("IDEMPOTENCY_KEY_REQUIRED","请求缺少有效幂等键")
    active(db,owner)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"),{"id":int(digest(owner+":portable:"+kind+":"+key)[:15],16)})
    previous=db.scalar(select(PortableOperation).where(PortableOperation.owner_id==owner,PortableOperation.kind==kind,PortableOperation.request_key==key))
    hashed=digest(canonical(payload))
    if previous:
        if previous.payload_hash!=hashed: raise Problem("IDEMPOTENCY_CONFLICT","同一请求标识对应了不同备份",409)
        db.commit();return previous,False
    operation=PortableOperation(owner_id=owner,kind=kind,request_key=key,payload_hash=hashed,expires_at=now()+timedelta(minutes=EXPIRY_MINUTES))
    db.add(operation);db.commit()
    return operation,True


def hash_upload(handle):
    handle.seek(0); digest=hashlib.sha256();size=0
    while chunk:=handle.read(1024**2):
        size+=len(chunk)
        if size>MAX_CIPHERTEXT_BYTES+128: raise ArchiveError("BACKUP_SIZE_LIMIT")
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest(),size


def fail_operation(db, operation_id, owner, code):
    db.rollback()
    operation=owned_operation(db,owner,operation_id,lock=True)
    if operation.status=="preparing":
        operation.status="failed";operation.error_code=code;operation.version+=1
    db.commit()


def allocate_stage(db,operation,owner):
    """Record the allocated name durably before writing any plaintext.

    A worker crash leaves an expired, discoverable stage even if preparing did
    not finish. Export's snapshot and ZIP work directories stay inside it.
    """
    active(db,owner);operation=owned_operation(db,owner,operation.id,lock=True)
    if operation.status!="preparing" or operation.stage_name is not None:raise ArchiveError("BACKUP_STAGING_INVALID")
    prefix="reader-bk-import-" if operation.kind=="restore" else "reader-bk-download-"
    operation.stage_name=prefix+uid();db.commit()
    root=stage_path(owner,operation.stage_name,exists=False);root.mkdir(mode=0o700)
    if (settings.storage_path/".deleted"/owner).exists():
        _remove_owned_stage(root,root.parent,prefix)
        raise ArchiveError("BACKUP_OWNER_NOT_ACTIVE")
    return root


def preview_backup(db, owner, key, upload, password):
    digest_value,size=hash_upload(upload)
    operation,fresh=create_operation(db,owner,"restore",key,{"fileHash":digest_value,"size":size})
    if not fresh:return public(operation)
    prepared=None;root=None
    try:
        root=allocate_stage(db,operation,owner)
        prepared=prepare_backup(upload,password,root.parent,allocated_root=root)
        from .preflight import preflight_worlds
        preflight_worlds(prepared)
        raw=encode_json(prepared.manifest);(prepared.root/"validated-manifest.json").write_bytes(raw)
        active(db,owner); operation=owned_operation(db,owner,operation.id,lock=True)
        operation.stage_name=prepared.root.name;operation.manifest_hash=digest(raw);operation.counts=prepared.object_counts
        operation.status="ready";operation.result={"objectType":prepared.manifest["objectType"],"targetOwnerId":owner}
        db.commit();return public(operation)
    except BaseException as error:
        if root is not None:_remove_owned_stage(root,root.parent,"reader-bk-import-")
        fail_operation(db,operation.id,owner,getattr(error,"code","BACKUP_PREPARATION_FAILED"))
        raise problem(error)


def export_backup(db, owner, story_id, key, password, include_source):
    story_root(db,owner,story_id)
    operation,fresh=create_operation(db,owner,"export",key,{"storyId":story_id,"includeSource":include_source})
    if not fresh:return public(operation)
    root=None
    try:
        root=allocate_stage(db,operation,owner)
        with SessionLocal() as snapshot_db:
            snapshot_db.connection(execution_options={"isolation_level":"REPEATABLE READ"})
            with build_domain_snapshot(snapshot_db,owner,story_id,root,include_source=include_source) as snapshot:
                with (root/"private.srbackup").open("xb") as output: write_backup(snapshot.root,snapshot.manifest,output,password,root)
                counts={}
                for item in snapshot.manifest['objects']:counts[item['type']]=counts.get(item['type'],0)+1
        active(db,owner);operation=owned_operation(db,owner,operation.id,lock=True)
        operation.stage_name=root.name;operation.status="ready";operation.counts=counts;db.commit()
        return public(operation)
    except BaseException as error:
        if root is not None:_remove_owned_stage(root,root.parent,"reader-bk-download-")
        fail_operation(db,operation.id,owner,getattr(error,"code","BACKUP_EXPORT_FAILED"))
        raise problem(error)


def load_preview(operation):
    if operation.kind!="restore" or operation.status!="ready" or operation.expires_at<=now():
        raise Problem("BACKUP_PREVIEW_EXPIRED","预检已失效，请重新选择备份文件",409)
    root=stage_path(operation.owner_id,operation.stage_name)
    raw=(root/"validated-manifest.json").read_bytes()
    if len(raw)>ArchiveLimits().max_manifest_bytes or digest(raw)!=operation.manifest_hash:raise ArchiveError("BACKUP_FILE_HASH_MISMATCH")
    manifest=validate_manifest(parse_json(raw,ArchiveLimits().max_manifest_bytes))
    return PreparedArchive(root,directory(operation.owner_id),root/"files",manifest,{})


def restore_preview(owner, identifier, key, expected_version, confirmed, target_owner):
    if confirmed is not True or target_owner!=owner:raise Problem("BACKUP_CONFIRMATION_REQUIRED","请确认当前账号与完整恢复范围",403)
    if not key or len(key)>160:raise Problem("IDEMPOTENCY_KEY_REQUIRED","请求缺少有效幂等键")
    hashed=digest(canonical({"expectedVersion":expected_version,"targetOwnerId":owner,"confirmed":True}))
    def completed_replay():
        with SessionLocal() as db:
            active(db,owner);operation=owned_operation(db,owner,identifier)
            if operation.status=="restored" and operation.result.get("requestId")==key and operation.result.get("requestHash")==hashed:return public(operation)
        return None
    try:
        with SessionLocal() as db:
            active(db,owner); operation=owned_operation(db,owner,identifier)
            if operation.status=="restored":
                if operation.result.get("requestId")!=key or operation.result.get("requestHash")!=hashed:raise Problem("IDEMPOTENCY_CONFLICT","这次预检已恢复，查看已有故事即可",409)
                return public(operation)
            prepared=load_preview(operation);db.rollback()
    except (ArchiveError,OSError) as error:
        replay=completed_replay()
        if replay is not None:return replay
        raise problem(error)
    def authorize(db):
        active(db,owner);operation=owned_operation(db,owner,identifier,lock=True)
        if operation.status=="restored":
            if operation.result.get("requestId")==key and operation.result.get("requestHash")==hashed:return operation.result["receipt"]
            raise Problem("IDEMPOTENCY_CONFLICT","这次预检已恢复，查看已有故事即可",409)
        if operation.version!=expected_version:raise Problem("BACKUP_PREVIEW_CONFLICT","预检记录已变化，请刷新后确认",409)
        if operation.status!="ready" or operation.expires_at<=now():raise Problem("BACKUP_PREVIEW_EXPIRED","预检已失效，请重新选择备份文件",409)
        return None
    def save(db, result):
        operation=owned_operation(db,owner,identifier,lock=True)
        receipt={key:result[key] for key in ("storyId","counts","historicalTasksReplayed","creditTransferred")}
        operation.status="restored";operation.version+=1
        operation.result={"receipt":receipt,"requestId":key,"requestHash":hashed}
    try:
        with SessionLocal() as db:restore_domain(db,prepared,owner,authorize=authorize,record_receipt=save)
    except BaseException as error:
        replay=completed_replay()
        if replay is not None:return replay
        raise problem(error)
    # The receipt is committed even if deletion of this owned plaintext stage
    # fails. A repeat GET/replay returns that one copy; expiry cleanup retries.
    try:prepared.close()
    except (OSError,ArchiveError):pass
    with SessionLocal() as db:return public(owned_operation(db,owner,identifier))


def cleanup_expired(db, limit=20, *, scan_limit=200):
    if type(limit) is not int or type(scan_limit) is not int or not 1<=limit<=scan_limit<=1000:raise ValueError("Invalid portable cleanup budget")
    completed=0
    scanned=0;cursor=None;cutoff=now()
    while scanned<scan_limit and completed<limit:
        query=select(PortableOperation).where(PortableOperation.expires_at<=cutoff,PortableOperation.stage_name.is_not(None),
            or_(PortableOperation.result["cleanupRetryAt"].astext.is_(None),PortableOperation.result["cleanupRetryAt"].astext<=cutoff.isoformat()))
        if cursor is not None:query=query.where(tuple_(PortableOperation.expires_at,PortableOperation.id)>cursor)
        operations=list(db.scalars(query.order_by(PortableOperation.expires_at,PortableOperation.id).limit(min(limit,scan_limit-scanned)).with_for_update(skip_locked=True)))
        if not operations:break
        for operation in operations:
            scanned+=1;cursor=(operation.expires_at,operation.id)
            try:
                root=stage_path(operation.owner_id,operation.stage_name,exists=False)
                prefix="reader-bk-import-" if operation.kind=="restore" else "reader-bk-download-"
                _remove_owned_stage(root,directory(operation.owner_id),prefix)
                operation.stage_name=None
                if operation.status!="restored":operation.status="expired";operation.version+=1
                completed+=1
            except (OSError,ArchiveError):
                attempts=operation.result.get("cleanupAttempts",0)
                if type(attempts) is not int or attempts<0:attempts=0
                operation.result={**operation.result,"cleanupAttempts":attempts+1,
                    "cleanupRetryAt":(cutoff+timedelta(minutes=min(60,5*(attempts+1)))).isoformat()}
                continue
            if completed>=limit:break
    db.flush();return completed


def purge_owner_files(owner):
    """Account cleanup calls after its durable tombstone, before User CASCADE.

    Failure propagates to the existing delete-account recoverable task; a late
    preparation/export cannot allocate another stage after the tombstone.
    """
    if not re.fullmatch(r"[a-f0-9]{32}",owner) or not (settings.storage_path/".deleted"/owner).is_file():
        raise ArchiveError("BACKUP_OWNER_PURGE_NOT_AUTHORIZED")
    parent=settings.storage_path/".portable";target=parent/owner
    if parent.is_symlink() or not parent.resolve().is_relative_to(settings.storage_path.resolve()):raise ArchiveError("BACKUP_PATH_INVALID")
    _remove_owned_stage(target,parent,owner)


def maintenance():
    """Finite worker hook; no model work and safe before the additive migration."""
    with SessionLocal() as db:
        if db.scalar(text("SELECT to_regclass('portable_operations')")) is None:return 0
        count=cleanup_expired(db);db.commit();return count
