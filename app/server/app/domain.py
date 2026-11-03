import hashlib
import json
from datetime import timedelta
from sqlalchemy import select, text
from .models import Branch, Revision, ContentBlock, Book, Idempotency, now
from .errors import Problem


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def owned(db, cls, object_id, owner_id, lock=False):
    # All reader writers enter the same book lifecycle before row locks. The
    # lookup is authorization-only and is re-read after the advisory lock.
    from .models import Task, Candidate, RoleSession, KnowledgeSnapshot, AnalysisChunk
    from .book_deletion import lifecycle, task_book, require_existing
    if cls is Book:
        return require_existing(db, owner_id, object_id)
    if cls in (Branch, Revision, ContentBlock, Task, Candidate, RoleSession):
        acquired = set()
        while True:
            probe = db.scalar(select(cls).where(cls.id == object_id, cls.owner_id == owner_id).execution_options(populate_existing=True))
            book_id = None
            if probe is None:
                break
            book_id = getattr(probe, "book_id", None)
            if cls is Revision:
                branch = db.scalar(select(Branch).where(Branch.id == probe.branch_id).execution_options(populate_existing=True))
                book_id = branch.book_id if branch and branch.owner_id == owner_id else None
            elif cls is Task:
                book_id = task_book(db, probe)
            elif cls is Candidate:
                book_id = task_book(db, db.scalar(select(Task).where(Task.id == probe.task_id).execution_options(populate_existing=True)))
            if not book_id or book_id in acquired:
                break
            lifecycle(db, owner_id, book_id)
            acquired.add(book_id)
            # A committed detach may change scope while we wait. Refresh it
            # without a row lock and acquire its new lifecycle before FOR UPDATE.
    q = select(cls).where(cls.id == object_id, cls.owner_id == owner_id)
    if lock:
        q = q.with_for_update()
    row = db.scalar(q.execution_options(populate_existing=True))
    if row is None:
        raise Problem("RESOURCE_NOT_FOUND", "内容不存在或不可访问", 404)
    return row


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def anchor_label(db, anchor, owner):
    if not anchor:
        return None
    revision = owned(db, Revision, anchor["revisionId"], owner)
    for ci, chapter in enumerate(revision.manifest):
        if chapter["id"] == anchor["chapterId"] and anchor["blockId"] in chapter["blocks"]:
            pi = chapter["blocks"].index(anchor["blockId"])
            return f"第 {ci+1} 章 · 第 {pi+1} 段" + ("之前" if anchor["end"] == 0 else "末尾")
    return "位置已失效，请重新选择"


def utf16_slice(text, start, end):
    if start < 0 or end < start or end > utf16_len(text):
        raise Problem("ANCHOR_INVALID", "阅读位置超出文本范围")
    try:
        return text.encode("utf-16-le")[start * 2:end * 2].decode("utf-16-le")
    except UnicodeError:
        raise Problem("ANCHOR_INVALID", "阅读位置切断了 Unicode 字符")


def boundary_hash(text, revision_id, block_id, position):
    utf16_slice(text, 0, position)
    left, right = max(0, position - 32), min(utf16_len(text), position + 32)
    try:
        before = utf16_slice(text, left, position)
    except Problem:
        before = utf16_slice(text, left - 1, position)
    try:
        after = utf16_slice(text, position, right)
    except Problem:
        after = utf16_slice(text, position, right + 1)
    return digest(json.dumps(["anchor-v1", revision_id, block_id, position, before, after], ensure_ascii=False, separators=(",", ":")))


def make_anchor(book_id, branch_id, revision_id, block, start=0, end=None):
    end = start if end is None else end
    return {"bookId": book_id, "branchId": branch_id, "revisionId": revision_id, "chapterId": block.chapter_id,
            "blockId": block.id, "start": start, "end": end, "offsetUnit": "utf16",
            "quoteHash": boundary_hash(block.text, revision_id, block.id, start) if start == end else digest(utf16_slice(block.text, start, end))}


