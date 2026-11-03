"""Owner-authorized aggregation; each mode has its own navigation position.

Books and worlds stay in their existing schemas. Reading progress, world heads,
knowledge and camera versions are never changed by these endpoints.
"""
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select, text, tuple_

from .config import settings
from .db import get_db
from .domain import digest, make_anchor, owned, validate_anchor
from .errors import Problem
from .models import Book, Branch, ContentBlock, Progress, ResumeTarget, Revision, RoleMessage, RoleSession, now
from .schemas import Anchor, Strict
from .security import current_user
from .world.models import World, WorldCheckpoint, WorldLine, WorldPublication
from .world.service import checked_checkpoint, require_line, source_kind
from .world.state import ancestry

router = APIRouter()
ID_PATTERN = r"^[a-f0-9]{32}$"
STORY_PATTERN = r"^(book|world)-([a-f0-9]{32})$"
MODES = ("read", "play", "chat")


class ContextAnchor(Anchor):
    start: int = Field(ge=0, strict=True)
    end: int = Field(ge=0, strict=True)


class StoryContext(Strict):
    storyId: str = Field(pattern=STORY_PATTERN)
    mode: Literal["read", "chat", "play"]
    bookId: str | None = Field(default=None, pattern=ID_PATTERN)
    readerBranchId: str | None = Field(default=None, pattern=ID_PATTERN)
    worldId: str | None = Field(default=None, pattern=ID_PATTERN)
    worldLineId: str | None = Field(default=None, pattern=ID_PATTERN)
    anchor: ContextAnchor | None = None
    checkpointId: str | None = Field(default=None, pattern=ID_PATTERN)
    characterId: str | None = Field(default=None, min_length=1, max_length=100)
    sessionId: str | None = Field(default=None, pattern=ID_PATTERN)
    perspective: Literal["reader", "actor"]
    entryOrigin: Literal["library", "worlds", "reader", "graph", "conversation"]

    @model_validator(mode="after")
    def coherent_mode(self):
        world = any((self.worldId, self.worldLineId, self.checkpointId))
        reader = any((self.bookId, self.readerBranchId, self.anchor))
        if world and reader:
            raise ValueError("Reader and world locations are separate contexts")
        if self.mode == "play":
            if self.perspective != "actor" or not all((self.worldId, self.worldLineId, self.checkpointId, self.characterId)) or self.sessionId:
                raise ValueError("Play requires an actor at an explicit world checkpoint")
        else:
            if self.perspective != "reader":
                raise ValueError("Reading and conversation use the reader perspective")
            if self.mode == "chat":
                if not all((self.bookId, self.readerBranchId, self.anchor, self.characterId, self.sessionId)) or world:
                    raise ValueError("Chat requires a pinned reader session")
            elif self.sessionId or not (world or reader):
                raise ValueError("Read requires an explicit reading location")
            elif world and not all((self.worldId, self.worldLineId, self.checkpointId)):
                raise ValueError("World reading requires a line and checkpoint")
            elif reader and (not all((self.bookId, self.readerBranchId, self.anchor)) or self.characterId):
                raise ValueError("Book reading requires a complete anchor")
        return self


class ResumeInput(Strict):
    expectedVersion: int = Field(ge=0, strict=True)
    context: StoryContext


def story_root(db, owner, story_id, *, lock=False):
    match = re.fullmatch(STORY_PATTERN, story_id)
    if not match:
        raise Problem("RESOURCE_NOT_FOUND", "故事不存在或不可访问", 404)
    kind, object_id = match.groups()
    if kind == "world" and not settings.interactive_world_enabled:
        raise Problem("RESOURCE_NOT_FOUND", "故事不存在或不可访问", 404)
    row = owned(db, Book if kind == "book" else World, object_id, owner, lock=lock)
    if row.status == "deleted":
        raise Problem("RESOURCE_NOT_FOUND", "故事不存在或不可访问", 404)
    return kind, row


def publication_book_ids(db, world, owner):
    # A publication survives world removal in reader provenance. Do not rely on
    # a cross-schema foreign key or match unrelated stories by their titles.
    ids = set(db.scalars(select(WorldPublication.book_id).where(
        WorldPublication.owner_id == owner, WorldPublication.world_id == world.id)))
    ids.update(db.scalars(select(Branch.book_id).join(Revision, Revision.branch_id == Branch.id).where(
        Branch.owner_id == owner, Revision.owner_id == owner,
        Revision.provenance["operation"].astext == "world_publication",
        Revision.provenance["worldId"].astext == world.id)))
    if world.source_book_id:
        ids.add(world.source_book_id)
    return ids


