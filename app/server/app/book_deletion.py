"""Book lifecycle, exact publication detachment and committed file cleanup."""
from copy import deepcopy
import json
from sqlalchemy import select, text, delete
from .db import SessionLocal
from .domain import digest, canonical, make_anchor, validate_anchor
from .errors import Problem
from .models import Book, BookDeletion, Branch, Revision, ContentBlock, SourceDocument, Progress, Bookmark, ReadingActivity, KnowledgeSnapshot, AnalysisChunk, RoleSession, ResumeTarget, Task, TaskEvent, Candidate, Idempotency, OperationPlan, uid, now
from .storage import private_path, write_private

_cleanup_cursor = None


def lifecycle(db, owner, book_id):
    if book_id:
        db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id":int(digest("book-lifecycle:"+owner+":"+book_id)[:15],16)})


def task_book(db, task):
    if not task or task.task_type.startswith("world_") or task.task_type == "delete_account":
        return None
    operation=task.payload.get("operation")
    operation=operation if isinstance(operation,dict) else {}
    return (task.payload.get("bookId") or (task.payload.get("anchor") or {}).get("bookId") or
        (operation.get("anchor") or {}).get("bookId") or
        task.result.get("bookId") or task.result.get("duplicateBookId"))


def lock_task(db, task_id):
    row=db.get(Task,task_id)
    if row:
        lifecycle(db,row.owner_id,task_book(db,row))
    return db.scalar(select(Task).where(Task.id==task_id).with_for_update().execution_options(populate_existing=True))


def deleted(db, owner, book_id):
    return db.scalar(select(BookDeletion).where(BookDeletion.owner_id==owner,BookDeletion.book_id==book_id))


def require_existing(db, owner, book_id):
    lifecycle(db,owner,book_id)
    book=db.scalar(select(Book).where(Book.id==book_id,Book.owner_id==owner).execution_options(populate_existing=True))
    if not book:
        code="BOOK_DELETED" if deleted(db,owner,book_id) else "RESOURCE_NOT_FOUND"
        raise Problem(code,"内容不存在或已移除",404)
    return book


def rebook(value, old_id, new_id):
    if isinstance(value,dict):
        return {key:(new_id if key=="bookId" and item==old_id else rebook(item,old_id,new_id)) for key,item in value.items()}
    if isinstance(value,list):
        return [rebook(item,old_id,new_id) for item in value]
    return value


def references(value, book_id, identities=()):
    if isinstance(value,dict):
        return any((key in {"bookId","duplicateBookId"} and item==book_id) or
            (key in {"taskId","candidateId","sessionId","flowId"} and isinstance(item,str) and item in identities) or
            references(item,book_id,identities) for key,item in value.items())
    return isinstance(value,list) and any(references(item,book_id,identities) for item in value)


def retained_branches(db,owner,book_id):
    from .world.models import WorldPublication
    rows=list(db.scalars(select(Branch).where(Branch.owner_id==owner,Branch.book_id==book_id)))
    roots=set(db.scalars(select(WorldPublication.branch_id).where(WorldPublication.owner_id==owner,WorldPublication.book_id==book_id)))
    roots.update(db.scalars(select(Revision.branch_id).join(Branch,Branch.id==Revision.branch_id).where(
        Revision.owner_id==owner,Branch.owner_id==owner,Branch.book_id==book_id,
        Revision.provenance["operation"].astext=="world_publication")))
    source=db.scalar(select(SourceDocument).where(SourceDocument.owner_id==owner,SourceDocument.book_id==book_id))
    kind=source.source_map.get("kind") if source else None
    if kind=="detached_publication":
        # This is the explicitly selected preserved publication object itself.
        return []
    if kind=="world_publication":
        book=db.get(Book,book_id)
        # A publication's own root must really disappear. A historical foreign
        # world publication embedded below it remains a separate protected root.
        roots.discard(book.original_branch_id)
    keep=set(roots)
    while True:
        expanded=keep | {b.id for b in rows if b.parent_branch_id in keep}
        if expanded==keep: break
        keep=expanded
    return [b for b in rows if b.id in keep]


