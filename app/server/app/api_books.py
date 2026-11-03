import json
from datetime import timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, Query
from pydantic import Field, field_validator, StrictBool
from sqlalchemy import select, tuple_
from .db import get_db
from .models import Book, Branch, Revision, ContentBlock, SourceDocument, Progress, Bookmark, Task, Outbox, Candidate, Idempotency, uid, now
from .security import current_user
from .domain import owned, digest, canonical, make_anchor, validate_anchor, prefix_blocks, utf16_len, anchor_order, idempotent, is_tail, is_import_root, anchor_label
from .schemas import ProgressInput, Anchor, Strict, AcceptInput
from .errors import Problem
from .storage import write_private, private_path
from .importer import MAX_UPLOAD
from .generation_review import candidate_acceptable, saved_warnings

router = APIRouter()


class DeleteBook(Strict):
    expectedVersion: int = Field(strict=True, ge=1)


class ResolveReadingContext(Strict):
    bookId: str = Field(pattern=r"^[a-f0-9]{32}$")
    branchId: str = Field(pattern=r"^[a-f0-9]{32}$")
    revisionId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    anchor: Anchor | None = None


@router.post("/reading-context/resolve")
def resolve_reading_context(data: ResolveReadingContext, user=Depends(current_user), db=Depends(get_db)):
    from .book_deletion import resolve_context
    return resolve_context(db, user.id, data.bookId, data.branchId, data.revisionId,
        data.anchor.model_dump() if data.anchor else None)


