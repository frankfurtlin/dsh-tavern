from sqlalchemy import select, text
from .. import models as m
from ..domain import digest, idempotent, make_anchor, owned, utf16_len
from ..errors import Problem
from ..world.service import checked_checkpoint, require_line
from . import reader, world
from .common import authorize, differences, exposure_ready, scope_key
from .models import ComparisonExposure
from .schemas import PROTOCOL


def compare(db, owner, story_id, data):
    for line_id in (data.leftLineId, data.rightLineId):
        authorize(db, owner, story_id, data.kind, data.bookId or data.worldId, line_id)
    left, right, common = (reader if data.kind == "book" else world).compare(db, owner, data)
    changes, truncated = differences(left["state"], right["state"])
    return {"protocol": PROTOCOL, "storyId": story_id, "kind": data.kind,
        "alignment": data.alignment, "atMinute": data.atMinute, "common": common,
        "left": left, "right": right, "differences": changes, "differencesTruncated": truncated}


def selected_anchor(db, owner, data):
    if data.kind == "book":
        book, branch, revision, entries = reader.location(db, owner, data.resourceId, data.lineId, data.nodeId)
        if not entries:
            raise Problem("COMPARE_RANGE_EMPTY", "这条路径没有可阅读正文", 409)
        block = owned(db, m.ContentBlock, entries[-1][2], owner)
        if block.book_id != book.id or block.chapter_id != entries[-1][0] or block.content_hash != digest(block.text):
            raise Problem("CONTENT_INVALID", "正文段落校验未通过", 409)
        return make_anchor(book.id, branch.id, revision.id, block, utf16_len(block.text))
    _, line = require_line(db, owner, data.resourceId, data.lineId)
    point = checked_checkpoint(db, owner, data.resourceId, data.nodeId)
    graph = world.skeleton(db, owner, data.resourceId)
    if point.id not in world.path(graph, line.head_checkpoint_id):
        raise Problem("RESOURCE_NOT_FOUND", "比较节点不属于所选路径", 404)
    return {"checkpointId": point.id}


def result(row):
    return {"protocol": PROTOCOL, "kind": row.kind, "resourceId": row.resource_id,
        "lineId": row.line_id, "nodeId": row.node_id, "version": row.version,
        "anchor": row.anchor}


def reveal(db, owner, story_id, key, data):
    authorize(db, owner, story_id, data.kind, data.resourceId, data.lineId)
    # The view range has no World FK. Join the same lifecycle as deletion so
    # a previously authorized request cannot recreate a purged range afterward.
    # Acquire reader lifecycle before world rows, as source removal does.
    from ..api_stories import story_root
    from ..world.models import World
    root_kind, root = story_root(db, owner, story_id)
    if data.kind == "book":
        owned(db, m.Book, data.resourceId, owner)
    world_id = data.resourceId if data.kind == "world" else root.id if root_kind == "world" else None
    if world_id is not None:
        world_row = owned(db, World, world_id, owner, lock=True)
        if world_row.status == "deleted":
            raise Problem("RESOURCE_NOT_FOUND", "比较路径已移除", 404)
    if data.kind == "world":
        require_line(db, owner, data.resourceId, data.lineId, lock=True)
    else:
        branch = owned(db, m.Branch, data.lineId, owner, lock=True)
        if branch.book_id != data.resourceId or branch.status == "deleted":
            raise Problem("RESOURCE_NOT_FOUND", "比较路径已移除", 404)
    authorize(db, owner, story_id, data.kind, data.resourceId, data.lineId)
    anchor = selected_anchor(db, owner, data)
    if not data.confirmed:
        raise Problem("COMPARE_UNSEEN_CONFIRMATION_REQUIRED", "请确认查看所选路径后续", 409)
    if not exposure_ready(db):
        raise Problem("COMPARE_NOT_READY", "查看后续尚未就绪", 503)
    scope = scope_key(data.kind, data.resourceId, data.lineId, data.nodeId)
    def save():
        # Serialize insertion and CAS on this view range alone. No world head,
        # source cursor, camera, model task or knowledge record is written.
        db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": int(digest(owner+scope)[:15], 16)})
        row = db.scalar(select(ComparisonExposure).where(ComparisonExposure.owner_id == owner,
            ComparisonExposure.scope_key == scope).with_for_update())
        version = row.version if row else 0
        if version != data.expectedVersion:
            raise Problem("COMPARE_VIEW_CONFLICT", "另一设备已更新查看范围，请刷新后重试", 409,
                {"version": version})
        if not row:
            row = ComparisonExposure(owner_id=owner, scope_key=scope, kind=data.kind,
                resource_id=data.resourceId, line_id=data.lineId, node_id=data.nodeId)
            db.add(row)
        row.anchor = anchor; row.version = version+1; row.updated_at = m.now()
        db.flush()
        return result(row)
    return idempotent(db, owner, "compare-reveal:"+story_id, key, data.model_dump(), save)


def reveal_status(db, owner, story_id, key):
    # A lookup never recreates or dispatches the original request.
    from ..api_stories import story_root
    story_root(db, owner, story_id)
    row = db.scalar(select(m.Idempotency).where(m.Idempotency.owner_id == owner,
        m.Idempotency.scope == "compare-reveal:"+story_id, m.Idempotency.key == key))
    if not row:
        return {"found": False, "result": None}
    value = row.result
    if value.get("deleted"):
        raise Problem("RESOURCE_NOT_FOUND", "原比较路径已经移除", 404)
    authorize(db, owner, story_id, value["kind"], value["resourceId"], value["lineId"])
    # Cached results are still subject to current source ownership/deletion.
    from .schemas import RevealInput
    selected_anchor(db, owner, RevealInput(confirmed=True, kind=value["kind"], resourceId=value["resourceId"],
        lineId=value["lineId"], nodeId=value["nodeId"], expectedVersion=value["version"]))
    return {"found": True, "result": value}