def detach(db,owner,book,branches):
    """Move referenced records, preserving revision/block identity and quoteHash."""
    from .world.models import WorldPublication
    if not branches: return []
    kept={b.id for b in branches}
    revisions=list(db.scalars(select(Revision).where(Revision.owner_id==owner,Revision.branch_id.in_(kept))))
    revision_ids={r.id for r in revisions}
    block_ids={bid for r in revisions for chapter in r.manifest for bid in chapter["blocks"]}
    blocks=list(db.scalars(select(ContentBlock).where(ContentBlock.owner_id==owner,ContentBlock.book_id==book.id,ContentBlock.id.in_(block_ids))))
    if len(blocks)!=len(block_ids): raise Problem("PUBLICATION_INVALID","保留故事内容不完整",409)
    root=min(branches,key=lambda b:(b.created_at,b.id))
    source_id=uid(); carrier=Book(id=uid(),owner_id=owner,title=book.title[:180]+" · 保留故事",source_hash=digest(canonical(sorted(revision_ids))),
        source_id=source_id,original_branch_id=root.id,original_revision_id=root.head_revision_id,source_label="来源已移除 · 本人保留的公开阅读故事")
    db.add(carrier);db.flush()
    source=db.scalar(select(SourceDocument).where(SourceDocument.owner_id==owner,SourceDocument.book_id==book.id))
    # Historical same-book publications lose their original analysis snapshots
    # when the source disappears. Preserve only exact scoped identity references,
    # never the old cards, names, goals or private knowledge. These IDs let a
    # migration roundtrip restore retained role sessions without guessing names.
    from .models import CharacterAlias, CharacterIdentity
    identity_bindings = {}
    for alias in db.scalars(select(CharacterAlias).join(CharacterIdentity,
            CharacterIdentity.id == CharacterAlias.identity_id).where(
            CharacterAlias.owner_id == owner, CharacterIdentity.owner_id == owner,
            CharacterAlias.scope_kind == 'reader', CharacterAlias.scope_id.in_(kept))):
        identity_bindings[(alias.scope_id, alias.entity_id)] = alias.identity_id
    for role in db.scalars(select(RoleSession).join(CharacterIdentity,
            CharacterIdentity.id == RoleSession.character_identity_id).where(
            RoleSession.owner_id == owner, CharacterIdentity.owner_id == owner,
            RoleSession.book_id == book.id, RoleSession.branch_id.in_(kept))):
        key = (role.branch_id, role.character_id)
        if key in identity_bindings and identity_bindings[key] != role.character_identity_id:
            raise Problem('CHARACTER_REFERENCE_INVALID', '保留人物引用不一致', 409)
        identity_bindings[key] = role.character_identity_id
    # Copy only images actually referenced by retained HTML, with fresh names.
    assets={}
    for asset_id,ref in (source.source_map.get("assets",{}) if source else {}).items():
        if any(b.html and ('data-asset="'+asset_id+'"') in b.html for b in blocks):
            new_ref="publication-asset-"+source_id+"-"+asset_id+".png"
            assets[asset_id]=write_private(owner,new_ref,private_path(owner,ref).read_bytes())
    content="\n\n".join(b.text for b in sorted(blocks,key=lambda b:b.id))
    ref=write_private(owner,"publication-source-"+source_id+".txt",content.encode())
    db.add(SourceDocument(id=source_id,owner_id=owner,book_id=carrier.id,file_hash=digest(content),file_ref=ref,
        normalized_ref=ref,format="txt",encoding="utf-8",source_map={"assets":assets,"kind":"detached_publication",
            "characterAliasBindings": [{"branchId": branch, "entityId": entity, "identityId": identity}
                for (branch, entity), identity in sorted(identity_bindings.items())]}))
    for b in branches:
        b.book_id=carrier.id;b.version+=1
        if b.parent_branch_id not in kept: b.parent_branch_id=None;b.fork_anchor=None
        else: b.fork_anchor=rebook(b.fork_anchor,book.id,carrier.id)
    for r in revisions:
        if r.parent_revision_id not in revision_ids:r.parent_revision_id=None
    for b in blocks:b.book_id=carrier.id
    for model in (Progress,Bookmark,ReadingActivity,RoleSession):
        for row in db.scalars(select(model).where(model.owner_id==owner,model.book_id==book.id,model.branch_id.in_(kept))):
            row.book_id=carrier.id
            if isinstance(row,Progress):
                row.anchor=rebook(row.anchor,book.id,carrier.id);row.max_observed_anchor=rebook(row.max_observed_anchor,book.id,carrier.id);row.version+=1
            elif isinstance(row,Bookmark):row.anchor=rebook(row.anchor,book.id,carrier.id);row.version+=1
            elif isinstance(row,RoleSession):
                row.pinned_anchor=rebook(row.pinned_anchor,book.id,carrier.id);row.status="needs_refresh";row.pending_task_id=None;row.snapshot_id=""
    for row in db.scalars(select(WorldPublication).where(WorldPublication.owner_id==owner,WorldPublication.book_id==book.id,WorldPublication.branch_id.in_(kept))):
        row.book_id=carrier.id;row.anchor=rebook(row.anchor,book.id,carrier.id)
    for row in db.scalars(select(ResumeTarget).where(ResumeTarget.owner_id==owner)):
        if row.context.get("bookId")==book.id and row.context.get("readerBranchId") in kept and row.mode=="read":
            row.context=rebook(row.context,book.id,carrier.id)
            if row.story_id=="book-"+book.id:
                row.story_id="book-"+carrier.id;row.context={**row.context,"storyId":row.story_id}
            row.version+=1
    from .models import PlaybackPosition
    if db.scalar(text("SELECT to_regclass('playback_positions')")) is not None:
        for row in db.scalars(select(PlaybackPosition).where(PlaybackPosition.owner_id == owner,
                PlaybackPosition.book_id == book.id, PlaybackPosition.kind == "reader", PlaybackPosition.line_id.in_(kept))):
            row.book_id = carrier.id
            row.context = rebook(row.context, book.id, carrier.id)
            row.last_completed_anchor = rebook(row.last_completed_anchor, book.id, carrier.id)
            row.max_heard_anchor = rebook(row.max_heard_anchor, book.id, carrier.id)
            if row.story_id == "book-" + book.id:
                row.story_id = "book-" + carrier.id
                row.context = {**row.context, "storyId": row.story_id}
            row.version += 1
    from .comparison_lifecycle import detach_publication_views
    detach_publication_views(db, owner, book.id, carrier.id, kept)
    db.flush()
    return [{"bookId":carrier.id,"storyId":"book-"+carrier.id,"branchIds":sorted(kept)}]