@router.get("/books/{book_id}/deletion")
def book_deletion_receipt(book_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .book_deletion import deleted, receipt_result
    row = deleted(db, user.id, book_id)
    if not row:
        raise Problem("RESOURCE_NOT_FOUND", "删除回执不存在或不可访问", 404)
    return receipt_result(row)


@router.delete("/books/{book_id}", status_code=202)
def delete_book(book_id: str, data: DeleteBook, request: Request, user=Depends(current_user), db=Depends(get_db)):
    from .book_deletion import prepare_delete, clean_deleted_books, deleted, receipt_result
    def commit():
        return receipt_result(prepare_delete(db, user.id, book_id, data.expectedVersion))
    saved = idempotent(db, user.id, "book-delete:" + book_id, request.headers.get("idempotency-key"), data.model_dump(), commit)
    from sqlalchemy.exc import SQLAlchemyError
    try:
        clean_deleted_books(user.id, book_id)
        db.expire_all()
        return receipt_result(deleted(db, user.id, book_id))
    except (OSError, SQLAlchemyError):
        db.rollback()
        # DB deletion is already committed. Do not make a persistence outage
        # masquerade as a rejected DELETE or invite an accidental recreation.
        return {**saved, "cleanupPending": True, "cleanupState": "pending"}


def paginate(db, cls, owner, cursor=None, limit=20, filters=(), ascending=False):
    limit = min(100, max(1, limit))
    query = select(cls).where(cls.owner_id == owner, *filters)
    if cursor:
        point = owned(db, cls, cursor, owner)
        column = tuple_(cls.created_at, cls.id)
        query = query.where(column > (point.created_at, point.id) if ascending else column < (point.created_at, point.id))
    query = query.order_by(cls.created_at.asc(), cls.id.asc()) if ascending else query.order_by(cls.created_at.desc(), cls.id.desc())
    rows = list(db.scalars(query.limit(limit + 1)))
    return rows[:limit], rows[limit - 1].id if len(rows) > limit else None


def book_result(db, book):
    progress = db.scalar(select(Progress).where(Progress.owner_id == book.owner_id, Progress.book_id == book.id).order_by(Progress.updated_at.desc()).limit(1))
    return {"id": book.id, "title": book.title, "source": book.source_label, "status": book.status, "version": book.version,
            "favorite": book.favorite, "tags": book.tags, "createdAt": book.created_at.isoformat(),
            "originalBranchId": book.original_branch_id, "originalRevisionId": book.original_revision_id,
            "importStatus": "reading_ready", "aiStatus": "ai_unprepared", "coverUrl": "/assets/fog-harbor.png",
            "lastProgress": {"branchId": progress.branch_id, "revisionId": progress.revision_id, "anchor": progress.anchor, "updatedAt": progress.updated_at.isoformat()} if progress else None}


def create_book(db, owner, title, parsed, file_hash, file_ref, format, source_label="用户提供 · 权利来源未核验"):
    book_id, branch_id, revision_id, source_id = uid(), uid(), uid(), uid()
    book = Book(id=book_id, owner_id=owner, title=title, source_hash=file_hash, source_id=source_id,
                original_branch_id=branch_id, original_revision_id=revision_id, source_label=source_label)
    db.add(book)
    db.flush()
    branch = Branch(id=branch_id, owner_id=owner, book_id=book_id, name="原文", head_revision_id=revision_id)
    db.add(branch)
    db.flush()
    # Parser IDs are deterministic, while persisted IDs are private and unique.
    chapters, mapping = [], {}
    for c in parsed["chapters"]:
        mapping[c["id"]] = uid()
        chapters.append({"id": mapping[c["id"]], "title": c["title"], "blocks": []})
    chapter_by_id = {c["id"]: c for c in chapters}
    block_map = {}
    normalized_cursor, normalized_utf16 = 0, 0
    for b in parsed["blocks"]:
        block = ContentBlock(id=uid(), owner_id=owner, book_id=book_id, chapter_id=mapping[b["chapterId"]], text=b["text"], html=b.get("html"), content_hash=digest(b["text"]), source="original")
        db.add(block)
        chapter_by_id[block.chapter_id]["blocks"].append(block.id)
        start = b.get("sourceOffset", normalized_cursor)
        normalized_utf16 += utf16_len(parsed["normalized"][normalized_cursor:start])
        normalized_cursor = start
        block_map[block.id] = {"parserBlockId": b["id"], "normalizedStart": normalized_utf16,
            "normalizedEnd": normalized_utf16+utf16_len(b["text"]), "offsetUnit": "utf16",
            **b.get("sourceLocation", {})}
    revision = Revision(id=revision_id, owner_id=owner, branch_id=branch_id, manifest=chapters,
                        content_hash=digest(canonical([(b["text"], b.get("html")) for b in parsed["blocks"]])), provenance={"type": "import", "sourceId": source_id})
    db.add(revision)
    normalized_ref = write_private(owner, f"normalized-{source_id}.txt", parsed["normalized"].encode())
    db.add(SourceDocument(id=source_id, owner_id=owner, book_id=book_id, file_hash=file_hash, file_ref=file_ref,
           format=format, encoding=parsed["encoding"], normalized_ref=normalized_ref, source_map={**parsed.get("sourceMap", {}), "assets": parsed.get("assets", {}), "blocks": block_map}))
    db.flush()
    first = owned(db, ContentBlock, chapters[0]["blocks"][0], owner)
    return {"bookId": book_id, "branchId": branch_id, "revisionId": revision_id, "readingAnchor": make_anchor(book_id, branch_id, revision_id, first)}


@router.get("/books")
def books(cursor: str | None = None, limit: int = 20, status: str = "active", user=Depends(current_user), db=Depends(get_db)):
    rows, next_cursor = paginate(db, Book, user.id, cursor, limit, (Book.status == status,))
    return {"items": [book_result(db, b) for b in rows], "nextCursor": next_cursor}


@router.post("/books/imports", status_code=202)
async def import_book(request: Request, file: UploadFile = File(...), encoding: str | None = Form(None), user=Depends(current_user), db=Depends(get_db)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise Problem("IMPORT_LIMIT", "上传超过 100 MiB")
    name = Path(file.filename or "material.txt").name
    if Path(name).suffix.lower() not in {".txt", ".md", ".markdown", ".epub"}:
        raise Problem("UNSUPPORTED_FORMAT", "支持 TXT、Markdown 与无 DRM EPUB")
    file_hash = digest(data)
    duplicate = db.scalar(select(Book).where(Book.owner_id == user.id, Book.source_hash == file_hash))
    payload = {"fileHash": file_hash, "filename": name, "encoding": encoding}

    def create():
        task_id = uid()
        ref = write_private(user.id, f"source-{task_id}{Path(name).suffix.lower()}", data)
        db.add(Task(id=task_id, owner_id=user.id, task_type="import", payload={**payload, "fileRef": ref}, result={"importStatus": "uploaded", "duplicateBookId": duplicate.id if duplicate else None}))
        return {"importTaskId": task_id, "taskId": task_id}
    return idempotent(db, user.id, "import", request.headers.get("idempotency-key"), payload, create)


@router.get("/books/imports/{task_id}")
def import_status(task_id: str, user=Depends(current_user), db=Depends(get_db)):
    task = owned(db, Task, task_id, user.id)
    if task.task_type != "import":
        raise Problem("RESOURCE_NOT_FOUND", "导入任务不存在", 404)
    if task.result.get("deleted"):
        raise Problem("BOOK_DELETED", "来源小说已移除", 404)
    return {"id": task.id, "state": task.state, "errorCode": task.error_code, "sourceFormat":Path(task.payload["filename"]).suffix.lower()[1:], **task.result}


class ConfirmImport(Strict):
    parserVersion: str = "import-v1"
    title: str = Field(min_length=1, max_length=200)
    chapterOrder: list[str] | None = None
    encoding: str | None = None
    reuseDuplicate: bool = False
    prepareCharacters: bool = False


class ImportPreview(Strict):
    encoding: str = Field(min_length=1,max_length=30)


@router.post("/books/imports/{task_id}/preview")
def preview_import(task_id: str, data: ImportPreview, user=Depends(current_user), db=Depends(get_db)):
    from .importer import parse
    task=owned(db,Task,task_id,user.id,lock=True)
    if task.task_type!="import" or task.result.get("importStatus")!="awaiting_confirmation":
        raise Problem("IMPORT_NOT_READY","材料不能重新预览")
    if Path(task.payload["filename"]).suffix.lower()!=".txt":
        raise Problem("ENCODING_NOT_APPLICABLE","EPUB 编码由文件内容声明")
    parsed=parse(private_path(user.id,task.payload["fileRef"]).read_bytes(),task.payload["filename"],user.id,task.id,data.encoding)
    write_private(user.id,task.checkpoint["parsedRef"],json.dumps(parsed,ensure_ascii=False).encode())
    task.result={**task.result,"encoding":parsed["encoding"],"preview":parsed["normalized"][:1000],"warnings":parsed["warnings"],"chapters":[{"id":c["id"],"title":c["title"],"blockCount":len(c["blocks"])} for c in parsed["chapters"]]}
    db.commit()
    return {"id":task.id,"state":task.state,"errorCode":task.error_code,"sourceFormat":"txt",**task.result}


@router.post("/books/imports/{task_id}/confirm", status_code=201)
def confirm_import(task_id: str, data: ConfirmImport, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def confirm():
        def prepare(result):
            if data.prepareCharacters:
                from .character_cards import start_character_index
                try:
                    index = start_character_index(db, user.id, result["bookId"], result["branchId"])
                    result = {**result, "characterIndex": index}
                except Problem as error:
                    if error.code not in {"NO_PROVIDER", "BUDGET_NOT_AUTHORIZED", "CONCURRENCY_LIMIT"}:
                        raise
                    result = {**result, "characterIndex": {"indexState": "deferred", "errorCode": error.code}}
            return result
        task = owned(db, Task, task_id, user.id, lock=True)
        if task.result.get("deleted"):
            raise Problem("BOOK_DELETED", "来源小说已移除", 404)
        if task.result.get("bookId"):
            return task.result["confirmed"]
        if task.result.get("importStatus") != "awaiting_confirmation" or data.parserVersion != "import-v1":
            raise Problem("IMPORT_NOT_READY", "请等待材料解析或重新预览")
        if data.reuseDuplicate and task.result.get("duplicateBookId"):
            book = owned(db, Book, task.result["duplicateBookId"], user.id)
            result=prepare({"bookId": book.id, "branchId": book.original_branch_id, "revisionId": book.original_revision_id, "reused": True})
            task.result={**task.result,"bookId":book.id,"confirmed":result,"importStatus":"reading_ready"}
            return result
        parsed = json.loads(private_path(user.id, task.checkpoint["parsedRef"]).read_text("utf-8"))
        if data.encoding and data.encoding != parsed["encoding"]:
            from .importer import parse
            parsed = parse(private_path(user.id, task.payload["fileRef"]).read_bytes(), task.payload["filename"], user.id, task.id, data.encoding)
        if data.chapterOrder:
            if set(data.chapterOrder) != {c["id"] for c in parsed["chapters"]} or len(set(data.chapterOrder)) != len(data.chapterOrder):
                raise Problem("DIRECTORY_INVALID", "目录顺序无效")
            by_id = {c["id"]: c for c in parsed["chapters"]}
            parsed["chapters"] = [by_id[c] for c in data.chapterOrder]
        result = create_book(db, user.id, data.title, parsed, task.payload["fileHash"], task.payload["fileRef"], Path(task.payload["filename"]).suffix[1:])
        result = prepare(result)
        task.result = {**task.result, "bookId": result["bookId"], "confirmed": result, "importStatus": "reading_ready"}
        return result
    return idempotent(db, user.id, f"import-confirm:{task_id}", request.headers.get("idempotency-key"), data.model_dump(), confirm)


@router.post("/books/demo", status_code=201)
def demo(request: Request, user=Depends(current_user), db=Depends(get_db)):
    from .importer import txt_parse
    fixture = Path(__file__).parent / "fixtures" / "fog-harbor.txt"
    data = fixture.read_bytes()
    file_hash = digest(data)

    def create():
        existing = db.scalar(select(Book).where(Book.owner_id == user.id, Book.source_hash == file_hash))
        if existing:
            return {"bookId": existing.id, "branchId": existing.original_branch_id, "revisionId": existing.original_revision_id}
        ref = write_private(user.id, f"demo-{uid()}.txt", data)
        return create_book(db, user.id, "雾港来信", txt_parse(data), file_hash, ref, "txt", "原创示例 · AI 互动另行标记")
    return idempotent(db, user.id, "demo", request.headers.get("idempotency-key"), {}, create)


@router.get("/books/{book_id}")
def book(book_id: str, user=Depends(current_user), db=Depends(get_db)):
    return book_result(db, owned(db, Book, book_id, user.id))


class Metadata(Strict):
    expectedVersion: int
    name: str | None = Field(default=None, min_length=1, max_length=100)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = None
    favorite: StrictBool | None = None
    tags: list[str] | None = Field(default=None, max_length=8)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, values):
        if values is None:
            return None
        cleaned = list(dict.fromkeys(tag.strip() for tag in values))
        if any(not tag or len(tag) > 20 for tag in cleaned):
            raise ValueError("标签须为1至20字，最多8个")
        return cleaned


@router.patch("/books/{book_id}")
def book_update(book_id: str, data: Metadata, user=Depends(current_user), db=Depends(get_db)):
    book = owned(db, Book, book_id, user.id, lock=True)
    if book.version != data.expectedVersion:
        raise Problem("REVISION_CONFLICT", "书籍信息已更新", 409)
    if data.status not in (None, "active", "archived"):
        raise Problem("STATUS_INVALID", "状态无效")
    book.title, book.status = data.title or book.title, data.status or book.status
    if data.favorite is not None:
        book.favorite = data.favorite
    if data.tags is not None:
        book.tags = data.tags
    book.version += 1
    db.commit()
    return book_result(db, book)


@router.get("/books/{book_id}/branches")
def branches(book_id: str, cursor: str | None = None, limit: int = 20, status: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, book_id, user.id)
    filters = [Branch.book_id == book_id]
    if status:
        filters.append(Branch.status == status)
    rows, next_cursor = paginate(db, Branch, user.id, cursor, limit, filters)
    items = []
    for branch in rows:
        progress = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branch.id).order_by(Progress.updated_at.desc()).limit(1))
        items.append({"id": branch.id, "name": branch.name, "status": branch.status, "version": branch.version, "headRevisionId": branch.head_revision_id,
           "parentBranchId": branch.parent_branch_id, "forkAnchor": branch.fork_anchor, "forkLocation": anchor_label(db, branch.fork_anchor, user.id), "originOperationId": branch.origin_operation_id,
           "lastLocation": anchor_label(db, progress.anchor, user.id) if progress else None,
           "lastProgress": {"revisionId": progress.revision_id, "anchor": progress.anchor} if progress else None})
    return {"items": items, "nextCursor": next_cursor}