def related_worlds(db, kind, root, owner):
    if not settings.interactive_world_enabled:
        return []
    if kind == "world":
        return [root]
    publications = set(db.scalars(select(WorldPublication.world_id).where(
        WorldPublication.owner_id == owner, WorldPublication.book_id == root.id)))
    publications.update(db.scalars(select(Revision.provenance["worldId"].astext).join(
        Branch, Revision.branch_id == Branch.id).where(Revision.owner_id == owner,
        Branch.owner_id == owner, Branch.book_id == root.id,
        Revision.provenance["operation"].astext == "world_publication")))
    query = select(World).where(World.owner_id == owner, World.status != "deleted",
        (World.source_book_id == root.id) | World.id.in_(publications))
    return list(db.scalars(query.order_by(World.updated_at.desc(), World.created_at.desc(), World.id.desc())))


def relevant_books(db, kind, root, owner):
    if kind == "book":
        return [root]
    ids = publication_book_ids(db, root, owner)
    return list(db.scalars(select(Book).where(Book.owner_id == owner, Book.id.in_(ids), Book.status != "deleted").order_by(Book.created_at.desc(), Book.id.desc())))


def publication_branches(db, owner, world):
    ids = set(db.scalars(select(WorldPublication.branch_id).where(
        WorldPublication.owner_id == owner, WorldPublication.world_id == world.id)))
    ids.update(db.scalars(select(Revision.branch_id).where(Revision.owner_id == owner,
        Revision.provenance["operation"].astext == "world_publication",
        Revision.provenance["worldId"].astext == world.id)))
    # Reader-only writing from a published snapshot remains in that reading
    # family, while an unrelated branch sharing the same source book does not.
    rows = list(db.scalars(select(Branch).where(Branch.owner_id == owner,
        Branch.book_id.in_(publication_book_ids(db, world, owner)))))
    # Another world's publication is a different family even when historical
    # same-book publications happen to have a parent in this family.
    foreign_roots = set(db.scalars(select(Revision.branch_id).where(Revision.owner_id == owner,
        Revision.provenance["operation"].astext == "world_publication",
        Revision.provenance["worldId"].astext != world.id)))
    foreign_roots.update(db.scalars(select(WorldPublication.branch_id).where(
        WorldPublication.owner_id == owner, WorldPublication.world_id != world.id)))
    previous = None
    while previous != len(ids):
        previous = len(ids)
        ids.update(branch.id for branch in rows if branch.parent_branch_id in ids and branch.id not in foreign_roots)
    return ids


def validate_context(db, owner, story_id, context, *, root=None):
    kind, obj = root or story_root(db, owner, story_id)
    if context.storyId != story_id:
        raise Problem("STORY_CONTEXT_INVALID", "上下文与故事入口不一致")
    value = context.model_dump(exclude_none=True)
    if context.worldId:
        if not settings.interactive_world_enabled:
            raise Problem("RESOURCE_NOT_FOUND", "内容不存在或不可访问", 404)
        world, line = require_line(db, owner, context.worldId, context.worldLineId)
        if (kind == "world" and world.id != obj.id) or (kind == "book" and obj.id not in publication_book_ids(db, world, owner)):
            raise Problem("RESOURCE_NOT_FOUND", "内容不存在或不可访问", 404)
        checkpoint = checked_checkpoint(db, owner, world.id, context.checkpointId)
        head = checked_checkpoint(db, owner, world.id, line.head_checkpoint_id)
        if checkpoint.id not in {c.id for c in ancestry(db, head, owner)}:
            raise Problem("RESOURCE_NOT_FOUND", "这个节点不在所选故事线中", 404)
        if context.characterId is not None and checkpoint.state.get("playerId") != context.characterId:
            raise Problem("STORY_CONTEXT_INVALID", "人物与所选历史时点不一致")
        if context.mode == "play" and (world.status != "active" or line.status != "active"):
            raise Problem("WORLD_ARCHIVED", "请先恢复这个世界或故事线", 409)
    else:
        book = owned(db, Book, context.bookId, owner)
        branch = owned(db, Branch, context.readerBranchId, owner)
        if (branch.book_id != book.id or (kind == "book" and book.id != obj.id) or
                (kind == "world" and branch.id not in publication_branches(db, owner, obj))):
            raise Problem("RESOURCE_NOT_FOUND", "内容不存在或不可访问", 404)
        anchor = context.anchor.model_dump()
        # Explicitly authorize every UUID before validating its relationships.
        owned(db, Revision, anchor["revisionId"], owner)
        owned(db, ContentBlock, anchor["blockId"], owner)
        if anchor["bookId"] != book.id or anchor["branchId"] != branch.id:
            raise Problem("STORY_CONTEXT_INVALID", "阅读位置与所选故事线不一致")
        validate_anchor(db, anchor, owner)
        if context.mode == "chat":
            session = owned(db, RoleSession, context.sessionId, owner)
            if (session.book_id != book.id or session.branch_id != branch.id or
                    session.pinned_revision_id != anchor["revisionId"] or session.pinned_anchor != anchor or
                    session.character_id != context.characterId or session.identity_mode != "reader_out_of_story"):
                raise Problem("STORY_CONTEXT_INVALID", "请恢复该会话固定的角色和阅读时点")
            if session.status != "active" or book.status != "active" or branch.status != "active":
                raise Problem("ROLE_UNAVAILABLE", "请先恢复这个故事或会话", 409)
            from .character_cards import ensure_session_scope
            ensure_session_scope(db, session, owner)
    return value