def prepare_delete(db,owner,book_id,expected_version):
    lifecycle(db,owner,book_id)
    previous=deleted(db,owner,book_id)
    if previous:
        if expected_version!=previous.book_version:raise Problem("VERSION_CONFLICT","删除版本不一致",409)
        return previous
    book=require_existing(db,owner,book_id)
    if book.version!=expected_version:raise Problem("VERSION_CONFLICT","小说信息已更新",409,{"currentVersion":book.version})
    branches=retained_branches(db,owner,book_id); kept={b.id for b in branches}
    sources=list(db.scalars(select(SourceDocument).where(SourceDocument.owner_id==owner,SourceDocument.book_id==book_id)))
    files={ref for s in sources for ref in [s.file_ref,s.normalized_ref,*s.source_map.get("assets",{}).values()] if ref}
    tasks=list(db.scalars(select(Task).where(Task.owner_id==owner,Task.task_type.not_like("world_%"),Task.task_type!="delete_account").order_by(Task.id)))
    affected={t.id for t in tasks if references(t.payload,book_id) or references(t.result,book_id)}
    while True:
        expanded=affected|{t.id for t in tasks if references(t.payload,book_id,affected)}
        if expanded==affected:break
        affected=expanded
    session_ids=set(db.scalars(select(RoleSession.id).where(RoleSession.owner_id==owner,RoleSession.book_id==book_id)))
    candidate_ids=set(db.scalars(select(Candidate.id).where(Candidate.owner_id==owner,Candidate.task_id.in_(affected))))
    retained=detach(db,owner,book,branches)
    from .comparison.cleanup import purge as purge_comparison
    purge_comparison(db, owner, 'book', book_id)
    if db.scalar(text("SELECT to_regclass('image_assets')")) is not None:
        from .assets.service import detach_book
        detach_book(db,owner,book_id,retained)
    receipts=[]
    for task in tasks:
        if task.id not in affected:continue
        task=db.scalar(select(Task).where(Task.id==task.id).with_for_update())
        for ref in (task.payload.get("fileRef"),task.checkpoint.get("parsedRef")):
            if ref:
                files.add(ref)
                if ref==task.checkpoint.get("parsedRef") and private_path(owner,ref).exists():
                    files.update(json.loads(private_path(owner,ref).read_text("utf-8")).get("assets",{}).values())
        files.update(task.checkpoint.get('proseStreamRefs', []))
        calls=deepcopy(task.checkpoint.get("calls",{}))
        for call in calls.values():
            path=private_path(owner,"journal-"+task.id+"-"+call["callId"]+".json")
            try:
                value=json.loads(path.read_text("utf-8"));result=value["result"]
                if (value["taskId"]==task.id and value["callId"]==call["callId"] and
                    value["hash"]==digest(json.dumps(result,ensure_ascii=False,sort_keys=True))):
                    minimum={"usage":deepcopy(result["usage"])}
                    for key in ("notSent","latencyMs"):
                        if key in result:minimum[key]=result[key]
                    receipts.append({"taskId":task.id,"callId":call["callId"],"result":minimum})
            except (OSError,ValueError,KeyError,TypeError):
                # An invalid/unreceived receipt cannot justify guessed tokens or
                # refunds. Its unknown intent remains preserved without a resend.
                pass
        files.update("journal-"+task.id+"-"+c["callId"]+".json" for c in calls.values())
        task.payload={"bookId":book_id};task.checkpoint={"calls":calls}
        task.result={"bookId":book_id,"deleted":True};task.cancel_requested=True
        task.state="cancelled";task.persistence_state="saved";task.error_code="BOOK_DELETED";task.result_outcome="cancelled"
    db.execute(delete(TaskEvent).where(TaskEvent.task_id.in_(affected)))
    db.execute(delete(Candidate).where(Candidate.owner_id==owner,Candidate.task_id.in_(affected)))
    for model in (Progress,Bookmark,ReadingActivity,RoleSession,KnowledgeSnapshot,AnalysisChunk,SourceDocument):
        db.execute(delete(model).where(model.owner_id==owner,model.book_id==book_id))
    from .models import CharacterScopeRevision, CharacterAlias
    db.execute(delete(CharacterScopeRevision).where(CharacterScopeRevision.owner_id == owner,
        CharacterScopeRevision.context["bookId"].astext == book_id))
    removed_branches = set(db.scalars(select(Branch.id).where(Branch.owner_id == owner, Branch.book_id == book_id)))
    db.execute(delete(CharacterAlias).where(CharacterAlias.owner_id == owner, CharacterAlias.scope_kind == "reader",
        CharacterAlias.scope_id.in_(removed_branches)))
    for row in list(db.scalars(select(ResumeTarget).where(ResumeTarget.owner_id==owner))):
        if row.story_id=="book-"+book_id or row.context.get("bookId")==book_id:db.delete(row)
    ids=affected|session_ids|candidate_ids
    for row in db.scalars(select(Idempotency).where(Idempotency.owner_id==owner)):
        if references(row.result,book_id,ids):
            branch_id=(row.result or {}).get("branchId") or (row.result or {}).get("readingAnchor",{}).get("branchId")
            if retained and branch_id in kept:row.result=rebook(row.result,book_id,retained[0]["bookId"])
            else:row.result={"bookId":book_id,"deleted":True}
    for row in list(db.scalars(select(OperationPlan).where(OperationPlan.owner_id==owner))):
        if references(row.payload,book_id):db.delete(row)
    receipt=BookDeletion(owner_id=owner,book_id=book_id,book_version=expected_version,retained=retained,
        cleanup_manifest={"files":sorted(files),"taskIds":sorted(affected),"receipts":receipts},cleanup_state="pending")
    db.add(receipt);db.delete(book);db.flush()
    return receipt