@router.patch("/branches/{branch_id}")
def branch_update(branch_id: str, data: Metadata, user=Depends(current_user), db=Depends(get_db)):
    branch = owned(db, Branch, branch_id, user.id, lock=True)
    book = owned(db, Book, branch.book_id, user.id)
    if branch.version != data.expectedVersion:
        raise Problem("REVISION_CONFLICT", "故事线信息已更新", 409)
    if data.status not in (None, "active", "archived") or (data.status == "archived" and is_import_root(db, book, branch)):
        raise Problem("BRANCH_STATUS_INVALID", "原文入口必须保留")
    branch.name, branch.status = data.name or branch.name, data.status or branch.status
    branch.version += 1
    db.commit()
    return {"id": branch.id, "name": branch.name, "status": branch.status, "version": branch.version}


def get_revision(db, branch_id, revision_id, owner):
    branch = owned(db, Branch, branch_id, owner)
    revision = owned(db, Revision, revision_id or branch.head_revision_id, owner)
    if revision.branch_id != branch.id:
        raise Problem("ANCHOR_INVALID", "修订不属于当前故事线")
    return branch, revision


@router.get("/branches/{branch_id}/chapters")
def chapters(branch_id: str, revisionId: str | None = None, cursor: str | None = None, limit: int = 100, user=Depends(current_user), db=Depends(get_db)):
    branch, revision = get_revision(db, branch_id, revisionId, user.id)
    progress = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branch.id, Progress.revision_id == revision.id))
    boundary = anchor_order(revision, progress.max_observed_anchor)[0] if progress else -1
    start = 0
    if cursor:
        start = next((i + 1 for i, c in enumerate(revision.manifest) if c["id"] == cursor), -1)
        if start < 0:
            raise Problem("CURSOR_INVALID", "目录游标已失效")
    limit = min(100, max(1, limit))
    subset = revision.manifest[start:start + limit]
    return {"revisionId": revision.id, "branchId": branch.id, "items": [{"id": c["id"], "title": c["title"] if start + i <= boundary else f"第 {start + i + 1} 章", "observed": start + i <= boundary, "blockCount": len(c["blocks"])} for i, c in enumerate(subset)],
            "nextCursor": subset[-1]["id"] if start + limit < len(revision.manifest) else None}