def validate_anchor(db, anchor, owner_id, paragraph=False):
    required = {"bookId", "branchId", "revisionId", "chapterId", "blockId", "start", "end", "offsetUnit", "quoteHash"}
    if set(anchor) != required or anchor["offsetUnit"] != "utf16" or type(anchor["start"]) is not int or type(anchor["end"]) is not int:
        raise Problem("ANCHOR_INVALID", "阅读位置格式无效")
    branch = owned(db, Branch, anchor["branchId"], owner_id)
    revision = owned(db, Revision, anchor["revisionId"], owner_id)
    block = owned(db, ContentBlock, anchor["blockId"], owner_id)
    if branch.book_id != anchor["bookId"] or revision.branch_id != branch.id or block.book_id != branch.book_id or block.chapter_id != anchor["chapterId"]:
        raise Problem("ANCHOR_INVALID", "阅读位置与故事线不匹配")
    if not any(c["id"] == anchor["chapterId"] and block.id in c["blocks"] for c in revision.manifest):
        raise Problem("ANCHOR_INVALID", "此段落不在指定修订中")
    selection = utf16_slice(block.text, anchor["start"], anchor["end"])
    expected = boundary_hash(block.text, revision.id, block.id, anchor["start"]) if anchor["start"] == anchor["end"] else digest(selection)
    if expected != anchor["quoteHash"]:
        raise Problem("ANCHOR_INVALID", "阅读位置校验失败")
    if paragraph and (anchor["start"] != anchor["end"] or anchor["end"] not in (0, utf16_len(block.text))):
        raise Problem("ANCHOR_INVALID", "请在段落边界开始")
    return branch, revision, block


def anchor_order(revision, anchor):
    for ci, chapter in enumerate(revision.manifest):
        if anchor["blockId"] in chapter["blocks"]:
            return ci, chapter["blocks"].index(anchor["blockId"]), anchor["end"]
    raise Problem("ANCHOR_INVALID", "位置已失效")


def prefix_blocks(db, revision, anchor, owner_id):
    result = []
    for chapter in revision.manifest:
        for block_id in chapter["blocks"]:
            block = owned(db, ContentBlock, block_id, owner_id)
            if block_id == anchor["blockId"]:
                part = utf16_slice(block.text, 0, anchor["end"])
                if part:
                    result.append({"blockId": block.id, "chapterId": chapter["id"], "text": part, "source": block.source})
                return result
            result.append({"blockId": block.id, "chapterId": chapter["id"], "text": block.text, "source": block.source})
    raise Problem("ANCHOR_INVALID", "无法读取该位置")


def is_tail(revision, anchor, block):
    return revision.manifest[-1]["blocks"][-1] == block.id and anchor["end"] == utf16_len(block.text)


def is_import_root(db, book, branch):
    """The immutable imported original differs from an editable AI story root."""
    if branch.id != book.original_branch_id:
        return False
    from .models import SourceDocument
    source = db.scalar(select(SourceDocument).where(SourceDocument.owner_id == book.owner_id, SourceDocument.book_id == book.id))
    return not source or source.source_map.get("kind") not in {"world_publication", "detached_publication"}


def idempotent(db, owner_id, scope, key, payload, action):
    if not key or len(key) > 160:
        raise Problem("IDEMPOTENCY_KEY_REQUIRED", "请求缺少有效幂等键")
    lock_id = int(digest(owner_id + scope + key)[:15], 16)
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": lock_id})
    hashed = digest(canonical(payload))
    existing = db.scalar(select(Idempotency).where(Idempotency.owner_id == owner_id, Idempotency.scope == scope, Idempotency.key == key))
    if existing:
        if existing.payload_hash != hashed:
            raise Problem("IDEMPOTENCY_CONFLICT", "同一请求标识对应了不同内容", 409)
        result = existing.result
        if isinstance(result, dict) and result.get('deleted') and result.get('bookmarkId') and scope.startswith('bookmark:'):
            raise Problem('BOOKMARK_DELETED', '此笔记已移除', 404,
                {'bookId': result['bookId'], 'bookmarkId': result['bookmarkId']})
        if isinstance(result, dict) and result.get("deleted") and result.get("bookId") and not scope.startswith(("book-delete:", "bookmark-delete:")):
            raise Problem("BOOK_DELETED", "来源小说已移除", 404)
        return result
    result = action()
    db.add(Idempotency(owner_id=owner_id, scope=scope, key=key, payload_hash=hashed, result=result, expires_at=now() + timedelta(days=7)))
    db.commit()
    return result