def valid_fallback(db, owner, story_id, candidate, instant, root):
    try:
        validated = validate_context(db, owner, story_id, StoryContext.model_validate(candidate), root=root)
    except (Problem, ValidationError):
        return None
    return validated, instant


def world_default_context(db, owner, story_id, world, *, mode="play"):
    if mode == "play" and world.status != "active":
        return None
    lines = list(db.scalars(select(WorldLine).where(WorldLine.owner_id == owner,
        WorldLine.world_id == world.id, WorldLine.status.in_(["active"] if mode == "play" else ["active", "archived"]))))
    # Prefer the existing entry pointer; then actual saved head time, not a camera
    # update or a speculative candidate checkpoint.
    ranked = []
    for line in lines:
        cp = db.scalar(select(WorldCheckpoint).where(WorldCheckpoint.owner_id == owner,
            WorldCheckpoint.world_id == world.id, WorldCheckpoint.id == line.head_checkpoint_id))
        if cp and cp.state.get("playerId"):
            ranked.append((line.id == world.active_line_id, cp.created_at, line.id, line, cp))
    for _, _, _, line, cp in sorted(ranked, key=lambda entry: entry[:3], reverse=True):
        selected = cp.id
        if mode == "read" and line.reading_position.get("checkpointId"):
            selected = line.reading_position["checkpointId"]
        candidate = {"storyId": story_id, "mode": mode, "worldId": world.id, "worldLineId": line.id,
            "checkpointId": selected, "perspective": "actor" if mode == "play" else "reader", "entryOrigin": "worlds"}
        if mode == "play":
            candidate["characterId"] = cp.state["playerId"]
        result = valid_fallback(db, owner, story_id, candidate, cp.created_at, ("world", world))
        if result:
            return result
        # A removed historical display position should still recover this head.
        candidate["checkpointId"] = cp.id
        result = valid_fallback(db, owner, story_id, candidate, cp.created_at, ("world", world))
        if result:
            return result
    return None


def read_activity(db, owner, story_id, root, books, worlds):
    """Real reading positions compete by their own time, never head/camera time."""
    kind, obj = root
    query = select(Progress).where(Progress.owner_id == owner, Progress.book_id.in_([b.id for b in books]))
    if kind == "world":
        query = query.where(Progress.branch_id.in_(publication_branches(db, owner, obj)))
    candidates = []
    for progress in db.scalars(query):
        value = {"storyId": story_id, "mode": "read", "bookId": progress.book_id, "readerBranchId": progress.branch_id,
            "anchor": progress.anchor, "perspective": "reader", "entryOrigin": "library"}
        result = valid_fallback(db, owner, story_id, value, progress.updated_at, root)
        if result:
            candidates.append(result)
    lines = db.scalars(select(WorldLine).where(WorldLine.owner_id == owner, WorldLine.world_id.in_([w.id for w in worlds]),
        WorldLine.status.in_(["active", "archived"])))
    for line in lines:
        position = line.reading_position
        if not position.get("checkpointId") or not isinstance(position.get("updatedAt"), str):
            continue  # Legacy positions remain fallbacks, with no invented time.
        try:
            instant = datetime.fromisoformat(position["updatedAt"])
            if instant.tzinfo is None or instant > now():
                continue
        except ValueError:
            continue
        value = {"storyId": story_id, "mode": "read", "worldId": line.world_id, "worldLineId": line.id,
            "checkpointId": position["checkpointId"], "perspective": "reader", "entryOrigin": "worlds"}
        result = valid_fallback(db, owner, story_id, value, instant, root)
        if result:
            candidates.append(result)
    return max(candidates, key=lambda candidate: candidate[1]) if candidates else None