@router.get("/branches/{branch_id}/chapters/{chapter_id}")
def chapter_text(branch_id: str, chapter_id: str, revisionId: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    branch, revision = get_revision(db, branch_id, revisionId, user.id)
    chapter = next((c for c in revision.manifest if c["id"] == chapter_id), None)
    if chapter is None:
        raise Problem("RESOURCE_NOT_FOUND", "章节不存在", 404)
    # get_revision holds the book lifecycle lock. Fetch this chapter together,
    # retaining owner/book scope and manifest order without one lock/query per
    # paragraph (a long chapter otherwise makes hundreds of round trips).
    by_id = {block.id: block for block in db.scalars(select(ContentBlock).where(
        ContentBlock.owner_id == user.id, ContentBlock.book_id == branch.book_id,
        ContentBlock.id.in_(chapter["blocks"])).execution_options(populate_existing=True))}
    if any(block_id not in by_id for block_id in chapter["blocks"]):
        raise Problem("RESOURCE_NOT_FOUND", "内容不存在或不可访问", 404)
    blocks = [by_id[block_id] for block_id in chapter["blocks"]]
    return {"id": chapter_id, "title": chapter["title"], "revisionId": revision.id, "contentHash": digest(canonical([b.content_hash for b in blocks])),
           "blocks": [{"id": b.id, "text": b.text, "html": b.html, "source": b.source, "contentHash": b.content_hash,
              "startAnchor": make_anchor(branch.book_id, branch.id, revision.id, b),
              "endAnchor": make_anchor(branch.book_id, branch.id, revision.id, b, utf16_len(b.text))} for b in blocks]}


@router.get("/books/{book_id}/progress")
def progress_read(book_id: str, branchId: str, revisionId: str, user=Depends(current_user), db=Depends(get_db)):
    branch, revision = get_revision(db, branchId, revisionId, user.id)
    if branch.book_id != book_id:
        raise Problem("RESOURCE_NOT_FOUND", "位置不存在", 404)
    row = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branchId, Progress.revision_id == revisionId))
    return progress_result(row) if row else {"version": 0, "anchor": None, "maxObservedAnchor": None}


