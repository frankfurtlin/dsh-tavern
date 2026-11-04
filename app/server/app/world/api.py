import json
import time
from copy import deepcopy
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from fastapi.responses import StreamingResponse
from sqlalchemy import select, delete, text, or_, and_
from sqlalchemy.exc import SQLAlchemyError
from ..config import settings
from ..db import get_db, SessionLocal
from ..security import current_user
from ..models import Book, Branch, Revision, Progress, ContentBlock, Task, TaskEvent, DeviceSession, User, Idempotency, uid, now
from ..domain import owned, idempotent, make_anchor, utf16_len, validate_anchor, digest, canonical
from ..errors import Problem
from ..api_books import paginate
from ..worker import add_event
from .migrate import ready
from .models import World, WorldLine, WorldCheckpoint, WorldTurn, WorldEvent
from .schemas import CreateWorld, BeginWorld, ActionInput, ForkInput, EditWorld, EditLine, WriteInput, AcceptWrite, CorrectionInput, RewriteInput, WorldRules
from .service import create_world, world_result, begin_world, require_line, line_result, checked_checkpoint, checkpoint_result, create_turn, fork_line, publish_reading, source_kind
from .jobs import create_job, task_view, TERMINAL
from .state import ancestry, public_view, public_payload, checkpoint_hash, apply_plan, visible_to, scrub_phrases, event_audience_origins, frozen_event, event_visible_to
from .memory import save_memory
from .corrections import corrected_state
from ..schemas import Strict
from ..cards.world_export import export_world_character


def available(db=Depends(get_db)):
    if not settings.interactive_world_enabled:
        raise Problem("WORLD_DISABLED", "互动世界暂未开启，阅读功能仍可使用", 503)
    if not ready(db):
        raise Problem("WORLD_MIGRATION_REQUIRED", "互动世界正在准备，阅读功能仍可使用", 503)


router = APIRouter(dependencies=[Depends(available)])
config_router = APIRouter()


@config_router.get("/config")
def config(user=Depends(current_user), db=Depends(get_db)):
    return {"enabled": settings.interactive_world_enabled, "ready": settings.interactive_world_enabled and ready(db),
            "releaseStatus": "preview"}


def mutate(db, user, request, scope, data, action):
    # A cached success is still scoped to a currently owned world. This lock
    # linearizes replay against deletion rather than bypassing the route action.
    world_id = request.path_params.get("world_id")
    if world_id:
        owned(db, World, world_id, user.id, lock=True)
        if line_id := request.path_params.get("line_id"):
            require_line(db, user.id, world_id, line_id)
        if turn_id := request.path_params.get("turn_id"):
            turn = owned(db, WorldTurn, turn_id, user.id)
            if turn.world_id != world_id:
                raise Problem("RESOURCE_NOT_FOUND", "这次写作不存在", 404)
        if checkpoint_id := request.path_params.get("checkpoint_id"):
            checked_checkpoint(db, user.id, world_id, checkpoint_id)
    result = idempotent(db, user.id, "world:" + scope, request.headers.get("idempotency-key"), data, action)
    if result.get("deleted"):
        raise Problem("RESOURCE_NOT_FOUND", "这个互动世界已删除", 404)
    return result


@router.get("/sources")
def sources(bookId: str | None = None, user=Depends(current_user), db=Depends(get_db)):
    books = [owned(db, Book, bookId, user.id)] if bookId else list(db.scalars(select(Book).where(Book.owner_id == user.id, Book.status == "active").order_by(Book.created_at.desc())))
    result = []
    for book in books:
        lines = []
        for branch in db.scalars(select(Branch).where(Branch.owner_id == user.id, Branch.book_id == book.id, Branch.status == "active")):
            revision = owned(db, Revision, branch.head_revision_id, user.id)
            progress = db.scalar(select(Progress).where(Progress.owner_id == user.id, Progress.branch_id == branch.id, Progress.revision_id == revision.id))
            if not progress:
                continue
            anchor = progress.max_observed_anchor
            _, _, boundary = validate_anchor(db, anchor, user.id)
            ids = [b for c in revision.manifest for b in c["blocks"]]
            boundary_index = ids.index(boundary.id)
            chapters = []
            for chapter in revision.manifest:
                permitted = [b for b in chapter["blocks"] if ids.index(b) <= boundary_index]
                if permitted:
                    last = owned(db, ContentBlock, permitted[-1], user.id)
                    offset = anchor["end"] if last.id == boundary.id else utf16_len(last.text)
                    chapters.append({"title": chapter["title"], "anchor": make_anchor(book.id, branch.id, revision.id, last, offset)})
            lines.append({"id": branch.id, "name": branch.name, "anchor": anchor, "chapters": chapters})
        result.append({"id": book.id, "title": book.title, "lines": lines})
    return {"items": result}