def world_recent_instant(db, owner, world):
    story_id = "world-" + world.id
    books = relevant_books(db, "world", world, owner)
    roots = {story_id} | {"book-" + book.id for book in books}
    result = read_activity(db, owner, story_id, ("world", world), books, [world])
    rows = db.scalars(select(ResumeTarget).where(ResumeTarget.owner_id == owner,
        ResumeTarget.story_id.in_(roots)).order_by(ResumeTarget.updated_at.desc(), ResumeTarget.id.desc()))
    for row in rows:
        if row.context.get("mode") != row.mode:
            continue
        candidate = valid_fallback(db, owner, story_id, {**row.context, "storyId": story_id}, row.updated_at, ("world", world))
        if candidate:
            if result is None or candidate[1] > result[1]:
                result = candidate
            break
    return result[1] if result else world.created_at


def chat_activity(db, owner, story_id, root, books, *, include_unused=False):
    """Actual durable messages, including stopped prose, compete with resumes."""
    from sqlalchemy import func
    kind, obj = root
    durable = RoleMessage.status.in_(["saved", "review_warning", "interrupted", "stopped"])
    pending_user = (RoleMessage.status == "pending") & (RoleMessage.role == "user")
    latest = select(RoleMessage.session_id, RoleMessage.created_at).where(
        RoleMessage.owner_id == owner, durable | pending_user,
        RoleMessage.role.in_(["user", "assistant"]), RoleMessage.text.op("~")(r"\S")).subquery()
    last = func.max(latest.c.created_at)
    query = select(RoleSession, last).outerjoin(latest, latest.c.session_id == RoleSession.id).where(
        RoleSession.owner_id == owner, RoleSession.book_id.in_([book.id for book in books]),
        RoleSession.status == "active").group_by(RoleSession.id)
    if not include_unused:
        query = query.having(last.is_not(None))
    if kind == "world":
        query = query.where(RoleSession.branch_id.in_(publication_branches(db, owner, obj)))
    query = query.order_by(last.is_not(None).desc(),
        func.coalesce(last, RoleSession.created_at).desc(), RoleSession.id.desc())
    for session, instant in db.execute(query):
        candidate = {"storyId": story_id, "mode": "chat", "bookId": session.book_id,
            "readerBranchId": session.branch_id, "anchor": session.pinned_anchor,
            "characterId": session.character_id, "sessionId": session.id,
            "perspective": "reader", "entryOrigin": "conversation"}
        result = valid_fallback(db, owner, story_id, candidate, instant or session.created_at, root)
        if result:
            return result
    return None


def fallback(db, owner, story_id, mode, root, books, worlds):
    kind, obj = root
    if mode == "play":
        for world in worlds:
            result = world_default_context(db, owner, story_id, world)
            if result:
                return result
        return None
    book_ids = [b.id for b in books]
    if mode == "chat":
        return chat_activity(db, owner, story_id, root, books, include_unused=True)
    query = select(Progress).where(Progress.owner_id == owner, Progress.book_id.in_(book_ids))
    if kind == "world":
        query = query.where(Progress.branch_id.in_(publication_branches(db, owner, obj)))
    query = query.order_by(Progress.updated_at.desc(), Progress.id.desc())
    for progress in db.scalars(query):
        candidate = {"storyId": story_id, "mode": "read", "bookId": progress.book_id,
            "readerBranchId": progress.branch_id, "anchor": progress.anchor, "perspective": "reader", "entryOrigin": "library"}
        result = valid_fallback(db, owner, story_id, candidate, progress.updated_at, root)
        if result:
            return result
    # A world without a publication reads its existing saved history directly.
    if kind == "world":
        result = world_default_context(db, owner, story_id, obj, mode="read")
        if result:
            return result
    for book in books:
        query = select(Branch).where(Branch.owner_id == owner, Branch.book_id == book.id,
            Branch.status.in_(["active", "archived"]))
        if kind == "world":
            query = query.where(Branch.id.in_(publication_branches(db, owner, obj)))
        branches = list(db.scalars(query.order_by(Branch.created_at, Branch.id)))
        branches.sort(key=lambda b: b.id != book.original_branch_id)
        for branch in branches:
            revision = db.scalar(select(Revision).where(Revision.owner_id == owner, Revision.branch_id == branch.id, Revision.id == branch.head_revision_id))
            if not revision:
                continue
            first_id = next((c["blocks"][0] for c in revision.manifest if c.get("blocks")), None)
            block = db.scalar(select(ContentBlock).where(ContentBlock.owner_id == owner, ContentBlock.book_id == book.id, ContentBlock.id == first_id))
            if block:
                candidate = {"storyId": story_id, "mode": "read", "bookId": book.id, "readerBranchId": branch.id,
                    "anchor": make_anchor(book.id, branch.id, revision.id, block), "perspective": "reader", "entryOrigin": "library"}
                result = valid_fallback(db, owner, story_id, candidate, book.created_at, root)
                if result:
                    return result
    return None