def progress_result(row):
    return {"version": row.version, "anchor": row.anchor, "maxObservedAnchor": row.max_observed_anchor, "revisionId": row.revision_id}


@router.put("/books/{book_id}/progress")
def progress_write(book_id: str, data: ProgressInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    anchor = data.anchor.model_dump()
    branch, revision, block = validate_anchor(db, anchor, user.id)
    if book_id != branch.book_id or data.branchId != branch.id or data.revisionId != revision.id or data.deviceId != request.state.session.id:
        raise Problem("ANCHOR_INVALID", "阅读位置关联无效")
    from sqlalchemy import text
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(digest(user.id + branch.id + revision.id)[:15], 16)})
    row = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branch.id, Progress.revision_id == revision.id).with_for_update())
    if row and data.clientSeq <= row.device_sequences.get(data.deviceId, 0):
        return progress_result(row)
    if data.expectedProgressVersion != (row.version if row else 0):
        raise Problem("PROGRESS_CONFLICT", "另一设备已保存新位置，请选择使用哪一处", 409, {"saved": progress_result(row) if row else None, "localAnchor": anchor})
    if not row:
        row = Progress(owner_id=user.id, book_id=book_id, branch_id=branch.id, revision_id=revision.id, anchor=anchor, max_observed_anchor=anchor, device_sequences={})
        db.add(row)
        db.flush()
    else:
        row.version += 1
        row.anchor = anchor
        if anchor_order(revision, anchor) > anchor_order(revision, row.max_observed_anchor):
            row.max_observed_anchor = anchor
    row.device_sequences = {**row.device_sequences, data.deviceId: data.clientSeq}
    row.updated_at = now()
    db.commit()
    return progress_result(row)


class BookmarkInput(Strict):
    anchor: Anchor
    note: str = Field(default="", max_length=2000)


@router.post("/books/{book_id}/bookmarks", status_code=201)
def bookmark_create(book_id: str, data: BookmarkInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, book_id, user.id)
    anchor = data.anchor.model_dump()
    branch, revision, block = validate_anchor(db, anchor, user.id)
    if branch.book_id != book_id:
        raise Problem("ANCHOR_INVALID", "书签关联无效")
    def create():
        row = Bookmark(owner_id=user.id, book_id=book_id, branch_id=branch.id, revision_id=revision.id, anchor=anchor, note=data.note)
        db.add(row)
        db.flush()
        return {"id": row.id, "anchor": anchor, "note": data.note, "status": "valid", "version": row.version}
    return idempotent(db, user.id, "bookmark:" + book_id, request.headers.get("idempotency-key"), data.model_dump(), create)