def receipt_result(row):
    return {"deletionId":row.id,"bookId":row.book_id,"deleted":True,"cleanupPending":row.cleanup_state!="completed",
        "cleanupState":row.cleanup_state,"retainedStories":row.retained}


def clean_deleted_books(owner=None,book_id=None):
    """Only committed rows authorize markers/unlink; HTTP failure is retryable."""
    from .config import settings
    global _cleanup_cursor
    clean_received_book_usage(owner)
    from sqlalchemy import case
    with SessionLocal() as db:
        query=select(BookDeletion.id).where(BookDeletion.cleanup_state!="completed")
        if owner is not None:query=query.where(BookDeletion.owner_id==owner)
        if book_id is not None:query=query.where(BookDeletion.book_id==book_id)
        if owner is None and book_id is None and _cleanup_cursor is not None:
            query=query.order_by(case((BookDeletion.id>_cleanup_cursor,0),else_=1))
        ids=list(db.scalars(query.order_by(BookDeletion.id).limit(20)))
        if ids and owner is None and book_id is None:_cleanup_cursor=ids[-1]
    complete=True
    for identity in ids:
        with SessionLocal() as db:
            row=db.scalar(select(BookDeletion).where(BookDeletion.id==identity).with_for_update(skip_locked=True))
            if row is None:complete=False;continue
            try:
                if not (settings.storage_path/".deleted"/row.owner_id).exists():
                    for task_id in row.cleanup_manifest.get("taskIds",[]):
                        write_private(row.owner_id,"book-deleted-"+task_id+".marker",b"deleted")
                from .worker import settle_usage
                for receipt in row.cleanup_manifest.get("receipts",[]):
                    settle_usage(receipt["taskId"],receipt["callId"],receipt["result"],accounting_only=True)
                for name in row.cleanup_manifest.get("files",[]):private_path(row.owner_id,name).unlink(missing_ok=True)
                row.cleanup_state="completed";row.completed_at=now();db.commit()
            except (OSError,Problem):db.rollback();complete=False
    return complete