def resume_result(db, owner, story_id, mode, root, books, worlds):
    row = db.scalar(select(ResumeTarget).where(ResumeTarget.owner_id == owner, ResumeTarget.story_id == story_id, ResumeTarget.mode == mode))
    kind, obj = root
    root_ids = {story_id}
    if kind == "book":
        root_ids.update("world-" + world.id for world in worlds)
    elif mode in {"read", "chat"}:
        root_ids.update("book-" + book.id for book in books)
    query = select(ResumeTarget).where(ResumeTarget.owner_id == owner, ResumeTarget.story_id.in_(root_ids),
        ResumeTarget.mode == mode).order_by(ResumeTarget.updated_at.desc(), ResumeTarget.id.desc())
    publications = publication_branches(db, owner, obj) if kind == "world" and mode in {"read", "chat"} else set()
    result = None
    for candidate in db.scalars(query):
        if candidate.context.get("mode") != mode:
            continue
        if kind == "world" and candidate.story_id != story_id and candidate.context.get("readerBranchId") not in publications:
            continue
        rebased = {**candidate.context, "storyId": story_id}
        result = valid_fallback(db, owner, story_id, rebased, candidate.updated_at, root)
        if result:
            break
    if mode == "read":
        activity = read_activity(db, owner, story_id, root, books, worlds)
        if activity and (result is None or activity[1] > result[1]):
            result = activity
    elif mode == "chat":
        activity = chat_activity(db, owner, story_id, root, books)
        if activity and (result is None or activity[1] > result[1]):
            result = activity
    if result is None:
        result = fallback(db, owner, story_id, mode, root, books, worlds)
    return {"version": row.version if row else 0, "context": result[0] if result else None,
        "updatedAt": result[1].isoformat() if result else None, "available": result is not None}


def world_summary(db, owner, story_id, world):
    selected = world_default_context(db, owner, story_id, world)
    readable = selected or world_default_context(db, owner, story_id, world, mode="read")
    cp = checked_checkpoint(db, owner, world.id, readable[0]["checkpointId"]) if readable else None
    entities = {entity["id"]: entity for entity in cp.definition.get("entities", [])} if cp else {}
    source_book = db.scalar(select(Book).where(Book.owner_id == owner, Book.id == world.source_book_id))
    return {"id": world.id, "name": world.name, "status": world.status, "version": world.version,
        "activeLineId": readable[0]["worldLineId"] if readable else None, "prepareTaskId": world.prepare_task_id,
        "sourceRemoved": source_kind(world) == "book" and bool(world.source_book_id and source_book is None),
        "sourceKind": source_kind(world), "templateId": world.source.get("templateId"), "templateSummary": world.source.get("summary", ""),
        "sourceBookId": source_book.id if source_book else None, "sourceTitle": world.source_title,
        "updatedAt": world.updated_at.isoformat(),
        "playerName": entities.get(cp.state.get("playerId"), {}).get("name", "") if cp else "",
        "locationName": entities.get(cp.state.get("location"), {}).get("name", "") if cp else "",
        "timeLabel": cp.state.get("timeLabel", "") if cp else "",
        "resumeContext": selected[0] if selected else None}