@router.get("/books/{book_id}/bookmarks")
def bookmarks(book_id: str, branchId: str, revisionId: str, cursor: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    branch, revision = get_revision(db, branchId, revisionId, user.id)
    if branch.book_id != book_id:
        raise Problem("ANCHOR_INVALID", "书签关联无效")
    rows, next_cursor = paginate(db, Bookmark, user.id, cursor, filters=(Bookmark.book_id == book_id, Bookmark.branch_id == branchId, Bookmark.revision_id == revisionId, Bookmark.status != 'deleted'))
    return {"items": [{"id": r.id, "anchor": r.anchor, "note": r.note, "status": r.status, "version": r.version} for r in rows], "nextCursor": next_cursor}


class BookmarkPatch(Strict):
    expectedVersion: int = Field(ge=1, strict=True)
    note: str = Field(max_length=2000)


@router.get("/books/{book_id}/bookmarks/{bookmark_id}")
def bookmark_read(book_id: str, bookmark_id: str, user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, book_id, user.id)
    row = owned(db, Bookmark, bookmark_id, user.id)
    if row.book_id != book_id:
        raise Problem("RESOURCE_NOT_FOUND", "书签不存在", 404)
    if row.status == 'deleted':
        raise Problem('BOOKMARK_DELETED', '此笔记已移除', 404, {'bookId': book_id, 'bookmarkId': bookmark_id})
    return {"id": row.id, "anchor": row.anchor, "note": row.note, "status": row.status, "version": row.version}


@router.patch("/books/{book_id}/bookmarks/{bookmark_id}")
def bookmark_update(book_id: str, bookmark_id: str, data: BookmarkPatch, user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, book_id, user.id)
    row = owned(db, Bookmark, bookmark_id, user.id, lock=True)
    if row.book_id != book_id:
        raise Problem("RESOURCE_NOT_FOUND", "书签不存在", 404)
    if row.status == 'deleted':
        raise Problem('BOOKMARK_DELETED', '此笔记已移除', 404, {'bookId': book_id, 'bookmarkId': bookmark_id})
    if row.version != data.expectedVersion:
        raise Problem("NOTE_CONFLICT", "笔记已在另一处更新，请重新打开后编辑", 409)
    row.note = data.note
    row.version += 1
    db.commit()
    return {"id": row.id, "anchor": row.anchor, "note": row.note, "status": row.status, "version": row.version}


class BookmarkDelete(Strict):
    expectedVersion: int = Field(ge=1, strict=True)


@router.delete("/books/{book_id}/bookmarks/{bookmark_id}")
def bookmark_delete(book_id: str, bookmark_id: str, request: Request, data: BookmarkDelete | None = None,
        user=Depends(current_user), db=Depends(get_db)):
    # The book lifecycle always precedes the bookmark lock, including source
    # deletion and preserved publication migration. Missing-body old callers
    # still receive ownership404 before request-shape errors.
    owned(db, Book, book_id, user.id)
    row = owned(db, Bookmark, bookmark_id, user.id, lock=True)
    if row.book_id != book_id:
        raise Problem("RESOURCE_NOT_FOUND", "书签不存在", 404)
    if data is None:
        raise Problem('INVALID_REQUEST', '删除笔记需要当前版本', details={'fields': ['body.expectedVersion']})
    def remove():
        if row.status == 'deleted':
            raise Problem('BOOKMARK_DELETED', '此笔记已移除', 404, {'bookId': book_id, 'bookmarkId': bookmark_id})
        if row.version != data.expectedVersion:
            raise Problem('NOTE_CONFLICT', '笔记已在另一处更新，请载入最新信息后再删除', 409,
                {'currentVersion': row.version})
        row.status = 'deleted'; row.version += 1
        row.deleted_at = now(); row.deletion_id = uid()
        row.note = ''; row.anchor = {}; row.branch_id = ''; row.revision_id = ''
        receipt = {'deleted': True, 'bookmarkId': bookmark_id, 'bookId': book_id, 'version': row.version,
            'receiptId': row.deletion_id, 'deletedAt': row.deleted_at.isoformat()}
        for prior in db.scalars(select(Idempotency).where(Idempotency.owner_id == user.id,
                Idempotency.scope == 'bookmark:' + book_id, Idempotency.result['id'].astext == bookmark_id)):
            prior.result = receipt
        db.flush()
        return receipt
    return idempotent(db, user.id, 'bookmark-delete:' + book_id + ':' + bookmark_id,
        request.headers.get('idempotency-key'), data.model_dump(), remove)


@router.get('/books/{book_id}/bookmarks/{bookmark_id}/deletion')
def bookmark_deletion(book_id: str, bookmark_id: str, requestId: str = Query(min_length=1, max_length=160),
        user=Depends(current_user), db=Depends(get_db)):
    owned(db, Book, book_id, user.id)
    row = owned(db, Bookmark, bookmark_id, user.id)
    if row.book_id != book_id:
        raise Problem('RESOURCE_NOT_FOUND', '书签不存在', 404)
    receipt = db.scalar(select(Idempotency).where(Idempotency.owner_id == user.id,
        Idempotency.scope == 'bookmark-delete:' + book_id + ':' + bookmark_id, Idempotency.key == requestId))
    if not receipt:
        return {'found': False}
    return {'found': True, 'receipt': receipt.result}


@router.get("/books/{book_id}/search")
def search(book_id: str, branchId: str, revisionId: str, query: str, cursor: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    branch, revision = get_revision(db, branchId, revisionId, user.id)
    if branch.book_id != book_id or not 1 <= len(query) <= 100:
        raise Problem("SEARCH_INVALID", "搜索范围或关键词无效")
    progress = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branch.id, Progress.revision_id == revision.id))
    if not progress:
        return {"items": [], "nextCursor": None}
    visible = prefix_blocks(db, revision, progress.max_observed_anchor, user.id)
    matches = []
    after = cursor is None
    for b in visible:
        if not after:
            after = b["blockId"] == cursor
            continue
        index = b["text"].find(query)
        if index >= 0:
            block = owned(db, ContentBlock, b["blockId"], user.id)
            start = utf16_len(b["text"][:index])
            matches.append({"snippet": b["text"][max(0, index - 24):index + len(query) + 48], "anchor": make_anchor(book_id, branch.id, revision.id, block, start, start + utf16_len(query))})
        if len(matches) == 21:
            break
    return {"items": matches[:20], "nextCursor": matches[19]["anchor"]["blockId"] if len(matches) > 20 else None}


