"""Bounded, owner-authorized graph metadata; no per-node database reads."""
from sqlalchemy import select
from ..domain import owned
from ..errors import Problem
from .models import World, WorldLine, WorldCheckpoint
from .state import checkpoint_hash, public_payload

MAX_GRAPH_NODES = 200000
MAX_PATH = 10000


def graph(db, owner, world_id):
    world = owned(db, World, world_id, owner)
    if world.status == "deleted":
        raise Problem("RESOURCE_NOT_FOUND", "这个世界不存在或不可访问", 404)
    lines = list(db.scalars(select(WorldLine).where(WorldLine.owner_id == owner,
        WorldLine.world_id == world_id, WorldLine.status != "deleted")))
    if not lines:
        return {"lines": [], "nodes": []}
    columns = (WorldCheckpoint.id, WorldCheckpoint.line_id, WorldCheckpoint.parent_checkpoint_id,
        WorldCheckpoint.sequence, WorldCheckpoint.title, WorldCheckpoint.action, WorldCheckpoint.kind,
        WorldCheckpoint.created_at, WorldCheckpoint.state, WorldCheckpoint.definition)
    rows = db.execute(select(*columns).where(WorldCheckpoint.owner_id == owner,
        WorldCheckpoint.world_id == world_id).limit(MAX_GRAPH_NODES+1)).all()
    if len(rows) > MAX_GRAPH_NODES:
        raise Problem("WORLD_GRAPH_LIMIT", "这张图谱过大，请打开单条历史路径", 422)
    records = {row.id: row for row in rows}
    heads = {line.head_checkpoint_id for line in lines}
    checkpoints = db.scalars(select(WorldCheckpoint).where(WorldCheckpoint.owner_id == owner,
        WorldCheckpoint.world_id == world_id, WorldCheckpoint.id.in_(heads)))
    verified = set()
    for point in checkpoints:
        if point.content_hash != checkpoint_hash(point.state, point.definition, point.text, point.parent_checkpoint_id):
            raise Problem("WORLD_CHECKPOINT_INVALID", "存档校验未通过，当前世界不会前进", 409)
        verified.add(point.id)
    if verified != heads:
        raise Problem("RESOURCE_NOT_FOUND", "故事路径不完整或不可访问", 404)
    reachable = {}; depths = {}
    for line in lines:
        current = line.head_checkpoint_id; chain = []; seen = set()
        while current is not None and current not in depths:
            if current not in records:
                raise Problem("RESOURCE_NOT_FOUND", "故事路径不完整或不可访问", 404)
            if current in seen or len(chain) >= MAX_PATH:
                raise Problem("WORLD_HISTORY_INVALID", "世界历史无法完整恢复", 409)
            seen.add(current); chain.append(current); current = records[current].parent_checkpoint_id
        depth = depths.get(current, 0)
        for node in reversed(chain):
            depth += 1
            if depth > MAX_PATH:
                raise Problem("WORLD_HISTORY_INVALID", "世界历史无法完整恢复", 409)
            depths[node] = depth
        for node in chain:
            reachable.setdefault(node, records[node])
    nodes = []
    for point in reachable.values():
        state, definition = point.state, point.definition
        metadata = public_payload({"title": point.title, "action": point.action,
            "timeLabel": state["timeLabel"], "location": next((entity["name"] for entity in definition["entities"]
                if entity["id"] == state["location"]), "")}, state, definition)
        nodes.append({"id": point.id, "worldId": world.id, "lineId": point.line_id,
            "parentCheckpointId": point.parent_checkpoint_id, "sequence": point.sequence,
            **metadata, "text": "", "summary": "", "options": [], "changes": [],
            "kind": point.kind, "createdAt": point.created_at.isoformat()})
    return {"lines": [{"id": line.id, "name": line.name, "headCheckpointId": line.head_checkpoint_id,
        "status": line.status, "version": line.version, "camera": line.camera} for line in lines], "nodes": nodes}