@router.get("/templates")
def templates(user=Depends(current_user)):
    from .templates import catalogue
    return {"items": catalogue()}


@router.get("/worlds")
def worlds(user=Depends(current_user), db=Depends(get_db)):
    rows = db.scalars(select(World).where(World.owner_id == user.id).order_by(World.updated_at.desc()))
    return {"items": [world_result(db, w) for w in rows]}


@router.post("/worlds", status_code=202)
def create(data: CreateWorld, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return mutate(db, user, request, "create", data.model_dump(), lambda: create_world(db, user.id, data))


@router.get("/worlds/{world_id}")
def world_read(world_id: str, user=Depends(current_user), db=Depends(get_db)):
    result = world_result(db, owned(db, World, world_id, user.id), include_definition=True)
    db.commit()
    return result


class ExportCharacterCardInput(Strict):
    characterId: str = Field(min_length=1, max_length=80)


@router.post("/worlds/{world_id}/character-cards", status_code=201)
def export_character_card(world_id: str, data: ExportCharacterCardInput,
                          user=Depends(current_user), db=Depends(get_db)):
    world = owned(db, World, world_id, user.id)
    card = export_world_character(db, user.id, world, data.characterId)
    return {"cardId": card.id}


@router.patch("/worlds/{world_id}")
def world_edit(world_id: str, data: EditWorld, user=Depends(current_user), db=Depends(get_db)):
    world = owned(db, World, world_id, user.id, lock=True)
    if world.version != data.expectedVersion:
        raise Problem("VERSION_CONFLICT", "世界已更新，请刷新后重试", 409)
    if data.status == "active" and world.status in ("preparing", "failed"):
        raise Problem("WORLD_NOT_READY", "请先完成世界准备", 409)
    if data.name is not None:
        world.name = data.name
        from ..authoring.models import AuthorDraft
        author=db.get(AuthorDraft,world.id)
        if author:author.value={**author.value,'title':data.name};author.version+=1;author.updated_at=now()
    if data.status:
        status = data.status
        if status == "active" and world.status == "archived" and not world.active_line_id:
            preparation = db.get(Task, world.prepare_task_id) if world.prepare_task_id else None
            complete = bool(world.definition.get("entities") and world.definition.get("location") and
                            (source_kind(world) == "standalone" or
                             (preparation and preparation.state == "succeeded")))
            status = "active" if complete else "failed"
        world.status = status
        cancel_jobs(db, user.id, world_id) if status == "archived" else None
    world.version += 1
    world.updated_at = now()
    db.commit()
    return world_result(db, world)


def cancel_jobs(db, owner, world_id, line_id=None):
    query = select(Task).where(Task.owner_id == owner, Task.task_type.like("world_%"),
                              Task.state.notin_(TERMINAL) | ((Task.state == "failed") & (Task.persistence_state == "recoverable")),
                              Task.payload["worldId"].astext == world_id)
    if line_id:
        query = query.where(Task.payload["lineId"].astext == line_id)
    for task in db.scalars(query.order_by(Task.id).with_for_update()):
        task.cancel_requested = True


@router.delete("/worlds/{world_id}")
def world_delete(world_id: str, user=Depends(current_user), db=Depends(get_db)):
    world = db.scalar(select(World).where(World.id == world_id, World.owner_id == user.id).with_for_update())
    if world is None:
        # A failed post-commit file cleanup remains retryable by its owner. An
        # arbitrary or another owner's missing world must still return 404.
        deleted = db.scalar(select(Task.id).where(Task.owner_id == user.id,
            Task.task_type.like("world_%"), Task.payload["worldId"].astext == world_id,
            Task.result["worldId"].astext == world_id, Task.result["deleted"].astext == "true").limit(1))
        if deleted is None:
            raise Problem("RESOURCE_NOT_FOUND", "内容不存在或不可访问", 404)
    else:
        cancel_jobs(db, user.id, world_id)
        resource_ids = {world_id}
        for model in (WorldLine, WorldCheckpoint, WorldTurn):
            resource_ids.update(db.scalars(select(model.id).where(model.owner_id == user.id, model.world_id == world_id)))
        for receipt in db.scalars(select(Idempotency).where(
                Idempotency.owner_id == user.id, Idempotency.scope.like("world:%")).with_for_update()):
            if (receipt.scope.rsplit(":", 1)[-1] in resource_ids or
                    (receipt.result or {}).get("worldId") == world_id):
                # Retain the stable key/hash, never a deleted private snapshot.
                receipt.result = {"worldId": world_id, "deleted": True}
        for task in db.scalars(select(Task).where(Task.owner_id == user.id, Task.task_type.like("world_%"),
                Task.payload["worldId"].astext == world_id).with_for_update()):
            task.checkpoint = {k: v for k, v in task.checkpoint.items()
                               if k not in ("worldStream", "worldDeniedPhrases")}
            task.checkpoint = {**task.checkpoint, "worldPrivateCleanupPending": True}
            task.result = {"worldId": world_id, "deleted": True}
            task.cancel_requested = True
            db.execute(delete(TaskEvent).where(TaskEvent.task_id == task.id))
        from ..models import CharacterAlias, CharacterScopeRevision
        db.execute(delete(CharacterScopeRevision).where(CharacterScopeRevision.owner_id == user.id,
            CharacterScopeRevision.scope_kind == "world", CharacterScopeRevision.scope_id == world_id))
        db.execute(delete(CharacterAlias).where(CharacterAlias.owner_id == user.id,
            CharacterAlias.scope_kind == "world", CharacterAlias.scope_id == world_id))
        if db.scalar(text("SELECT to_regclass('image_assets')")) is not None:
            from ..assets.service import purge_scope
            purge_scope(db, user.id, "world", world_id)
        purge_playback(db, user.id, world_id)
        from ..comparison.cleanup import purge as purge_comparison
        purge_comparison(db, user.id, 'world', world_id)
        from ..foundation.models import WorldPromptSelection
        db.execute(delete(WorldPromptSelection).where(WorldPromptSelection.world_id == world_id,
            WorldPromptSelection.owner_id == user.id))
        from ..authoring.models import AuthorDraft, Publication
        from ..play_controls.service import purge as purge_play_controls
        purge_play_controls(db,user.id,world_id)
        db.execute(delete(Publication).where(Publication.world_id==world_id,Publication.owner_id==user.id))
        db.execute(delete(AuthorDraft).where(AuthorDraft.world_id==world_id,AuthorDraft.owner_id==user.id))
        db.delete(world)
    # A rollback must preserve every recovery journal and cannot install a
    # permanent file tombstone for a still-existing world.
    db.commit()
    from ..storage import clean_deleted_world_journals
    try:
        complete = clean_deleted_world_journals(user.id, world_id)
    except (OSError, SQLAlchemyError):
        complete = False
    if not complete:
        raise Problem("WORLD_CLEANUP_PENDING", "世界已删除，私人文件清理暂未完成，请重试", 503,
                      {"worldId": world_id, "deleted": True, "cleanupPending": True}, retryable=True)
    return {"deleted": True}


@router.post("/worlds/{world_id}/prepare", status_code=202)
def prepare_again(world_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def action():
        world = owned(db, World, world_id, user.id, lock=True)
        if world.status == "archived":
            raise Problem("WORLD_ARCHIVED", "请先恢复这个世界", 409)
        if source_kind(world) == "standalone":
            raise Problem("WORLD_ALREADY_PREPARED", "原创模板已准备好，可以直接选择身份", 409)
        previous = db.get(Task, world.prepare_task_id) if world.prepare_task_id else None
        if previous and previous.state not in TERMINAL:
            return {"taskId": previous.id, "worldId": world.id}
        if world.active_line_id:
            raise Problem("WORLD_ALREADY_STARTED", "世界已开始，资料调整会另开世界线", 409)
        if previous and previous.state == "failed" and previous.persistence_state == "recoverable":
            previous.cancel_requested = True
        from .preparation import batches
        count=len(batches(world.source,8))
        task = create_job(db, user.id, "world_prepare", {"worldId": world.id,"preparationBatches":count}, ["analysis"]*count)
        world.prepare_task_id, world.status = task.id, "preparing"
        return {"taskId": task.id, "worldId": world.id}
    return mutate(db, user, request, "prepare:" + world_id, {}, action)


@router.post("/worlds/{world_id}/begin", status_code=202)
def begin(world_id: str, data: BeginWorld, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return mutate(db, user, request, "begin:" + world_id, data.model_dump(), lambda: begin_world(db, user.id, owned(db, World, world_id, user.id, lock=True), data))


@router.get("/worlds/{world_id}/lines")
def lines(world_id: str, user=Depends(current_user), db=Depends(get_db)):
    owned(db, World, world_id, user.id)
    result = {"items": [line_result(db, line) for line in db.scalars(select(WorldLine).where(WorldLine.owner_id == user.id, WorldLine.world_id == world_id, WorldLine.status != "deleted").order_by(WorldLine.created_at))]}
    db.commit()
    return result


@router.get("/worlds/{world_id}/lines/{line_id}")
def line_read(world_id: str, line_id: str, user=Depends(current_user), db=Depends(get_db)):
    result = line_result(db, require_line(db, user.id, world_id, line_id)[1])
    db.commit()
    return result


@router.patch("/worlds/{world_id}/lines/{line_id}")
def line_edit(world_id: str, line_id: str, data: EditLine, user=Depends(current_user), db=Depends(get_db)):
    owned(db, World, world_id, user.id, lock=True)
    world, line = require_line(db, user.id, world_id, line_id, lock=True)
    if line.version != data.expectedVersion:
        raise Problem("VERSION_CONFLICT", "世界线已更新，请刷新后重试", 409)
    for field, attribute in (("name", "name"), ("status", "status"), ("camera", "camera"), ("readingPosition", "reading_position")):
        value = getattr(data, field)
        if value is not None:
            if field == "readingPosition" and value.get("checkpointId"):
                selected = checked_checkpoint(db, user.id, world_id, value["checkpointId"])
                head = checked_checkpoint(db, user.id, world_id, line.head_checkpoint_id)
                if selected.id not in {c.id for c in ancestry(db, head, user.id)}:
                    raise Problem("RESOURCE_NOT_FOUND", "这个阅读位置不属于当前世界线", 404)
                # Reading has its own server time. Camera/name/head activity
                # must never outrank a later saved reading position.
                value = {**value, "updatedAt": now().isoformat()}
            setattr(line, attribute, value)
    if data.status == "archived":
        cancel_jobs(db, user.id, world_id, line_id)
    elif data.status == "active":
        entry = db.get(WorldLine, world.active_line_id) if world.active_line_id else None
        if not entry or entry.status != "active":
            world.active_line_id = line.id
    line.version += 1
    result = line_result(db, line)
    db.commit()
    return result


@router.delete("/worlds/{world_id}/lines/{line_id}")
def line_delete(world_id: str, line_id: str, user=Depends(current_user), db=Depends(get_db)):
    owned(db, World, world_id, user.id, lock=True)
    world, line = require_line(db, user.id, world_id, line_id, lock=True)
    cancel_jobs(db, user.id, world_id, line_id)
    if db.scalar(text("SELECT to_regclass('image_assets')")) is not None:
        from ..assets.service import purge_scope
        purge_scope(db, user.id, "line", line_id)
    purge_playback(db, user.id, world_id, line_id)
    from ..comparison.cleanup import purge as purge_comparison
    purge_comparison(db, user.id, 'world', world_id, line_id)
    line.status = "deleted"
    if world.active_line_id == line_id:
        from .service import readable_line
        replacement = readable_line(db, world, exclude=line_id)
        world.active_line_id = replacement.id if replacement else None
    db.commit()
    return {"deleted": True}


def purge_playback(db, owner, world_id, line_id=None):
    if db.scalar(text("SELECT to_regclass('playback_positions')")) is None:
        return
    from ..models import PlaybackPosition
    query = delete(PlaybackPosition).where(PlaybackPosition.owner_id == owner, PlaybackPosition.world_id == world_id)
    if line_id is not None:
        query = query.where(PlaybackPosition.line_id == line_id)
    db.execute(query)
    for row in db.scalars(select(Idempotency).where(Idempotency.owner_id == owner, Idempotency.scope == 'playback-save')):
        context = row.result.get('position', {}).get('context', {})
        if context.get('worldId') == world_id and (line_id is None or context.get('worldLineId') == line_id):
            row.result = {'worldId': world_id, 'deleted': True}


@router.get("/worlds/{world_id}/lines/{line_id}/history")
def history(world_id: str, line_id: str, user=Depends(current_user), db=Depends(get_db)):
    _, line = require_line(db, user.id, world_id, line_id)
    head = checked_checkpoint(db, user.id, world_id, line.head_checkpoint_id)
    return {"items": [checkpoint_result(c, include_state=False) for c in ancestry(db, head, user.id)], "headCheckpointId": head.id}


@router.get("/worlds/{world_id}/graph")
def graph(world_id: str, user=Depends(current_user), db=Depends(get_db)):
    from .graph import graph as graph_metadata
    return graph_metadata(db, user.id, world_id)


@router.get("/worlds/{world_id}/checkpoints/{checkpoint_id}")
def checkpoint(world_id: str, checkpoint_id: str, user=Depends(current_user), db=Depends(get_db)):
    owned(db, World, world_id, user.id)
    result = checkpoint_result(checked_checkpoint(db, user.id, world_id, checkpoint_id), db=db)
    db.commit()
    return result


@router.get("/worlds/{world_id}/checkpoints/{checkpoint_id}/events")
def checkpoint_events(world_id: str, checkpoint_id: str, user=Depends(current_user), db=Depends(get_db)):
    cp = checked_checkpoint(db, user.id, world_id, checkpoint_id)
    path = ancestry(db, cp, user.id)
    path_ids = [c.id for c in path]
    checkpoints, origins = {c.id: c for c in path}, event_audience_origins(path)
    events = db.scalars(select(WorldEvent).where(WorldEvent.owner_id == user.id, WorldEvent.checkpoint_id.in_(path_ids)).order_by(WorldEvent.minute, WorldEvent.created_at))
    return public_payload({"items": [{"id": e.id, "checkpointId": e.checkpoint_id, "minute": e.minute, "type": e.type, "description": e.data["description"], "entityId": e.entity_id}
        for e in events if event_visible_to(frozen_event({**e.data, "visibleTo": e.visible_to},
            origins.get(e.data.get("id"), checkpoints[e.checkpoint_id].state["playerId"])), cp.state["playerId"])]}, cp.state, cp.definition)


@router.post("/worlds/{world_id}/lines/{line_id}/actions", status_code=202)
def action(world_id: str, line_id: str, data: ActionInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def execute():
        world, line = require_line(db, user.id, world_id, line_id, lock=True, active=True)
        previous = db.scalar(select(WorldTurn).where(WorldTurn.owner_id == user.id, WorldTurn.line_id == line_id, WorldTurn.turn_key == data.turnId))
        if previous:
            if previous.action != data.message or previous.kind != data.kind or previous.parent_checkpoint_id != data.expectedCheckpointId:
                raise Problem("IDEMPOTENCY_CONFLICT", "同一回合不能更换内容，请创建新回合", 409)
            return {"taskId": previous.task_id, "turnId": previous.id, "lineId": line_id}
        return create_turn(db, user.id, world, line, data)
    return mutate(db, user, request, "action:" + line_id, data.model_dump(), execute)


@router.get("/worlds/{world_id}/lines/{line_id}/actions/{turn_key}")
def action_receipt(world_id: str, line_id: str, turn_key: str, user=Depends(current_user), db=Depends(get_db)):
    require_line(db, user.id, world_id, line_id)
    turn = db.scalar(select(WorldTurn).where(WorldTurn.owner_id == user.id, WorldTurn.line_id == line_id, WorldTurn.turn_key == turn_key))
    if not turn:
        raise Problem("RESOURCE_NOT_FOUND", "这次行动尚未提交", 404)
    return {"taskId": turn.task_id, "turnId": turn.id, "checkpointId": turn.checkpoint_id}


@router.post("/worlds/{world_id}/fork")
def fork(world_id: str, data: ForkInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return mutate(db, user, request, "fork:" + world_id, data.model_dump(), lambda: fork_line(db, user.id, owned(db, World, world_id, user.id, lock=True), data))


@router.post("/worlds/{world_id}/lines/{line_id}/write", status_code=202)
def write(world_id: str, line_id: str, data: WriteInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def execute():
        world, line = require_line(db, user.id, world_id, line_id, lock=True, active=True)
        value = ActionInput(message=data.instruction.strip() or "遵循人物与世界规则，继续推进当前故事，完成本次指定章数。", kind="action", expectedCheckpointId=data.expectedCheckpointId, turnId=request.headers.get("idempotency-key") or uid())
        return create_turn(db, user.id, world, line, value, "world_write", chapter_count=data.chapterCount)
    return mutate(db, user, request, "write:" + line_id, data.model_dump(), execute)


def turn_view(db, current):
    checkpoint = db.get(WorldCheckpoint, current.checkpoint_id or current.parent_checkpoint_id)
    state, definition = checkpoint.state, checkpoint.definition
    if current.result.get("chapters"):
        latest = current.result["chapters"][-1]
        state, definition = latest["state"], latest["definition"]
    task = db.get(Task, current.task_id) if current.result.get("importedHistory") is True and not current.checkpoint_id else None
    stream = task.checkpoint.get("worldStream", {}) if task else {}
    historical = bool(task and task.owner_id == current.owner_id and task.payload.get("importedHistory") is True
        and task.payload.get("worldId") == current.world_id and task.payload.get("lineId") == current.line_id
        and task.payload.get("turnId") == current.id and stream.get("historical") is True
        and stream.get("durability") == "unconfirmed" and stream.get("text"))
    from ..domain import digest
    draft_text = stream["text"] if historical else current.draft_text
    segments = current.result.get("draftSegments") if current.result.get("draftTextHash") == digest(draft_text or "") else None
    return public_payload({"id": current.id, "taskId": current.task_id, "lineId": current.line_id, "status": "historical" if historical else current.status,
        "parentCheckpointId": current.parent_checkpoint_id, "checkpointId": current.checkpoint_id,
        "action": current.action, "draftText": draft_text, "draftSegments": segments, "warnings": current.warnings,
        "chapters": [public_payload({"index": i + 1, "title": c["plan"]["title"], "text": c["text"], "changes": c["changes"], "options": c["plan"]["options"]}, c["state"], c["definition"]) for i, c in enumerate(current.result.get("chapters", []))],
        "acceptedCount": current.result.get("acceptedCount", 0)}, state, definition)


@router.get("/worlds/{world_id}/turns/{turn_id}")
def turn_read(world_id: str, turn_id: str, user=Depends(current_user), db=Depends(get_db)):
    current = owned(db, WorldTurn, turn_id, user.id)
    if current.world_id != world_id:
        raise Problem("RESOURCE_NOT_FOUND", "这次写作不存在", 404)
    return turn_view(db, current)


@router.get("/worlds/{world_id}/lines/{line_id}/drafts")
def drafts(world_id: str, line_id: str, cursor: str | None = None, limit: int = 20, user=Depends(current_user), db=Depends(get_db)):
    require_line(db, user.id, world_id, line_id)
    historical = and_(WorldTurn.status == "cancelled", WorldTurn.checkpoint_id.is_(None),
        WorldTurn.result["importedHistory"].astext == "true", Task.owner_id == user.id,
        Task.payload["importedHistory"].astext == "true", Task.payload["worldId"].astext == world_id,
        Task.payload["lineId"].astext == line_id, Task.payload["turnId"].astext == WorldTurn.id,
        Task.checkpoint["worldStream"]["historical"].astext == "true",
        Task.checkpoint["worldStream"]["durability"].astext == "unconfirmed",
        Task.checkpoint["worldStream"]["text"].astext != "")
    if cursor:
        point = owned(db, WorldTurn, cursor, user.id)
        if point.world_id != world_id or point.line_id != line_id:
            raise Problem("RESOURCE_NOT_FOUND", "这段草稿记录不存在", 404)
    receipt = select(Task.id).where(Task.id == WorldTurn.task_id, historical).correlate(WorldTurn).exists()
    rows, next_cursor = paginate(db, WorldTurn, user.id, cursor, limit, filters=(
        WorldTurn.world_id == world_id, WorldTurn.line_id == line_id,
        or_(WorldTurn.status.in_(["draft", "candidate"]), receipt)))
    return {"items": [turn_view(db, t) for t in rows], "nextCursor": next_cursor}


@router.post("/worlds/{world_id}/turns/{turn_id}/accept")
def accept(world_id: str, turn_id: str, data: AcceptWrite, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def execute():
        current = owned(db, WorldTurn, turn_id, user.id, lock=True)
        if current.world_id != world_id:
            raise Problem("RESOURCE_NOT_FOUND", "这次写作不存在", 404)
        world, line = require_line(db, user.id, world_id, current.line_id, lock=True, active=True)
        task = owned(db, Task, current.task_id, user.id, lock=True)
        if task.payload.get("importedHistory") is True or current.result.get("importedHistory") is True:
            raise Problem("WORLD_HISTORY_READ_ONLY", "恢复的正文仅供阅读，请从当前节点开始新的写作", 409)
        if task.task_type != "world_write" or task.state not in TERMINAL:
            raise Problem("WORLD_CANDIDATE_NOT_READY", "请等待这次写作结束", 409)
        if current.result.get("acceptedCount"):
            raise Problem("WORLD_ALREADY_ACCEPTED", "这次候选已经保存，剩余正文仍可查看", 409)
        chapters = current.result.get("chapters", [])
        if data.chapterCount > len(chapters):
            raise Problem("WORLD_PREFIX_INVALID", "只能接受已经完成的连续章节前缀")
        if line.head_checkpoint_id != current.parent_checkpoint_id or data.expectedCheckpointId != line.head_checkpoint_id or line.pending_task_id:
            raise Problem("WORLD_CHECKPOINT_CONFLICT", "世界线已变化，候选正文仍保留", 409)
        parent = checked_checkpoint(db, user.id, world_id, line.head_checkpoint_id)
        for scene in chapters[:data.chapterCount]:
            if scene.get("contentHash") != digest(canonical({k: v for k, v in scene.items() if k != "contentHash"})):
                raise Problem("WORLD_CHECKPOINT_INVALID", "候选保存校验未通过，正文仍可查看", 409)
            cp = WorldCheckpoint(id=scene["id"], owner_id=user.id, world_id=world.id, line_id=line.id,
                parent_checkpoint_id=parent.id, sequence=parent.sequence + 1, title=scene["plan"]["title"],
                text=scene["text"], action=current.action, state=scene["state"], definition=scene["definition"],
                options=[] if scene["state"]["ended"] else scene["plan"]["options"], changes=scene["changes"], summary=scene["plan"]["summary"],
                memory=deepcopy(parent.memory), content_hash=checkpoint_hash(scene["state"], scene["definition"], scene["text"], parent.id), kind="chapter")
            db.add(cp)
            db.flush()
            for event in scene["events"]:
                db.add(WorldEvent(owner_id=user.id, world_id=world.id, checkpoint_id=cp.id, entity_id=event.get("entityId"), type=event["type"], data=event, visible_to=event["visibleTo"], minute=event.get("minute", scene["state"]["minute"])))
            save_memory(db, cp, parent, scene["events"])
            parent = cp
        line.head_checkpoint_id, line.version = parent.id, line.version + 1
        world.active_line_id, world.updated_at = line.id, now()
        current.status, current.checkpoint_id = "committed", parent.id
        current.result = {**current.result, "acceptedCount": data.chapterCount}
        return {"lineId": line.id, "checkpointId": parent.id, "acceptedCount": data.chapterCount}
    return mutate(db, user, request, "accept:" + turn_id, data.model_dump(), execute)


@router.post("/worlds/{world_id}/rewrite", status_code=202)
def rewrite(world_id: str, data: RewriteInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def execute():
        world = owned(db, World, world_id, user.id, lock=True)
        if world.status != "active":
            raise Problem("WORLD_ARCHIVED", "请先恢复这个世界", 409)
        selected = checked_checkpoint(db, user.id, world_id, data.checkpointId)
        if selected.kind not in ("turn", "chapter", "rewrite") or not selected.parent_checkpoint_id:
            raise Problem("WORLD_REWRITE_INVALID", "请选择已经完成的场景正文")
        # Fork from BEFORE selected: the replacement scene uses the SAME post state.
        line = fork_line(db, user.id, world, ForkInput(checkpointId=selected.id, mode="rechoose", name="另一种写法"))
        target = owned(db, WorldLine, line["id"], user.id, lock=True)
        target.head_checkpoint_id = selected.parent_checkpoint_id
        turn_id = uid()
        task = create_job(db, user.id, "world_rewrite", {"worldId": world_id, "lineId": target.id, "turnId": turn_id, "rewriteCheckpointId": selected.id}, ["generation", "validation"])
        db.add(WorldTurn(id=turn_id, owner_id=user.id, world_id=world_id, line_id=target.id, parent_checkpoint_id=selected.parent_checkpoint_id,
            turn_key=uid(), task_id=task.id, action=data.instruction.strip() or "在保留本次行动结果的前提下，换一种细腻的文字表达。", kind="rewrite"))
        target.pending_task_id = task.id
        return {"taskId": task.id, "turnId": turn_id, "lineId": target.id}
    return mutate(db, user, request, "rewrite:" + world_id, data.model_dump(), execute)


@router.post("/worlds/{world_id}/corrections")
def correction(world_id: str, data: CorrectionInput, request: Request, user=Depends(current_user), db=Depends(get_db)):
    def execute():
        world = owned(db, World, world_id, user.id, lock=True)
        if world.status != "active":
            raise Problem("WORLD_ARCHIVED", "请先恢复这个世界", 409)
        parent = checked_checkpoint(db, user.id, world_id, data.expectedCheckpointId)
        result = fork_line(db, user.id, world, ForkInput(checkpointId=parent.id, name=data.name))
        line = owned(db, WorldLine, result["id"], user.id, lock=True)
        cp = checked_checkpoint(db, user.id, world_id, line.head_checkpoint_id)
        state, definition, changes = corrected_state(cp.state, cp.definition, data, cp.id)
        cp.definition, cp.state, cp.kind = definition, state, "correction"
        cp.options = []  # Suggestions from the old state are not advice for corrected facts.
        cp.summary = "用户调整：" + data.reason
        cp.changes = changes
        cp.content_hash = checkpoint_hash(state, definition, cp.text, cp.parent_checkpoint_id)
        return line_result(db, line)
    return mutate(db, user, request, "correction:" + world_id, data.model_dump(), execute)


@router.post("/worlds/{world_id}/checkpoints/{checkpoint_id}/publish")
def publish(world_id: str, checkpoint_id: str, request: Request, user=Depends(current_user), db=Depends(get_db)):
    return mutate(db, user, request, "publish:" + checkpoint_id, {}, lambda: publish_reading(db, user.id,
        owned(db, World, world_id, user.id, lock=True), checked_checkpoint(db, user.id, world_id, checkpoint_id)))


def world_task(db, task_id, owner, lock=False):
    task = owned(db, Task, task_id, owner, lock=lock)
    if not task.task_type.startswith("world_"):
        raise Problem("RESOURCE_NOT_FOUND", "这不是一个世界任务", 404)
    owned(db, World, task.payload["worldId"], owner)
    return task


@router.get("/tasks/{task_id}")
def task_read(task_id: str, user=Depends(current_user), db=Depends(get_db)):
    return task_view(world_task(db, task_id, user.id))


@router.post("/tasks/{task_id}/cancel")
def cancel(task_id: str, user=Depends(current_user), db=Depends(get_db)):
    task = world_task(db, task_id, user.id, lock=True)
    if task.state not in TERMINAL or (task.state == "failed" and task.persistence_state == "recoverable"):
        task.cancel_requested = True
    db.commit()
    return task_view(task)


@router.get("/tasks/{task_id}/events")
def events(task_id: str, request: Request, afterSeq: int = 0, user=Depends(current_user), db=Depends(get_db)):
    world_task(db, task_id, user.id)
    last = request.headers.get("last-event-id", "")
    if last.startswith(task_id + ":"):
        try:
            afterSeq = max(afterSeq, int(last.split(":")[-1]))
        except ValueError:
            pass
    owner, sid = user.id, request.state.session.id
    def stream():
        cursor = afterSeq
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            frames = []
            with SessionLocal() as conn:
                session, account = conn.get(DeviceSession, sid), conn.get(User, owner)
                if not session or session.revoked_at or session.expires_at < now() or not account or account.status != "active":
                    return
                try:
                    task = world_task(conn, task_id, owner)
                except Problem:
                    return
                for event in conn.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id, TaskEvent.seq > cursor).order_by(TaskEvent.seq)):
                    cursor = event.seq
                    from ..durable_stream import event_payload
                    canonical_types = {'text_delta','chapter_saved','phase','usage','content_hint','terminal'}
                    payload = event_payload(event.type,event.payload) if event.type in canonical_types else scrub_phrases(event.payload, task.checkpoint.get("worldDeniedPhrases", []))
                    legacy = event.payload if event.type=='text_delta' else payload
                    value = {'taskId': task_id, 'seq': event.seq, 'type': event.type, **legacy, 'payload': payload}
                    frames.append(f"id: {task_id}:{event.seq}\nevent: {event.type}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n")
                terminal = task.state in TERMINAL and task.persistence_state != "recoverable"
            # No database transaction/lifecycle lock survives network yield.
            yield from frames
            if terminal:
                return
            yield ": keepalive\n\n"
            time.sleep(.025)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