@router.get("/books/{book_id}/assets/{asset_id}")
def asset(book_id: str, asset_id: str, user=Depends(current_user), db=Depends(get_db)):
    from fastapi.responses import FileResponse
    book = owned(db, Book, book_id, user.id)
    source = owned(db, SourceDocument, book.source_id, user.id)
    name = source.source_map.get("assets", {}).get(asset_id)
    if not name:
        raise Problem("RESOURCE_NOT_FOUND", "图片不存在", 404)
    return FileResponse(private_path(user.id, name), media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/candidates/{candidate_id}")
def candidate_read(candidate_id: str, user=Depends(current_user), db=Depends(get_db)):
    candidate = owned(db, Candidate, candidate_id, user.id)
    task = owned(db, Task, candidate.task_id, user.id)
    visible = candidate.persistence_state == "saved" and bool(candidate.text.strip()) and candidate.content_hash == digest(candidate.text)
    acceptable = candidate_acceptable(candidate, task) and candidate.status in {"pending", "accepted"}
    return {"id": candidate.id, "taskId": candidate.task_id, "text": candidate.text if visible else None,
       "chapters": candidate_chapters(candidate, task) if acceptable else None,
       "canAccept": acceptable, "reviewWarnings": saved_warnings(task),
       "requestedChapters": task.payload.get("chapterCount", 1), "completedChapters": task.result.get("completedChapters", 1 if visible else 0),
       "persistenceState": candidate.persistence_state, "validationStatus": candidate.validation_status, "outcome": candidate.outcome,
       "status": candidate.status, "partial": candidate.partial, "acceptedRevisionId": candidate.accepted_revision_id, "operation": task.payload["operation"],
       "anchor": task.payload["anchor"], "anchorLocation": anchor_label(db, task.payload["anchor"], user.id), "target": task.payload["target"], "baseRevisionId": task.payload["anchor"]["revisionId"], "acceptResult": candidate.accept_result}


def candidate_chapters(candidate, task):
    chapters = task.checkpoint.get("chapters")
    requested = task.payload.get("chapterCount", 1)
    if chapters is None and requested == 1:
        # Candidates produced by older versions still accept as one chapter.
        chapters = [{"index": 1, "title": "续写 · 第 1 章", "text": candidate.text, "contentHash": digest(candidate.text)}]
    if not isinstance(chapters, list) or len(chapters) != requested or candidate.content_hash != digest(candidate.text):
        raise Problem("CANDIDATE_INVALID", "保存的章节不完整或校验不一致", 409)
    for index, chapter in enumerate(chapters, 1):
        if (not isinstance(chapter, dict) or chapter.get("index") != index or not isinstance(chapter.get("title"), str) or not chapter["title"].strip()
                or len(chapter["title"]) > 100 or not isinstance(chapter.get("text"), str)
                or not chapter["text"].strip() or chapter.get("contentHash") != digest(chapter["text"])):
            raise Problem("CANDIDATE_INVALID", "保存的章节不完整或校验不一致", 409)
    combined = chapters[0]["text"] if requested == 1 else "\n\n".join(c["title"] + "\n\n" + c["text"] for c in chapters)
    if combined != candidate.text:
        raise Problem("CANDIDATE_INVALID", "候选正文与章节检查点不一致", 409)
    return chapters


@router.post("/candidates/{candidate_id}/discard")
def discard(candidate_id: str, user=Depends(current_user), db=Depends(get_db)):
    row = owned(db, Candidate, candidate_id, user.id, lock=True)
    if row.accepted_revision_id:
        raise Problem("CANDIDATE_ACCEPTED", "候选已接受", 409)
    row.status = "discarded"
    db.commit()
    return {"discarded": True}


@router.post("/candidates/{candidate_id}/accept")
def accept(candidate_id: str, data: AcceptInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def commit():
        candidate = owned(db, Candidate, candidate_id, user.id, lock=True)
        if candidate.accepted_revision_id:
            return candidate.accept_result
        task = owned(db, Task, candidate.task_id, user.id)
        if candidate.status != "pending" or not candidate_acceptable(candidate, task):
            raise Problem("CANDIDATE_UNACCEPTABLE", "只有正文完整且已保存的章节可以加入故事线")
        generated_chapters = candidate_chapters(candidate, task)
        anchor = task.payload["anchor"]
        source = owned(db, Branch, anchor["branchId"], user.id, lock=True)
        _, base, anchor_block = validate_anchor(db, anchor, user.id, paragraph=True)
        book = owned(db, Book, source.book_id, user.id)
        if source.status != "active" or book.status != "active":
            raise Problem("BRANCH_ARCHIVED", "请先恢复归档内容", 409)
        if data.expectedHeadRevisionId != base.id:
            raise Problem("REVISION_CONFLICT", "提交基线与候选不一致", 409)
        if data.sourcePolicy == "require_unchanged_head" and source.head_revision_id != base.id:
            raise Problem("REVISION_CONFLICT", "故事线已更新，候选仍保留", 409, {"currentHeadRevisionId": source.head_revision_id, "canForkPinnedRevision": True})
        append = data.target.mode == "append_existing"
        if data.sourcePolicy == "fork_pinned_revision" and append:
            raise Problem("REVISION_CONFLICT", "旧版本仅能创建新故事线", 409)
        if append and (task.payload["operation"] != "continue_at_tail" or is_import_root(db, book, source) or not is_tail(base, anchor, anchor_block) or data.target.branchId != source.id):
            raise Problem("TARGET_INVALID", "此候选不能追加至目标故事线")
        target = source if append else Branch(id=uid(), owner_id=user.id, book_id=book.id, parent_branch_id=source.id,
                    fork_anchor=anchor, name=data.target.name, head_revision_id=uid(), origin_operation_id=task.id)
        if not append:
            db.add(target)
            db.flush()
        manifest = []
        stopped = False
        for c in base.manifest:
            keep = []
            for bid in c["blocks"]:
                if bid == anchor_block.id:
                    if anchor["end"] == utf16_len(anchor_block.text):
                        keep.append(bid)
                    stopped = True
                    break
                keep.append(bid)
            if keep:
                manifest.append({**c, "blocks": keep})
            if stopped:
                break
        prefix_ids = {bid for c in manifest for bid in c["blocks"]}
        prefix_boundary = manifest[-1]["blocks"][-1] if manifest else None
        new_blocks = []
        for chapter in generated_chapters:
            chapter_id, chapter_blocks = uid(), []
            for paragraph in chapter["text"].splitlines():
                if paragraph.strip():
                    block = ContentBlock(id=uid(), owner_id=user.id, book_id=book.id, chapter_id=chapter_id, text=paragraph, html=None, content_hash=digest(paragraph), source="ai")
                    db.add(block)
                    chapter_blocks.append(block)
                    new_blocks.append(block)
            manifest.append({"id": chapter_id, "title": chapter["title"], "blocks": [b.id for b in chapter_blocks]})
        revision = Revision(id=uid(), owner_id=user.id, branch_id=target.id, parent_revision_id=base.id,
            sequence=base.sequence + 1 if append else 1, manifest=manifest, content_hash=digest(canonical(manifest)),
            provenance={"type": "ai", "taskId": task.id, "candidateId": candidate.id, "profileId": task.payload["profileId"], "contextHash": task.checkpoint.get("contextHash"), "instruction": task.payload.get("instruction"), "operation": task.payload["operation"], "chapterCount": len(generated_chapters),
                        "reviewPolicy": task.checkpoint.get("generationReviewPolicy"), "reviewWarnings": saved_warnings(task)})
        db.add(revision)
        target.head_revision_id = revision.id
        target.version += 1
        db.flush()
        result = {"candidateId": candidate.id, "branchId": target.id, "revisionId": revision.id, "created": not append, "committed": True,
            "readingAnchor": make_anchor(book.id, target.id, revision.id, new_blocks[0]), "chapterCount": len(generated_chapters),
            "chapterIds": [c["id"] for c in manifest[-len(generated_chapters):]]}
        candidate.accepted_revision_id, candidate.accept_result, candidate.status = revision.id, result, "accepted"
        # Inherit only verified unchanged prefix observations. Newly generated blocks stay locked.
        previous = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == source.id, Progress.revision_id == base.id))
        if previous and manifest[0]["blocks"]:
            permitted = prefix_ids
            old = previous.max_observed_anchor
            boundary_block = owned(db, ContentBlock, old["blockId"], user.id) if old["blockId"] in permitted else (owned(db, ContentBlock, prefix_boundary, user.id) if prefix_boundary else None)
            if boundary_block:
                pos = old["end"] if old["blockId"] in permitted else utf16_len(boundary_block.text)
                inherited = make_anchor(book.id, target.id, revision.id, boundary_block, pos)
                db.add(Progress(owner_id=user.id, book_id=book.id, branch_id=target.id, revision_id=revision.id, anchor=inherited, max_observed_anchor=inherited, device_sequences={}))
        db.add(Outbox(owner_id=user.id, event_type="story_committed", payload={"branchId": target.id, "revisionId": revision.id, "baseRevisionId": base.id}))
        return result
    return idempotent(db, user.id, f"accept:{candidate_id}", request.headers.get("idempotency-key"), data.model_dump(), commit)