def story_result(db, owner, story_id, root=None):
    root = root or story_root(db, owner, story_id)
    kind, obj = root
    books = relevant_books(db, kind, obj, owner)
    worlds = related_worlds(db, kind, obj, owner)
    worlds.sort(key=lambda world: (world_recent_instant(db, owner, world), world.created_at, world.id), reverse=True)
    resumes = {mode: resume_result(db, owner, story_id, mode, root, books, worlds) for mode in MODES}
    instants = [obj.created_at] + [w.updated_at for w in worlds]
    instants += [r["updatedAt"] for r in resumes.values() if r["updatedAt"]]
    source = None
    if kind == "world" and (obj.source_book_id or obj.source.get("anchor")):
        book = db.scalar(select(Book).where(Book.owner_id == owner, Book.id == obj.source_book_id))
        source = {"bookId": book.id if book else None, "bookTitle": obj.source_title,
            "readerBranchId": obj.source.get("anchor", {}).get("branchId"), "anchor": obj.source.get("anchor"),
            "sourceRemoved": bool(obj.source_book_id and not book)}
    return {"id": story_id, "kind": kind, "title": obj.title if kind == "book" else obj.name,
        "status": obj.status, "version": obj.version, "coverUrl": "/assets/fog-harbor.png" if kind == "book" else None,
        "source": source, "updatedAt": max(t.isoformat() if hasattr(t, "isoformat") else t for t in instants),
        "resume": resumes, "worlds": [world_summary(db, owner, story_id, w) for w in worlds],
        "canCreateWorld": kind == "book" and obj.status == "active" and settings.interactive_world_enabled}


@router.get("/stories")
def stories(cursor: str | None = None, limit: int = Query(default=20, ge=1, le=100),
            status: Literal["active", "archived", "all"] = "active", user=Depends(current_user), db=Depends(get_db)):
    point = story_root(db, user.id, cursor)[1] if cursor else None
    rows = []
    for kind, model in (("book", Book), ("world", World)):
        if kind == "world" and not settings.interactive_world_enabled:
            continue
        query = select(model).where(model.owner_id == user.id, model.status != "deleted")
        if status != "all":
            query = query.where(model.status == "archived" if status == "archived" else model.status != "archived")
        if point:
            # Canonical ID includes the kind, so tied timestamps paginate across
            # both schemas without skipping or returning an item twice.
            query = query.where(tuple_(model.created_at, kind + "-" + model.id) < (point.created_at, cursor))
        rows.extend((f"{kind}-{row.id}", kind, row) for row in db.scalars(query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)))
    rows.sort(key=lambda value: (value[2].created_at, value[0]), reverse=True)
    selected = rows[:limit]
    return {"items": [story_result(db, user.id, sid, (kind, row)) for sid, kind, row in selected],
        "nextCursor": selected[-1][0] if len(rows) > limit else None}


@router.get("/stories/{story_id}")
def story(story_id: str, user=Depends(current_user), db=Depends(get_db)):
    return story_result(db, user.id, story_id)


@router.post("/stories/{story_id}/context/validate")
def context_validate(story_id: str, data: StoryContext, user=Depends(current_user), db=Depends(get_db)):
    return {"valid": True, "context": validate_context(db, user.id, story_id, data)}


@router.put("/stories/{story_id}/resume")
def resume_write(story_id: str, data: ResumeInput, user=Depends(current_user), db=Depends(get_db)):
    root = story_root(db, user.id, story_id, lock=True)
    context = validate_context(db, user.id, story_id, data.context, root=root)
    # Covers two first writes as well as ordinary per-mode CAS. Targets are weak
    # references: deletion cannot cascade into an independently saved world.
    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(digest(user.id + ":resume:" + story_id + data.context.mode)[:15], 16)})
    row = db.scalar(select(ResumeTarget).where(ResumeTarget.owner_id == user.id, ResumeTarget.story_id == story_id,
        ResumeTarget.mode == data.context.mode).with_for_update())
    if data.expectedVersion != (row.version if row else 0):
        raise Problem("VERSION_CONFLICT", "另一设备已保存新的继续位置，请刷新后选择", 409,
            {"currentVersion": row.version if row else 0, "mode": data.context.mode})
    if not row:
        row = ResumeTarget(owner_id=user.id, story_id=story_id, mode=data.context.mode, context=context)
        db.add(row)
    else:
        row.context, row.version = context, row.version + 1
    row.updated_at = now()
    db.commit()
    return {"version": row.version, "context": row.context, "updatedAt": row.updated_at.isoformat(), "available": True}