def usage_receipt(owner,task_id,call_id,result):
    """Durable accounting alone survives cancellation and DB unavailability."""
    minimum={"usage":deepcopy(result["usage"])}
    for key in ("notSent","latencyMs"):
        if key in result:minimum[key]=result[key]
    value={"taskId":str(task_id),"callId":call_id,"result":minimum}
    write_private(owner,"book-usage-"+str(task_id)+"-"+call_id+".json",canonical(value).encode())


def clean_received_book_usage(owner=None):
    from .config import settings
    from .worker import settle_usage
    import re
    from sqlalchemy.exc import SQLAlchemyError
    root=settings.storage_path.resolve()
    folders=[root/owner] if owner is not None else ([p for p in root.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch(r"[a-f0-9]{32}",p.name)] if root.exists() else [])
    for folder in folders:
        for path in sorted(folder.glob("book-usage-*.json")):
            match=re.fullmatch(r"book-usage-([a-f0-9]{32})-([a-f0-9]{32})\.json",path.name)
            if not match or path.is_symlink():continue
            try:
                value=json.loads(path.read_text("utf-8"));task_id,call_id=match.groups()
                if value.get("taskId")!=task_id or value.get("callId")!=call_id:continue
                with SessionLocal() as db:
                    from .models import UsageRecord
                    usage=db.scalar(select(UsageRecord).where(UsageRecord.owner_id==folder.name,UsageRecord.task_id==task_id,UsageRecord.provider_call_id==call_id))
                    if usage is None:
                        # Full account purge legitimately removes its ledger.
                        if not (root/".deleted"/folder.name).exists():continue
                    else:
                        task=db.get(Task,task_id)
                        if usage.status not in {"completed","not_sent"}:
                            # Active/recoverable reader execution owns its normal
                            # Task projection. Maintenance is deletion-only.
                            if not task or task.owner_id!=folder.name or not task.result.get("deleted"):
                                continue
                            settle_usage(task_id,call_id,value["result"],accounting_only=True)
                path.unlink(missing_ok=True)
            except (OSError,ValueError,KeyError,Problem,SQLAlchemyError):
                # A later file is still attempted; disk faults cannot starve it.
                continue


def resolve_context(db,owner,book_id,branch_id,revision_id=None,anchor=None):
    from .domain import owned
    lifecycle(db,owner,book_id)
    branch=db.scalar(select(Branch).where(Branch.id==branch_id,Branch.owner_id==owner))
    if not branch:raise Problem("RESOURCE_NOT_FOUND","阅读位置不存在或不可访问",404)
    migrated=False
    if branch.book_id!=book_id:
        row=deleted(db,owner,book_id)
        if not row or not any(branch_id in item["branchIds"] and branch.book_id==item["bookId"] for item in row.retained):
            raise Problem("RESOURCE_NOT_FOUND","阅读位置不存在或不可访问",404)
        migrated=True
    book=require_existing(db,owner,branch.book_id)
    revision=owned(db,Revision,revision_id or branch.head_revision_id,owner)
    if revision.branch_id!=branch.id:raise Problem("RESOURCE_NOT_FOUND","修订不属于这个阅读位置",404)
    if anchor is not None:
        if anchor.get("bookId")!=book_id or anchor.get("branchId")!=branch_id or anchor.get("revisionId")!=revision.id:
            raise Problem("ANCHOR_INVALID","位置与请求不一致")
        anchor=rebook(anchor,book_id,book.id);validate_anchor(db,anchor,owner)
    return {"bookId":book.id,"branchId":branch.id,"revisionId":revision.id,"anchor":anchor,"storyId":"book-"+book.id,"migrated":migrated}
